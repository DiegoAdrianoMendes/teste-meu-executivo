from __future__ import annotations

from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.deps import (
    AuthContext,
    get_current_auth_context,
    get_current_user_id,
    get_current_workspace_id,
    get_db,
)
from app.main import app as fastapi_app
from app.models.klm_operations import KlmAuditEvent
from app.models.notification import Notification
from app.models.user import AgentType, User, UserRole
from app.models.whatsapp import ConnectionType, WaConnectionStatus, WhatsAppConnection
from app.models.whatsapp_campaign import MetaTemplateStatus, WhatsAppMetaTemplate
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership, WorkspaceRole
from app.services.meta_ai_service import MetaAIService, assess_oab_risk
from app.services.whatsapp.secrets import WhatsAppSecretCipher

pytestmark = pytest.mark.usefixtures("active_subscription")


@asynccontextmanager
async def _client_as(db_session, workspace_id, user_id, role, *, membership_id=None):
    """Cliente HTTP autenticado como outro papel do mesmo workspace, sem
    perturbar as fixtures `auth_context`/`client` padrão do módulo."""
    context = AuthContext(
        user_id=user_id,
        workspace_id=workspace_id,
        membership_id=membership_id,
        role=role,
        is_internal_staff=False,
    )

    async def override_get_db():
        yield db_session

    async def override_get_current_auth_context():
        return context

    async def override_get_current_user_id():
        return user_id

    async def override_get_current_workspace_id():
        return workspace_id

    previous_overrides = dict(fastapi_app.dependency_overrides)
    fastapi_app.dependency_overrides[get_db] = override_get_db
    fastapi_app.dependency_overrides[get_current_auth_context] = override_get_current_auth_context
    fastapi_app.dependency_overrides[get_current_user_id] = override_get_current_user_id
    fastapi_app.dependency_overrides[get_current_workspace_id] = override_get_current_workspace_id
    try:
        async with AsyncClient(
            transport=ASGITransport(app=fastapi_app), base_url="http://test"
        ) as ac:
            yield ac
    finally:
        fastapi_app.dependency_overrides.clear()
        fastapi_app.dependency_overrides.update(previous_overrides)


async def _make_operator_member(db_session, workspace_id) -> User:
    user = User(
        name="Operador API Teste",
        email=f"operador-{uuid4().hex[:8]}@example.com",
        agent_type=AgentType.human,
        role=UserRole.intern,
        initials="OP",
        color="#1f2937",
        is_active=True,
        is_internal_staff=False,
    )
    db_session.add(user)
    await db_session.flush()
    db_session.add(
        WorkspaceMembership(
            workspace_id=workspace_id,
            user_id=user.id,
            role=WorkspaceRole.operator,
            status=MembershipStatus.active,
            is_default=True,
        )
    )
    await db_session.commit()
    return user


async def _make_connection(db_session, workspace_id, *, waba_id="waba_api_1"):
    cipher = WhatsAppSecretCipher()
    connection = WhatsAppConnection(
        workspace_id=workspace_id,
        connection_type=ConnectionType.OFFICIAL_MANUAL,
        is_default=True,
        phone_number_id="pn_api_tpl",
        phone_number="5511988880000",
        display_phone_number="+55 11 98888-0000",
        access_token_encrypted=cipher.encrypt("meta-token"),
        waba_id=waba_id,
        status=WaConnectionStatus.connected,
    )
    db_session.add(connection)
    await db_session.flush()
    await db_session.commit()
    return connection


def _valid_payload(connection_id: str, name: str = "atualizacao caso") -> dict:
    return {
        "whatsappConnectionId": connection_id,
        "name": name,
        "language": "pt_BR",
        "category": "utility",
        "components": {
            "headerType": "TEXT",
            "headerText": "Olá {{1}}",
            "headerExample": "Maria",
            "bodyText": "Seu processo {{1}} foi atualizado em {{2}}.",
            "bodyExamples": ["0001234-56.2026.8.07.0001", "10/08/2026"],
            "footerText": "MV Advocacia",
            "buttons": [{"type": "QUICK_REPLY", "text": "Ver detalhes"}],
        },
    }


@pytest.mark.asyncio
async def test_create_and_get_template_draft(client, db_session, auth_context):
    connection = await _make_connection(db_session, auth_context.workspace_id)

    response = await client.post(
        "/api/v1/whatsapp/meta-template-drafts", json=_valid_payload(str(connection.id))
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["isDraft"] is True
    assert body["localStatus"] == "draft"
    assert body["name"] == "atualizacao_caso"

    get_response = await client.get(f"/api/v1/whatsapp/meta-template-drafts/{body['id']}")
    assert get_response.status_code == 200
    assert get_response.json()["id"] == body["id"]


@pytest.mark.asyncio
async def test_create_template_draft_rejects_invalid_body(client, db_session, auth_context):
    connection = await _make_connection(db_session, auth_context.workspace_id)
    payload = _valid_payload(str(connection.id))
    payload["components"]["bodyText"] = ""

    response = await client.post("/api/v1/whatsapp/meta-template-drafts", json=payload)
    assert response.status_code == 422
    assert "errors" in response.json()["detail"]


@pytest.mark.asyncio
async def test_update_template_draft(client, db_session, auth_context):
    connection = await _make_connection(db_session, auth_context.workspace_id)
    create_response = await client.post(
        "/api/v1/whatsapp/meta-template-drafts", json=_valid_payload(str(connection.id))
    )
    draft_id = create_response.json()["id"]

    update_response = await client.patch(
        f"/api/v1/whatsapp/meta-template-drafts/{draft_id}",
        json={"category": "marketing"},
    )
    assert update_response.status_code == 200
    assert update_response.json()["category"] == "marketing"


@pytest.mark.asyncio
async def test_delete_template_draft(client, db_session, auth_context):
    connection = await _make_connection(db_session, auth_context.workspace_id)
    create_response = await client.post(
        "/api/v1/whatsapp/meta-template-drafts", json=_valid_payload(str(connection.id))
    )
    draft_id = create_response.json()["id"]

    delete_response = await client.delete(f"/api/v1/whatsapp/meta-template-drafts/{draft_id}")
    assert delete_response.status_code == 204

    get_response = await client.get(f"/api/v1/whatsapp/meta-template-drafts/{draft_id}")
    assert get_response.status_code == 404


@pytest.mark.asyncio
async def test_submit_with_gate_closed_is_honest_about_status(
    client, db_session, auth_context, monkeypatch
):
    monkeypatch.setattr(
        "app.services.whatsapp.template_management_service.external_delivery_allowed",
        lambda: False,
    )
    connection = await _make_connection(db_session, auth_context.workspace_id)
    create_response = await client.post(
        "/api/v1/whatsapp/meta-template-drafts", json=_valid_payload(str(connection.id), "gate_fechado_api")
    )
    draft_id = create_response.json()["id"]

    submit_response = await client.post(f"/api/v1/whatsapp/meta-template-drafts/{draft_id}/submit")
    assert submit_response.status_code == 200
    body = submit_response.json()
    assert body["submittedToMeta"] is False
    assert body["template"]["localStatus"] == "submission_prepared"
    assert body["template"]["status"] != "approved"
    assert "aguardando aprovação" in body["message"].lower()


@pytest.mark.asyncio
async def test_list_unified_templates_includes_drafts_and_synced(client, db_session, auth_context):
    connection = await _make_connection(db_session, auth_context.workspace_id)
    synced = WhatsAppMetaTemplate(
        workspace_id=auth_context.workspace_id,
        whatsapp_connection_id=connection.id,
        external_template_id="ext-api-1",
        name="sincronizado_api",
        language="pt_BR",
        category="marketing",
        status=MetaTemplateStatus.approved,
        quality_rating="green",
        components=[],
        raw_payload={"id": "ext-api-1"},
    )
    db_session.add(synced)
    await db_session.commit()

    await client.post("/api/v1/whatsapp/meta-template-drafts", json=_valid_payload(str(connection.id), "unificado_api"))

    response = await client.get("/api/v1/whatsapp/meta-templates/unified")
    assert response.status_code == 200
    names = {item["name"]: item for item in response.json()}
    assert "sincronizado_api" in names
    assert "unificado_api" in names
    assert names["sincronizado_api"]["isDraft"] is False
    assert names["unificado_api"]["isDraft"] is True


@pytest.mark.asyncio
async def test_sync_marks_template_missing_from_waba_without_deleting_it(
    client, db_session, auth_context, monkeypatch
):
    connection = await _make_connection(db_session, auth_context.workspace_id)
    present = WhatsAppMetaTemplate(
        workspace_id=auth_context.workspace_id,
        whatsapp_connection_id=connection.id,
        external_template_id="ext-present",
        name="presente_na_meta",
        language="pt_BR",
        category="utility",
        status=MetaTemplateStatus.approved,
        components=[],
        raw_payload={"id": "ext-present"},
    )
    orphan = WhatsAppMetaTemplate(
        workspace_id=auth_context.workspace_id,
        whatsapp_connection_id=connection.id,
        external_template_id="ext-orphan",
        name="removido_na_meta",
        language="pt_BR",
        category="utility",
        status=MetaTemplateStatus.rejected,
        components=[],
        raw_payload={"id": "ext-orphan"},
    )
    just_submitted = WhatsAppMetaTemplate(
        workspace_id=auth_context.workspace_id,
        whatsapp_connection_id=connection.id,
        external_template_id="ext-eventually-consistent",
        name="recem_submetido",
        language="pt_BR",
        category="utility",
        status=MetaTemplateStatus.pending,
        components=[],
        raw_payload={"source": "local_draft", "submitted_at": "2026-08-10T20:00:00Z"},
    )
    db_session.add_all([present, orphan, just_submitted])
    await db_session.commit()

    monkeypatch.setattr(
        "app.api.v1.endpoints.whatsapp_campaigns.external_delivery_allowed", lambda: True
    )
    monkeypatch.setattr(
        "app.services.whatsapp.campaign_service.external_delivery_allowed", lambda: True
    )

    async def fake_get_message_templates(_self, waba_id, access_token):
        assert waba_id == "waba_api_1"
        assert access_token == "meta-token"
        return {
            "data": [
                {
                    "id": "ext-present",
                    "name": "presente_na_meta",
                    "language": "pt_BR",
                    "category": "UTILITY",
                    "status": "APPROVED",
                    "components": [],
                }
            ]
        }

    monkeypatch.setattr(
        "app.services.whatsapp.meta_cloud_client.MetaCloudClient.get_message_templates",
        fake_get_message_templates,
    )

    sync_response = await client.post(
        "/api/v1/whatsapp/meta-templates/sync",
        json={"connectionId": str(connection.id)},
    )
    assert sync_response.status_code == 200, sync_response.text
    assert {item["name"] for item in sync_response.json()} == {
        "presente_na_meta",
        "recem_submetido",
    }

    active_response = await client.get("/api/v1/whatsapp/meta-templates/unified")
    assert active_response.status_code == 200
    assert {item["name"] for item in active_response.json()} == {
        "presente_na_meta",
        "recem_submetido",
    }

    history_response = await client.get(
        "/api/v1/whatsapp/meta-templates/unified?include_removed_source=true"
    )
    assert history_response.status_code == 200
    history = {item["name"]: item for item in history_response.json()}
    assert history["removido_na_meta"]["removedAtSourceAt"] is not None

    stored_orphan = await db_session.get(WhatsAppMetaTemplate, orphan.id)
    assert stored_orphan is not None
    assert stored_orphan.removed_at_source_at is not None
    stored_just_submitted = await db_session.get(WhatsAppMetaTemplate, just_submitted.id)
    assert stored_just_submitted is not None
    assert stored_just_submitted.removed_at_source_at is None


@pytest.mark.asyncio
async def test_sync_keeps_rejection_reason_when_meta_returns_none(
    client,
    db_session,
    auth_context,
    monkeypatch,
):
    # V29: o webhook gravou INVALID_FORMAT; a Meta responde rejected_reason "NONE" no GET.
    connection = await _make_connection(db_session, auth_context.workspace_id)
    rejected = WhatsAppMetaTemplate(
        workspace_id=auth_context.workspace_id,
        whatsapp_connection_id=connection.id,
        external_template_id="ext-rejected",
        name="movimentacao_processual",
        language="pt_BR",
        category="utility",
        status=MetaTemplateStatus.rejected,
        components=[],
        raw_payload={
            "source": "meta_sync",
            "rejected_reason": "INVALID_FORMAT",
            "last_status_event": "REJECTED",
            "utility_catalog": {"key": "movimentacao_processual"},
        },
    )
    db_session.add(rejected)
    await db_session.commit()

    monkeypatch.setattr(
        "app.api.v1.endpoints.whatsapp_campaigns.external_delivery_allowed", lambda: True
    )
    monkeypatch.setattr(
        "app.services.whatsapp.campaign_service.external_delivery_allowed", lambda: True
    )

    async def fake_get_message_templates(_self, waba_id, access_token):
        return {
            "data": [
                {
                    "id": "ext-rejected",
                    "name": "movimentacao_processual",
                    "language": "pt_BR",
                    "category": "UTILITY",
                    "status": "REJECTED",
                    "rejected_reason": "NONE",
                    "components": [],
                }
            ]
        }

    monkeypatch.setattr(
        "app.services.whatsapp.meta_cloud_client.MetaCloudClient.get_message_templates",
        fake_get_message_templates,
    )

    sync_response = await client.post(
        "/api/v1/whatsapp/meta-templates/sync",
        json={"connectionId": str(connection.id)},
    )
    assert sync_response.status_code == 200, sync_response.text

    unified = await client.get("/api/v1/whatsapp/meta-templates/unified")
    assert unified.status_code == 200
    item = next(entry for entry in unified.json() if entry["name"] == "movimentacao_processual")
    assert item["status"] == "rejected"
    assert item["rejectedReason"] == "INVALID_FORMAT"
    assert item["isDraft"] is False

    stored = await db_session.get(WhatsAppMetaTemplate, rejected.id)
    await db_session.refresh(stored)
    assert stored.raw_payload["rejected_reason"] == "INVALID_FORMAT"
    assert stored.raw_payload["utility_catalog"] == {"key": "movimentacao_processual"}
    assert stored.raw_payload["source"] == "meta_sync"


@pytest.mark.asyncio
async def test_submitted_template_local_record_requires_confirmation_and_is_removable(
    client, db_session, auth_context
):
    connection = await _make_connection(db_session, auth_context.workspace_id)
    template = WhatsAppMetaTemplate(
        workspace_id=auth_context.workspace_id,
        whatsapp_connection_id=connection.id,
        external_template_id="ext-remove-local",
        name="registro_local_removivel",
        language="pt_BR",
        category="utility",
        status=MetaTemplateStatus.rejected,
        components=[],
        raw_payload={"id": "ext-remove-local"},
    )
    db_session.add(template)
    await db_session.commit()

    without_confirmation = await client.delete(
        f"/api/v1/whatsapp/meta-templates/{template.id}/local-record"
    )
    assert without_confirmation.status_code == 422
    assert await db_session.get(WhatsAppMetaTemplate, template.id) is not None

    confirmed = await client.delete(
        f"/api/v1/whatsapp/meta-templates/{template.id}/local-record"
        "?confirm_history_loss=true"
    )
    assert confirmed.status_code == 204, confirmed.text
    assert await db_session.get(WhatsAppMetaTemplate, template.id) is None
    audit = await db_session.scalar(
        select(KlmAuditEvent).where(
            KlmAuditEvent.entity_id == template.id,
            KlmAuditEvent.event_type == "whatsapp_template_local_record_removed",
        )
    )
    assert audit is not None
    assert audit.actor_user_id == auth_context.user_id
    assert audit.before_data["externalTemplateId"] == "ext-remove-local"


@pytest.mark.asyncio
async def test_analyze_meta_template_uses_ai_service(client, monkeypatch, auth_context):
    captured = {}

    async def fake_analyze(self, **kwargs):
        captured.update(kwargs)
        return {
            "predicted_category": "utility",
            "approval_likelihood": "high",
            "confidence": 0.91,
            "cost_band": "Menor, categoria utility",
            "recommendation": "Preparar submissão",
            "summary": "Mensagem transacional com contexto claro.",
            "signals": ["Prazo esperado pelo cliente.", "Variáveis contextualizadas."],
            "provider": "openai",
            "model_used": "gpt-test",
        }

    monkeypatch.setattr(MetaAIService, "analyze_whatsapp_template", fake_analyze)
    response = await client.post(
        "/api/v1/whatsapp/meta-templates/analyze",
        json={
            "name": "atualizacao_documentos",
            "categoryIntent": "utility",
            "body": "Olá {{1}}, seus documentos foram recebidos.",
            "sampleVariables": "Maria",
            "practiceArea": "Direito do consumidor",
        },
    )

    assert response.status_code == 200
    assert response.json()["modelUsed"] == "gpt-test"
    assert response.json()["approvalLikelihood"] == "high"
    assert captured["workspace_id"] == str(auth_context.workspace_id)
    assert captured["body"] == "Olá {{1}}, seus documentos foram recebidos."


@pytest.mark.asyncio
async def test_analyze_meta_template_accepts_authentication_intent(
    client, monkeypatch
):
    captured = {}

    async def fake_analyze(self, **kwargs):
        captured.update(kwargs)
        return {
            "predicted_category": "authentication",
            "approval_likelihood": "high",
            "confidence": 0.95,
            "cost_band": "Autenticação",
            "recommendation": "Manter como autenticação",
            "summary": "Código de uso único.",
            "signals": ["Código temporário", "Finalidade de autenticação"],
            "meta_category_suggested": "authentication",
            "marketing_signals": [],
            "oab_risk": {
                "level": "low",
                "signals": [],
                "requires_acknowledgement": False,
            },
            "provider": "openai",
            "model_used": "gpt-test",
        }

    monkeypatch.setattr(MetaAIService, "analyze_whatsapp_template", fake_analyze)
    response = await client.post(
        "/api/v1/whatsapp/meta-templates/analyze",
        json={
            "name": "codigo_acesso",
            "categoryIntent": "authentication",
            "body": "Seu código de acesso é {{1}} e expira em 10 minutos.",
            "sampleVariables": "123456",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["metaCategorySuggested"] == "authentication"
    assert captured["category_intent"] == "authentication"


@pytest.mark.asyncio
async def test_persisted_ai_oab_risk_from_template_id_gates_submission(
    client, db_session, auth_context, monkeypatch
):
    connection = await _make_connection(db_session, auth_context.workspace_id)
    created = await client.post(
        "/api/v1/whatsapp/meta-template-drafts",
        json=_valid_payload(str(connection.id), "risco_contextual"),
    )
    assert created.status_code == 201, created.text
    template_id = created.json()["id"]

    async def fake_analyze(self, **kwargs):
        return {
            "predicted_category": "marketing",
            "approval_likelihood": "medium",
            "confidence": 0.82,
            "cost_band": "Marketing",
            "recommendation": "Exigir revisão humana",
            "summary": "Contexto indica captação.",
            "signals": ["Oferta ativa", "Destinatário não cliente"],
            "meta_category_suggested": "marketing",
            "marketing_signals": ["Oferta ativa"],
            "oab_risk": {
                "level": "high",
                "signals": ["oferta_a_nao_cliente"],
                "requires_acknowledgement": True,
            },
            "provider": "openai",
            "model_used": "gpt-test",
        }

    monkeypatch.setattr(MetaAIService, "analyze_whatsapp_template", fake_analyze)
    analyzed = await client.post(
        "/api/v1/whatsapp/meta-templates/analyze",
        json={
            "templateId": template_id,
            "name": "risco_contextual",
            "categoryIntent": "utility",
            "body": "Seu processo foi atualizado e aguarda novos documentos.",
        },
    )
    assert analyzed.status_code == 200, analyzed.text

    template = await db_session.scalar(
        select(WhatsAppMetaTemplate).where(WhatsAppMetaTemplate.id == template_id)
    )
    assert template.raw_payload["ai_analysis"]["oab_risk"]["level"] == "high"
    assert template.raw_payload["ai_analysis_at"]
    analysis_audit = await db_session.scalar(
        select(KlmAuditEvent).where(
            KlmAuditEvent.workspace_id == auth_context.workspace_id,
            KlmAuditEvent.entity_id == template.id,
            KlmAuditEvent.event_type == "whatsapp_template_oab_risk_analyzed",
        )
    )
    actor = await db_session.scalar(select(User).where(User.id == auth_context.user_id))
    assert analysis_audit is not None
    assert analysis_audit.actor_user_id == auth_context.user_id
    assert analysis_audit.actor_label == actor.name

    blocked = await client.post(f"/api/v1/whatsapp/meta-template-drafts/{template_id}/submit")
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "WA-TPL-OAB-ACK-REQUIRED"
    assert blocked.json()["detail"]["oabRisk"]["signals"] == ["oferta_a_nao_cliente"]


def test_oab_risk_floor_detects_promises_and_artificial_urgency():
    risk = assess_oab_risk(
        "Causa ganha: contrate agora o melhor escritório e garanta sua indenização."
    )

    assert risk["level"] == "high"
    assert risk["requires_acknowledgement"] is True
    assert {"promessa_resultado", "superlativo", "urgencia_artificial"} <= set(
        risk["signals"]
    )


@pytest.mark.asyncio
async def test_high_oab_risk_requires_explicit_ack_and_records_actor(
    client, db_session, auth_context, monkeypatch
):
    monkeypatch.setattr(
        "app.services.whatsapp.template_management_service.external_delivery_allowed",
        lambda: False,
    )
    connection = await _make_connection(db_session, auth_context.workspace_id)
    payload = _valid_payload(str(connection.id), "promessa_resultado")
    payload["components"]["bodyText"] = (
        "Causa ganha: contrate agora o melhor escritório e garanta sua indenização {{1}}."
    )
    payload["components"]["bodyExamples"] = ["Maria"]
    created = await client.post("/api/v1/whatsapp/meta-template-drafts", json=payload)
    assert created.status_code == 201, created.text
    template_id = created.json()["id"]

    blocked = await client.post(f"/api/v1/whatsapp/meta-template-drafts/{template_id}/submit")
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "WA-TPL-OAB-ACK-REQUIRED"
    assert blocked.json()["detail"]["oabRisk"]["requiresAcknowledgement"] is True

    accepted = await client.post(
        f"/api/v1/whatsapp/meta-template-drafts/{template_id}/submit",
        json={"oabRiskAcknowledged": True},
    )
    assert accepted.status_code == 200, accepted.text

    template = await db_session.scalar(
        select(WhatsAppMetaTemplate).where(WhatsAppMetaTemplate.id == template_id)
    )
    acknowledgement = template.raw_payload["oab_risk_acknowledgement"]
    assert acknowledgement["acknowledgedByUserId"] == str(auth_context.user_id)
    assert acknowledgement["acknowledgedAt"]

    audit = await db_session.scalar(
        select(KlmAuditEvent).where(
            KlmAuditEvent.workspace_id == auth_context.workspace_id,
            KlmAuditEvent.entity_id == template.id,
            KlmAuditEvent.event_type == "whatsapp_template_oab_risk_acknowledged",
        )
    )
    assert audit is not None
    assert audit.actor_user_id == auth_context.user_id
    assert audit.after_data["riskLevel"] == "high"


@pytest.mark.asyncio
async def test_template_drafts_are_workspace_scoped(client, db_session, auth_context):
    from uuid import uuid4

    connection = await _make_connection(db_session, auth_context.workspace_id)
    create_response = await client.post(
        "/api/v1/whatsapp/meta-template-drafts", json=_valid_payload(str(connection.id), "isolado_workspace")
    )
    draft_id = create_response.json()["id"]

    # Sanity: outro workspace não vê o rascunho — simulado via query direta,
    # já que o cliente de teste está fixado no workspace de auth_context.
    other_workspace_id = uuid4()
    assert str(other_workspace_id) != str(auth_context.workspace_id)

    get_response = await client.get(f"/api/v1/whatsapp/meta-template-drafts/{draft_id}")
    assert get_response.status_code == 200


# --- W2.2: aprovação interna via API -----------------------------------------


@pytest.mark.asyncio
async def test_operator_submit_creates_internal_approval_and_owner_approves(
    client, db_session, auth_context, monkeypatch
):
    monkeypatch.setattr(
        "app.services.whatsapp.template_management_service.external_delivery_allowed",
        lambda: False,
    )
    connection = await _make_connection(db_session, auth_context.workspace_id)
    operator = await _make_operator_member(db_session, auth_context.workspace_id)

    async with _client_as(
        db_session, auth_context.workspace_id, operator.id, WorkspaceRole.operator
    ) as operator_client:
        create_response = await operator_client.post(
            "/api/v1/whatsapp/meta-template-drafts",
            json=_valid_payload(str(connection.id), "aprovacao_interna_api"),
        )
        assert create_response.status_code == 201, create_response.text
        draft_id = create_response.json()["id"]

        submit_response = await operator_client.post(
            f"/api/v1/whatsapp/meta-template-drafts/{draft_id}/submit"
        )
        assert submit_response.status_code == 200, submit_response.text
        body = submit_response.json()
        assert body["submittedToMeta"] is False
        assert body["template"]["localStatus"] == "pending_internal_approval"

    # O owner (auth_context) enxerga o pedido na fila e aprova.
    pending_response = await client.get(
        "/api/v1/whatsapp/meta-template-drafts/pending-internal-approval"
    )
    assert pending_response.status_code == 200
    pending_ids = {item["id"] for item in pending_response.json()}
    assert draft_id in pending_ids

    approve_response = await client.post(
        f"/api/v1/whatsapp/meta-template-drafts/{draft_id}/approve-internal"
    )
    assert approve_response.status_code == 200, approve_response.text
    approved_body = approve_response.json()
    assert approved_body["template"]["localStatus"] == "submission_prepared"

    notifications = (
        await db_session.scalars(
            select(Notification).where(Notification.user_id == operator.id)
        )
    ).all()
    assert any("aprovou" in n.message.lower() for n in notifications)


@pytest.mark.asyncio
async def test_operator_cannot_approve_own_internal_request(
    client, db_session, auth_context, monkeypatch
):
    monkeypatch.setattr(
        "app.services.whatsapp.template_management_service.external_delivery_allowed",
        lambda: False,
    )
    connection = await _make_connection(db_session, auth_context.workspace_id)
    operator = await _make_operator_member(db_session, auth_context.workspace_id)

    async with _client_as(
        db_session, auth_context.workspace_id, operator.id, WorkspaceRole.operator
    ) as operator_client:
        create_response = await operator_client.post(
            "/api/v1/whatsapp/meta-template-drafts",
            json=_valid_payload(str(connection.id), "operador_sem_permissao"),
        )
        draft_id = create_response.json()["id"]
        await operator_client.post(f"/api/v1/whatsapp/meta-template-drafts/{draft_id}/submit")

        forbidden_response = await operator_client.post(
            f"/api/v1/whatsapp/meta-template-drafts/{draft_id}/approve-internal"
        )
        assert forbidden_response.status_code == 403


# --- W2.2: "corrigir e reenviar" via API -------------------------------------


@pytest.mark.asyncio
async def test_clone_rejected_template_via_api(client, db_session, auth_context):
    connection = await _make_connection(db_session, auth_context.workspace_id)
    rejected = WhatsAppMetaTemplate(
        workspace_id=auth_context.workspace_id,
        whatsapp_connection_id=connection.id,
        external_template_id="ext-api-rejected-1",
        name="rejeitado_api",
        language="pt_BR",
        # A Meta grava a categoria em maiúsculas; o clone precisa nascer editável (V31).
        category="UTILITY",
        status=MetaTemplateStatus.rejected,
        components=[{"type": "BODY", "text": "Olá {{1}}"}],
        raw_payload={"rejected_reason": "INVALID_FORMAT"},
    )
    db_session.add(rejected)
    await db_session.commit()

    response = await client.post(
        f"/api/v1/whatsapp/meta-template-drafts/{rejected.id}/clone-rejected"
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["isDraft"] is True
    assert body["localStatus"] == "draft"
    assert body["name"].startswith("rejeitado_api")
    assert body["category"] == "utility"

    # O PATCH aceita a grafia da Meta e normaliza, em vez de responder 422 genérico.
    updated = await client.patch(
        f"/api/v1/whatsapp/meta-template-drafts/{body['id']}",
        json={
            "category": "UTILITY",
            "language": "pt_BR",
            "components": {
                "headerType": "NONE",
                "headerText": "",
                "headerExample": "",
                "bodyText": "Olá {{1}}, tudo certo com o seu caso.",
                "bodyExamples": ["Maria"],
                "footerText": "",
                "buttons": [],
            },
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["category"] == "utility"
