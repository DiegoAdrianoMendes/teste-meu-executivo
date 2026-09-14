from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.klm_operations import KlmAuditEvent
from app.models.notification import Notification
from app.models.whatsapp import ConnectionType, WaConnectionStatus, WhatsAppConnection
from app.models.workspace_membership import WorkspaceRole
from app.schemas.whatsapp_campaign import (
    TemplateButtonIn,
    TemplateComponentsIn,
    TemplateDraftCreate,
    TemplateDraftUpdate,
)
from app.services.whatsapp.connection_service import WhatsAppConnectionService
from app.services.whatsapp.meta_cloud_client import MetaCloudClient
from app.services.whatsapp.secrets import WhatsAppSecretCipher
from app.services.whatsapp.template_management_service import (
    TemplateManagementService,
    _build_meta_components,
    normalize_template_name,
    validate_template_draft,
)


async def _make_connection(db_session, workspace_id, *, waba_id="waba_1"):
    cipher = WhatsAppSecretCipher()
    connection = WhatsAppConnection(
        workspace_id=workspace_id,
        connection_type=ConnectionType.OFFICIAL_MANUAL,
        is_default=True,
        phone_number_id="pn_tpl_1",
        phone_number="5511999990000",
        display_phone_number="+55 11 99999-0000",
        access_token_encrypted=cipher.encrypt("meta-token"),
        waba_id=waba_id,
        status=WaConnectionStatus.connected,
    )
    db_session.add(connection)
    await db_session.flush()
    return connection


def _make_service(db_session) -> TemplateManagementService:
    cipher = WhatsAppSecretCipher()
    connection_service = WhatsAppConnectionService(db_session, cipher=cipher, meta_client=MetaCloudClient())
    return TemplateManagementService(
        db_session,
        connection_service=connection_service,
        meta_client=MetaCloudClient(),
    )


def _valid_components(**overrides) -> TemplateComponentsIn:
    defaults = dict(
        header_type="TEXT",
        header_text="Olá {{1}}",
        header_example="Kleist",
        body_text="Seu processo {{1}} teve atualização em {{2}}.",
        body_examples=["0001234-56.2026.8.07.0001", "10/08/2026"],
        footer_text="MV Advocacia",
        buttons=[TemplateButtonIn(type="QUICK_REPLY", text="Ver detalhes")],
    )
    defaults.update(overrides)
    return TemplateComponentsIn(**defaults)


# --- normalização de nome ----------------------------------------------------

def test_normalize_template_name_lowercases_and_strips_accents():
    assert normalize_template_name("Atualização Processo!") == "atualizacao_processo"


def test_normalize_template_name_collapses_separators():
    assert normalize_template_name("  meu -- template  ") == "meu_template"


# --- validação de placeholders/limites --------------------------------------

def test_validate_template_draft_accepts_valid_draft():
    errors = validate_template_draft(
        name="atualizacao_processo",
        language="pt_BR",
        category="utility",
        components=_valid_components(),
    )
    assert errors == []


def test_meta_payload_includes_required_variable_examples():
    components = _build_meta_components(_valid_components())
    assert components[0]["example"] == {"header_text": ["Kleist"]}
    assert components[1]["example"] == {
        "body_text": [["0001234-56.2026.8.07.0001", "10/08/2026"]]
    }


def test_validate_template_draft_rejects_missing_variable_examples():
    components = _valid_components(header_example=None, body_examples=[])
    errors = validate_template_draft(
        name="t", language="pt_BR", category="utility", components=components
    )
    assert any("exemplo" in error.lower() for error in errors)


def test_validate_template_draft_rejects_gap_in_placeholders():
    components = _valid_components(body_text="Olá {{1}}, seu prazo é {{3}}.")
    errors = validate_template_draft(
        name="t", language="pt_BR", category="utility", components=components
    )
    assert any("sequenciais" in error for error in errors)


def test_validate_template_draft_rejects_body_over_limit():
    components = _valid_components(body_text="x" * 1025)
    errors = validate_template_draft(
        name="t", language="pt_BR", category="utility", components=components
    )
    assert any("1024" in error for error in errors)


def test_validate_template_draft_rejects_empty_body():
    components = _valid_components(body_text="   ")
    errors = validate_template_draft(
        name="t", language="pt_BR", category="utility", components=components
    )
    assert any("corpo" in error.lower() for error in errors)


def test_validate_template_draft_rejects_invalid_category():
    errors = validate_template_draft(
        name="t", language="pt_BR", category="promo", components=_valid_components()
    )
    assert any("Categoria" in error for error in errors)


def test_validate_template_draft_rejects_placeholder_in_static_url_button():
    components = _valid_components(
        buttons=[TemplateButtonIn(type="URL", text="Acesse", url="https://mv.adv.br/{{1}}/extra")]
    )
    # placeholder não está no final -> deve reprovar a URL dinâmica malformada
    errors = validate_template_draft(
        name="t", language="pt_BR", category="utility", components=components
    )
    assert any("variável de URL dinâmica" in error for error in errors)


def test_validate_template_draft_accepts_dynamic_url_placeholder_at_end():
    components = _valid_components(
        buttons=[TemplateButtonIn(type="URL", text="Acesse", url="https://mv.adv.br/caso/{{1}}")]
    )
    errors = validate_template_draft(
        name="t", language="pt_BR", category="utility", components=components
    )
    assert errors == []


def test_validate_template_draft_rejects_too_many_url_buttons():
    components = _valid_components(
        buttons=[
            TemplateButtonIn(type="URL", text="Um", url="https://a.com"),
            TemplateButtonIn(type="URL", text="Dois", url="https://b.com"),
            TemplateButtonIn(type="URL", text="Três", url="https://c.com"),
        ]
    )
    errors = validate_template_draft(
        name="t", language="pt_BR", category="utility", components=components
    )
    assert any("no máximo 2" in error.lower() for error in errors)


def test_validate_template_draft_rejects_placeholder_in_footer():
    components = _valid_components(footer_text="Prazo {{1}}")
    errors = validate_template_draft(
        name="t", language="pt_BR", category="utility", components=components
    )
    assert any("rodapé" in error.lower() for error in errors)


# --- service: criar / editar / submeter rascunho ----------------------------

@pytest.mark.asyncio
async def test_create_draft_persists_row_without_migration_fields(db_session, auth_context):
    connection = await _make_connection(db_session, auth_context.workspace_id)
    service = _make_service(db_session)

    draft = await service.create_draft(
        auth_context.workspace_id,
        TemplateDraftCreate(
            whatsapp_connection_id=connection.id,
            name="Atualização de Caso",
            language="pt_BR",
            category="utility",
            components=_valid_components(),
        ),
    )

    assert draft.name == "atualizacao_de_caso"
    assert draft.is_draft is True
    assert draft.local_status == "draft"
    assert draft.external_template_id is None
    # status local nunca finge aprovação da Meta
    assert draft.status.value == "pending"


@pytest.mark.asyncio
async def test_create_draft_rejects_invalid_components(db_session, auth_context):
    connection = await _make_connection(db_session, auth_context.workspace_id)
    service = _make_service(db_session)

    with pytest.raises(HTTPException) as excinfo:
        await service.create_draft(
            auth_context.workspace_id,
            TemplateDraftCreate(
                whatsapp_connection_id=connection.id,
                name="template invalido",
                language="pt_BR",
                category="utility",
                components=_valid_components(body_text=""),
            ),
        )
    assert excinfo.value.status_code == 422


@pytest.mark.asyncio
async def test_create_draft_rejects_duplicate_name_language(db_session, auth_context):
    connection = await _make_connection(db_session, auth_context.workspace_id)
    service = _make_service(db_session)
    payload = TemplateDraftCreate(
        whatsapp_connection_id=connection.id,
        name="duplicado",
        language="pt_BR",
        category="utility",
        components=_valid_components(),
    )
    await service.create_draft(auth_context.workspace_id, payload)

    with pytest.raises(HTTPException) as excinfo:
        await service.create_draft(auth_context.workspace_id, payload)
    assert excinfo.value.status_code == 409


@pytest.mark.asyncio
async def test_update_draft_allowed_only_before_submission(db_session, auth_context):
    connection = await _make_connection(db_session, auth_context.workspace_id)
    service = _make_service(db_session)
    draft = await service.create_draft(
        auth_context.workspace_id,
        TemplateDraftCreate(
            whatsapp_connection_id=connection.id,
            name="editavel",
            language="pt_BR",
            category="utility",
            components=_valid_components(),
        ),
    )

    updated = await service.update_draft(
        auth_context.workspace_id,
        draft.id,
        TemplateDraftUpdate(category="marketing"),
    )
    assert updated.category == "marketing"


@pytest.mark.asyncio
async def test_material_update_invalidates_saved_analysis_and_oab_ack(
    db_session, auth_context
):
    connection = await _make_connection(db_session, auth_context.workspace_id)
    service = _make_service(db_session)
    draft = await service.create_draft(
        auth_context.workspace_id,
        TemplateDraftCreate(
            whatsapp_connection_id=connection.id,
            name="analise_invalida_apos_edicao",
            language="pt_BR",
            category="utility",
            components=_valid_components(),
        ),
    )
    template = await service._get_draft(auth_context.workspace_id, draft.id)
    template.raw_payload = {
        **template.raw_payload,
        "ai_analysis": {"oab_risk": {"level": "high", "signals": ["captacao"]}},
        "ai_analysis_at": "2026-08-19T10:00:00+00:00",
        "oab_risk_acknowledgement": {
            "acknowledgedByUserId": str(auth_context.user_id),
            "acknowledgedAt": "2026-08-19T10:01:00+00:00",
        },
    }
    await db_session.commit()

    await service.update_draft(
        auth_context.workspace_id,
        draft.id,
        TemplateDraftUpdate(
            components=_valid_components(
                body_text="Seu processo {{1}} agora aguarda os documentos {{2}}."
            )
        ),
    )

    await db_session.refresh(template)
    assert "ai_analysis" not in template.raw_payload
    assert "ai_analysis_at" not in template.raw_payload
    assert "oab_risk_acknowledgement" not in template.raw_payload
    audit = await db_session.scalar(
        select(KlmAuditEvent).where(
            KlmAuditEvent.workspace_id == auth_context.workspace_id,
            KlmAuditEvent.entity_id == template.id,
            KlmAuditEvent.event_type == "whatsapp_template_oab_analysis_invalidated",
        )
    )
    assert audit is not None


@pytest.mark.asyncio
async def test_submit_with_gate_closed_marks_submission_prepared_honestly(
    db_session, auth_context, monkeypatch
):
    monkeypatch.setattr(
        "app.services.whatsapp.template_management_service.external_delivery_allowed",
        lambda: False,
    )
    connection = await _make_connection(db_session, auth_context.workspace_id)
    service = _make_service(db_session)
    draft = await service.create_draft(
        auth_context.workspace_id,
        TemplateDraftCreate(
            whatsapp_connection_id=connection.id,
            name="gate_fechado",
            language="pt_BR",
            category="utility",
            components=_valid_components(),
        ),
    )

    result = await service.submit(auth_context.workspace_id, draft.id)

    assert result.submitted_to_meta is False
    assert result.template.local_status == "submission_prepared"
    assert result.template.external_template_id is None
    # nunca finge aprovado
    assert result.template.status.value != "approved"
    assert "aguardando aprovação" in result.message.lower()


@pytest.mark.asyncio
async def test_submit_twice_is_rejected(db_session, auth_context, monkeypatch):
    monkeypatch.setattr(
        "app.services.whatsapp.template_management_service.external_delivery_allowed",
        lambda: False,
    )
    connection = await _make_connection(db_session, auth_context.workspace_id)
    service = _make_service(db_session)
    draft = await service.create_draft(
        auth_context.workspace_id,
        TemplateDraftCreate(
            whatsapp_connection_id=connection.id,
            name="duplo_submit",
            language="pt_BR",
            category="utility",
            components=_valid_components(),
        ),
    )
    await service.submit(auth_context.workspace_id, draft.id)

    with pytest.raises(HTTPException) as excinfo:
        await service.submit(auth_context.workspace_id, draft.id)
    assert excinfo.value.status_code == 409


@pytest.mark.asyncio
async def test_submit_with_gate_open_calls_business_management_api(
    db_session, auth_context, monkeypatch
):
    monkeypatch.setattr(
        "app.services.whatsapp.template_management_service.external_delivery_allowed",
        lambda: True,
    )
    connection = await _make_connection(db_session, auth_context.workspace_id)
    service = _make_service(db_session)
    draft = await service.create_draft(
        auth_context.workspace_id,
        TemplateDraftCreate(
            whatsapp_connection_id=connection.id,
            name="gate_aberto",
            language="pt_BR",
            category="utility",
            components=_valid_components(),
        ),
    )

    async def _fake_create_message_template(**kwargs):
        assert kwargs["waba_id"] == "waba_1"
        assert kwargs["name"] == "gate_aberto"
        return {"id": "meta-template-123", "status": "PENDING"}

    monkeypatch.setattr(service.meta_client, "create_message_template", _fake_create_message_template)

    result = await service.submit(auth_context.workspace_id, draft.id)

    assert result.submitted_to_meta is True
    assert result.template.external_template_id == "meta-template-123"


@pytest.mark.asyncio
async def test_get_draft_rejects_synced_meta_template_as_draft(db_session, auth_context):
    from app.models.whatsapp_campaign import MetaTemplateStatus, WhatsAppMetaTemplate

    connection = await _make_connection(db_session, auth_context.workspace_id)
    synced = WhatsAppMetaTemplate(
        workspace_id=auth_context.workspace_id,
        whatsapp_connection_id=connection.id,
        external_template_id="ext-1",
        name="ja_sincronizado",
        language="pt_BR",
        category="utility",
        status=MetaTemplateStatus.approved,
        components=[],
        raw_payload={"id": "ext-1"},
    )
    db_session.add(synced)
    await db_session.flush()

    service = _make_service(db_session)
    with pytest.raises(HTTPException) as excinfo:
        await service.get_draft(auth_context.workspace_id, synced.id)
    assert excinfo.value.status_code == 409


@pytest.mark.asyncio
async def test_list_unified_returns_both_drafts_and_synced(db_session, auth_context):
    from app.models.whatsapp_campaign import MetaTemplateStatus, WhatsAppMetaTemplate

    connection = await _make_connection(db_session, auth_context.workspace_id)
    synced = WhatsAppMetaTemplate(
        workspace_id=auth_context.workspace_id,
        whatsapp_connection_id=connection.id,
        external_template_id="ext-2",
        name="sincronizado_2",
        language="pt_BR",
        category="marketing",
        status=MetaTemplateStatus.approved,
        quality_rating="green",
        components=[],
        raw_payload={"id": "ext-2"},
    )
    db_session.add(synced)
    await db_session.flush()

    service = _make_service(db_session)
    await service.create_draft(
        auth_context.workspace_id,
        TemplateDraftCreate(
            whatsapp_connection_id=connection.id,
            name="rascunho_unificado",
            language="pt_BR",
            category="utility",
            components=_valid_components(),
        ),
    )

    items = await service.list_unified(auth_context.workspace_id)
    names = {item.name: item for item in items}
    assert "sincronizado_2" in names
    assert "rascunho_unificado" in names
    assert names["sincronizado_2"].is_draft is False
    assert names["rascunho_unificado"].is_draft is True


# --- W2.2: aprovação interna antes da Meta -----------------------------------


@pytest.mark.asyncio
async def test_submit_without_approver_permission_parks_for_internal_approval(
    db_session, auth_context, monkeypatch
):
    monkeypatch.setattr(
        "app.services.whatsapp.template_management_service.external_delivery_allowed",
        lambda: False,
    )
    connection = await _make_connection(db_session, auth_context.workspace_id)
    service = _make_service(db_session)
    # auth_context já cria uma membership workspace_owner (ativa) para este
    # workspace — ela é o aprovador que deve receber a notificação abaixo.

    draft = await service.create_draft(
        auth_context.workspace_id,
        TemplateDraftCreate(
            whatsapp_connection_id=connection.id,
            name="requer_aprovacao",
            language="pt_BR",
            category="utility",
            components=_valid_components(),
        ),
    )

    operator_id = uuid4()
    result = await service.submit(
        auth_context.workspace_id,
        draft.id,
        requested_by_user_id=operator_id,
        requester_role=WorkspaceRole.operator,
        requester_label="Operador Teste",
    )

    assert result.submitted_to_meta is False
    assert result.template.local_status == "pending_internal_approval"
    assert "aprovação interna" in result.message.lower()

    audit_events = (
        await db_session.scalars(
            select(KlmAuditEvent).where(
                KlmAuditEvent.workspace_id == auth_context.workspace_id
            )
        )
    ).all()
    assert any(
        event.event_type == "whatsapp_template_internal_approval_requested"
        for event in audit_events
    )

    notifications = (
        await db_session.scalars(
            select(Notification).where(Notification.workspace_id == auth_context.workspace_id)
        )
    ).all()
    assert len(notifications) == 1
    assert "revise e aprove" in notifications[0].message.lower()


@pytest.mark.asyncio
async def test_submit_second_time_while_pending_internal_approval_is_rejected(
    db_session, auth_context, monkeypatch
):
    monkeypatch.setattr(
        "app.services.whatsapp.template_management_service.external_delivery_allowed",
        lambda: False,
    )
    connection = await _make_connection(db_session, auth_context.workspace_id)
    service = _make_service(db_session)
    draft = await service.create_draft(
        auth_context.workspace_id,
        TemplateDraftCreate(
            whatsapp_connection_id=connection.id,
            name="dupla_solicitacao",
            language="pt_BR",
            category="utility",
            components=_valid_components(),
        ),
    )
    await service.submit(
        auth_context.workspace_id,
        draft.id,
        requested_by_user_id=uuid4(),
        requester_role=WorkspaceRole.operator,
        requester_label="Operador Teste",
    )

    with pytest.raises(HTTPException) as excinfo:
        await service.submit(
            auth_context.workspace_id,
            draft.id,
            requested_by_user_id=uuid4(),
            requester_role=WorkspaceRole.operator,
            requester_label="Operador Teste",
        )
    assert excinfo.value.status_code == 409


@pytest.mark.asyncio
async def test_approve_internal_submission_then_prepares_meta_submission(
    db_session, auth_context, monkeypatch
):
    monkeypatch.setattr(
        "app.services.whatsapp.template_management_service.external_delivery_allowed",
        lambda: False,
    )
    connection = await _make_connection(db_session, auth_context.workspace_id)
    service = _make_service(db_session)
    draft = await service.create_draft(
        auth_context.workspace_id,
        TemplateDraftCreate(
            whatsapp_connection_id=connection.id,
            name="fluxo_completo",
            language="pt_BR",
            category="utility",
            components=_valid_components(),
        ),
    )
    requester_id = uuid4()
    await service.submit(
        auth_context.workspace_id,
        draft.id,
        requested_by_user_id=requester_id,
        requester_role=WorkspaceRole.operator,
        requester_label="Operador Teste",
    )

    pending = await service.list_pending_internal_approvals(auth_context.workspace_id)
    assert any(item.id == draft.id for item in pending)

    result = await service.approve_internal_submission(
        auth_context.workspace_id,
        draft.id,
        approver_user_id=auth_context.user_id,
        approver_label="Aprovador Teste",
    )
    assert result.template.local_status == "submission_prepared"
    assert result.submitted_to_meta is False

    pending_after = await service.list_pending_internal_approvals(auth_context.workspace_id)
    assert not any(item.id == draft.id for item in pending_after)


@pytest.mark.asyncio
async def test_submit_with_approver_role_skips_internal_approval(
    db_session, auth_context, monkeypatch
):
    monkeypatch.setattr(
        "app.services.whatsapp.template_management_service.external_delivery_allowed",
        lambda: False,
    )
    connection = await _make_connection(db_session, auth_context.workspace_id)
    service = _make_service(db_session)
    draft = await service.create_draft(
        auth_context.workspace_id,
        TemplateDraftCreate(
            whatsapp_connection_id=connection.id,
            name="aprovador_direto",
            language="pt_BR",
            category="utility",
            components=_valid_components(),
        ),
    )

    result = await service.submit(
        auth_context.workspace_id,
        draft.id,
        requested_by_user_id=auth_context.user_id,
        requester_role=WorkspaceRole.workspace_owner,
        requester_label="Owner Teste",
    )
    assert result.template.local_status == "submission_prepared"


# --- W2.2: "corrigir e reenviar" (clonar rejeitado) --------------------------


@pytest.mark.asyncio
async def test_clone_rejected_template_creates_editable_draft(db_session, auth_context):
    from app.models.whatsapp_campaign import MetaTemplateStatus, WhatsAppMetaTemplate

    connection = await _make_connection(db_session, auth_context.workspace_id)
    rejected = WhatsAppMetaTemplate(
        workspace_id=auth_context.workspace_id,
        whatsapp_connection_id=connection.id,
        external_template_id="ext-rejected-1",
        name="promocao_rejeitada",
        language="pt_BR",
        category="marketing",
        status=MetaTemplateStatus.rejected,
        components=[
            {"type": "BODY", "text": "Aproveite {{1}}!"},
        ],
        raw_payload={"rejected_reason": "INVALID_FORMAT"},
    )
    db_session.add(rejected)
    await db_session.flush()

    service = _make_service(db_session)
    clone = await service.clone_rejected_as_draft(auth_context.workspace_id, rejected.id)

    assert clone.is_draft is True
    assert clone.local_status == "draft"
    assert clone.name != rejected.name
    assert clone.name.startswith("promocao_rejeitada")


@pytest.mark.asyncio
async def test_clone_non_rejected_template_is_rejected(db_session, auth_context):
    from app.models.whatsapp_campaign import MetaTemplateStatus, WhatsAppMetaTemplate

    connection = await _make_connection(db_session, auth_context.workspace_id)
    approved = WhatsAppMetaTemplate(
        workspace_id=auth_context.workspace_id,
        whatsapp_connection_id=connection.id,
        external_template_id="ext-approved-1",
        name="aprovado_1",
        language="pt_BR",
        category="utility",
        status=MetaTemplateStatus.approved,
        components=[{"type": "BODY", "text": "Olá"}],
        raw_payload={},
    )
    db_session.add(approved)
    await db_session.flush()

    service = _make_service(db_session)
    with pytest.raises(HTTPException) as excinfo:
        await service.clone_rejected_as_draft(auth_context.workspace_id, approved.id)
    assert excinfo.value.status_code == 409
