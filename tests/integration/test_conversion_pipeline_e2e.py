"""Prova de ponta a ponta do pipeline anúncio → contrato → cobrança (item e
da tarefa de fechamento de gaps, auditoria 2026-07-26).

Dois canais, ambos exercitando o MESMO motor de atribuição/feedback
(`KlmOperationsService._ensure_conversion_feedback_intent` →
`ExternalActionIntent(ads_conversion_feedback)` →
`ads_feedback_tasks.dispatch_intent`):

  A. Lead CTWA no WhatsApp (referral com ctwa_clid) → contrato assinado →
     cobrança paga. ConversionEvents (AcquisitionEvent) criados uma única
     vez mesmo reprocessando. Emissor gera envelope Meta CAPI para Business
     Messaging (action_source=business_messaging, ctwa_clid em user_data,
     event_id determinístico klm-<uuid>, event_time unix, payloadSha256
     hex-64) — valida o formato contra a spec do enunciado.
  B. Lead de formulário Google Ads com gclid → contrato assinado → cobrança
     paga. Emissor gera envelope Data Manager API (gclid + conversion_action
     + conversion_date_time, NÃO o schema antigo do ClickConversionService).

Em ambos: gate de ações externas fechado (padrão em teste) → nada sai,
tudo persistido como `simulated`/`blocked`. O relatório
(`GET /tracking/summary`) mostra a campanha com a receita certa (só
`payment_confirmed` conta como receita — `contract_created` conta como
conversão, não como receita, ver `TRACKING_REVENUE_EVENTS`).
"""

from __future__ import annotations

import re
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models.client import Client
from app.models.conversion_feedback import AdsConversionFeedbackRecord
from app.models.klm_operations import (
    AcquisitionEvent,
    ExternalActionIntent,
    ExternalActionIntentStatus,
    OmnichannelThread,
)
from app.models.matter import Matter
from app.models.workspace import Workspace
from app.schemas.klm_operations import AcquisitionEventCreate
from app.services.conversion_events_service import record_conversion_event
from app.services.google_lead_webhook_service import generate_or_rotate_google_key
from app.services.klm_operations_service import KlmOperationsService
from app.services.sdr_intake_service import _create_lead_client_and_matter
from app.workers import ads_feedback_tasks

TEST_INTEGRATION_SECRET_KEY = "test-only-integration-key-7Hf9pQ2wX4mK8cR6vN3z"
_UUID_EVENT_ID_RE = re.compile(
    r"^klm-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


@pytest.fixture(autouse=True)
def _configure_integration_secret_key(monkeypatch):
    monkeypatch.setattr(settings, "INTEGRATION_SECRET_ENCRYPTION_KEY", TEST_INTEGRATION_SECRET_KEY)


@pytest.fixture(autouse=True)
def _isolate_from_sdr_dispatch(monkeypatch):
    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr("app.workers.event_bus.dispatch_new_lead", _noop)
    return None


async def _configure_provider_settings(db_session, workspace_id) -> None:
    workspace = await db_session.get(Workspace, workspace_id)
    workspace.settings = {
        "adsConversionFeedback": {
            "meta_ads": {
                "datasetId": "dataset-e2e",
                "credentialRef": "secret/meta-e2e",
                "consentPolicyRef": "policy-e2e",
            },
            "google_ads": {
                "customerId": "1234567890",
                "conversionAction": "customers/1234567890/conversionActions/e2e",
                "credentialRef": "secret/google-e2e",
                "adUserDataConsent": "GRANTED",
            },
        }
    }
    await db_session.flush()


CONSENT_METADATA = {
    "adDataConsent": "GRANTED",
    "adDataConsentEvidenceRef": "consent-ledger:e2e-test",
}


@pytest.mark.asyncio
async def test_whatsapp_ctwa_lead_to_paid_contract_meta_capi_pipeline(
    client, db_session, auth_context, active_subscription
):
    await _configure_provider_settings(db_session, auth_context.workspace_id)
    ops = KlmOperationsService(db_session)

    ctwa_lead, matter = await _create_lead_client_and_matter(
        db_session,
        workspace_id=auth_context.workspace_id,
        name="Lead CTWA E2E",
        lead_source="whatsapp",
        demand_type="Consulta trabalhista via anúncio",
    )
    thread = OmnichannelThread(
        workspace_id=auth_context.workspace_id,
        matter_id=matter.id,
        client_id=ctwa_lead.id,
        primary_channel="whatsapp",
        subject="Lead CTWA E2E",
        queue="new",
        thread_metadata={},
    )
    db_session.add(thread)
    await db_session.flush()

    wamid = f"wamid.E2E{uuid4().hex[:12]}"
    ctwa_clid = "ARDp1234567890abcdefWA"

    # 1) Toque de aquisição (equivalente a `_record_ctwa_acquisition`): a
    # conversa nasce com o ctwa_clid do referral do anúncio.
    await ops.record_acquisition_event(
        auth_context.workspace_id,
        None,
        AcquisitionEventCreate(
            matter_id=matter.id,
            client_id=ctwa_lead.id,
            event_name="conversation_started",
            channel="whatsapp",
            campaign="campanha-e2e-whatsapp",
            dedupe_key=f"ctwa:{wamid}",
            click_ids={"ctwa_clid": ctwa_clid},
            event_metadata={"threadId": str(thread.id), **CONSENT_METADATA},
        ),
    )

    # 2) Marco: contrato assinado. Chamado duas vezes (reprocessamento) —
    # idempotente, não duplica.
    for _ in range(2):
        await record_conversion_event(
            db_session,
            workspace_id=auth_context.workspace_id,
            user_id=None,
            marco="contrato_assinado",
            matter_id=matter.id,
            client_id=ctwa_lead.id,
            channel="whatsapp",
            value=4800.0,
            event_metadata=dict(CONSENT_METADATA),
        )

    # 3) Marco: cobrança paga. Também reprocessado.
    for _ in range(2):
        await record_conversion_event(
            db_session,
            workspace_id=auth_context.workspace_id,
            user_id=None,
            marco="cobranca_paga",
            matter_id=matter.id,
            client_id=ctwa_lead.id,
            channel="whatsapp",
            value=4800.0,
            event_metadata=dict(CONSENT_METADATA),
        )
    await db_session.commit()

    # ConversionEvents (AcquisitionEvent) criados exatamente uma vez cada,
    # apesar do reprocessamento.
    contract_events = (
        await db_session.scalars(
            select(AcquisitionEvent).where(
                AcquisitionEvent.workspace_id == auth_context.workspace_id,
                AcquisitionEvent.dedupe_key == f"conversion:contrato_assinado:{matter.id}",
            )
        )
    ).all()
    assert len(contract_events) == 1
    payment_events = (
        await db_session.scalars(
            select(AcquisitionEvent).where(
                AcquisitionEvent.workspace_id == auth_context.workspace_id,
                AcquisitionEvent.dedupe_key == f"conversion:cobranca_paga:{matter.id}",
            )
        )
    ).all()
    assert len(payment_events) == 1

    # Emissor: uma ExternalActionIntent por marco (contract_created -> Lead,
    # payment_confirmed -> Purchase — só `payment_confirmed` tem identidade
    # comercial explícita o bastante para colapsar com outro evento de
    # receita; `contract_created` não é evento de receita, ver
    # `TRACKING_REVENUE_EVENTS`). Reprocessar cada marco 2x acima não criou
    # intents extras — só uma por marco, confirmando a idempotência.
    intents = (
        await db_session.scalars(
            select(ExternalActionIntent).where(
                ExternalActionIntent.workspace_id == auth_context.workspace_id,
                ExternalActionIntent.action_type == "ads_conversion_feedback",
                ExternalActionIntent.provider == "meta_ads",
            )
        )
    ).all()
    assert len(intents) == 2
    by_conversion_event = {
        intent.payload["conversionEvent"]: intent for intent in intents
    }
    assert set(by_conversion_event) == {"contract_created", "payment_confirmed"}
    intent = by_conversion_event["payment_confirmed"]
    envelope = intent.payload["providerEnvelope"]

    # Valida o formato do payload Meta CAPI contra a spec do enunciado.
    assert len(envelope["payloadSha256"]) == 64
    assert all(char in "0123456789abcdef" for char in envelope["payloadSha256"])
    meta_event = envelope["providerPayload"]["data"][0]
    assert _UUID_EVENT_ID_RE.match(meta_event["event_id"]), meta_event["event_id"]
    assert isinstance(meta_event["event_time"], int)
    assert meta_event["action_source"] == "business_messaging"
    assert meta_event["messaging_channel"] == "whatsapp"
    assert meta_event["user_data"]["ctwa_clid"] == ctwa_clid
    assert meta_event["custom_data"]["value"] == 4800.0
    assert meta_event["custom_data"]["currency"] == "BRL"
    assert envelope["readyForDispatch"] is True

    # Gate fechado (padrão em teste, `KLM_EXTERNAL_ACTIONS_ENABLED`): as
    # intents nasceram já `blocked_external` — nada foi sequer enfileirado
    # para aprovação, muito menos enviado.
    for stored_intent in intents:
        assert stored_intent.status.value == "blocked_external"

    # Mesmo que um operador promova a intent (simulando fila para aprovação),
    # o worker só grava um recibo `blocked`/simulado: a credencial real de
    # despacho (`meta_capi`) nunca foi configurada neste teste.
    for stored_intent in intents:
        stored_intent.status = ExternalActionIntentStatus.awaiting_approval
    await db_session.flush()
    counts = await ads_feedback_tasks.dispatch_workspace_conversion_feedback(
        auth_context.workspace_id, db=db_session
    )
    assert counts["dispatchedReal"] == 0
    assert counts["blockedSimulated"] == 2
    receipts = (
        await db_session.scalars(
            select(AdsConversionFeedbackRecord).where(
                AdsConversionFeedbackRecord.workspace_id == auth_context.workspace_id,
                AdsConversionFeedbackRecord.intent_id.in_([i.id for i in intents]),
                AdsConversionFeedbackRecord.record_type == "provider_receipt",
            )
        )
    ).all()
    assert len(receipts) == 2
    assert all(receipt.state == "blocked" for receipt in receipts)
    assert all(receipt.provider_receipt["simulated"] is True for receipt in receipts)
    assert all(receipt.external_actions_executed == 0 for receipt in receipts)

    # Relatório: a campanha aparece com a receita certa (só payment conta).
    summary_response = await client.get("/api/v1/klm/tracking/summary")
    assert summary_response.status_code == 200, summary_response.text
    rows = summary_response.json()["attributionRows"]
    meta_row = next(row for row in rows if row["provider"] == "meta_ads")
    assert meta_row["attributableRevenue"] == 4800.0
    assert meta_row["clients"] >= 1


@pytest.mark.asyncio
async def test_google_form_lead_to_paid_contract_data_manager_pipeline(
    client, db_session, auth_context, active_subscription
):
    await _configure_provider_settings(db_session, auth_context.workspace_id)

    google_key = await generate_or_rotate_google_key(
        db_session, workspace_id=auth_context.workspace_id
    )
    await db_session.commit()

    lead_id = str(uuid4())
    gclid = "EAIaIQobChMI1234567890"
    lead_response = await client.post(
        "/api/v1/webhooks/google-leads",
        json={
            "google_key": google_key,
            "lead_id": lead_id,
            "campaign_id": "campanha-e2e-google",
            "gcl_id": gclid,
            "is_test": False,
            "user_column_data": [
                {"column_id": "FULL_NAME", "string_value": "Lead Google E2E"},
                {"column_id": "EMAIL", "string_value": f"lead-e2e-{uuid4().hex[:8]}@example.com"},
            ],
        },
    )
    assert lead_response.status_code == 200, lead_response.text

    matter = await db_session.scalar(
        select(Matter).where(
            Matter.workspace_id == auth_context.workspace_id,
            Matter.lead_source == "google_ads",
        )
    )
    assert matter is not None
    client_row = await db_session.scalar(select(Client).where(Client.id == matter.client_id))
    assert client_row is not None

    for _ in range(2):
        await record_conversion_event(
            db_session,
            workspace_id=auth_context.workspace_id,
            user_id=None,
            marco="contrato_assinado",
            matter_id=matter.id,
            client_id=client_row.id,
            channel="google_ads",
            campaign="campanha-e2e-google",
            click_ids={"gclid": gclid},
            value=3000.0,
            event_metadata=dict(CONSENT_METADATA),
        )
    for _ in range(2):
        await record_conversion_event(
            db_session,
            workspace_id=auth_context.workspace_id,
            user_id=None,
            marco="cobranca_paga",
            matter_id=matter.id,
            client_id=client_row.id,
            channel="google_ads",
            campaign="campanha-e2e-google",
            click_ids={"gclid": gclid},
            value=3000.0,
            event_metadata=dict(CONSENT_METADATA),
        )
    await db_session.commit()

    payment_events = (
        await db_session.scalars(
            select(AcquisitionEvent).where(
                AcquisitionEvent.workspace_id == auth_context.workspace_id,
                AcquisitionEvent.dedupe_key == f"conversion:cobranca_paga:{matter.id}",
            )
        )
    ).all()
    assert len(payment_events) == 1

    intents = (
        await db_session.scalars(
            select(ExternalActionIntent).where(
                ExternalActionIntent.workspace_id == auth_context.workspace_id,
                ExternalActionIntent.action_type == "ads_conversion_feedback",
                ExternalActionIntent.provider == "google_ads",
            )
        )
    ).all()
    assert len(intents) == 2
    by_conversion_event = {
        intent.payload["conversionEvent"]: intent for intent in intents
    }
    assert set(by_conversion_event) == {"contract_created", "payment_confirmed"}
    envelope = by_conversion_event["payment_confirmed"].payload["providerEnvelope"]
    google_event = envelope["providerPayload"]["events"][0]

    # Data Manager API — NÃO o schema antigo do ClickConversionService.
    assert google_event["gclid"] == gclid
    assert google_event["conversion_action"] == "customers/1234567890/conversionActions/e2e"
    assert re.match(
        r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\+00:00$", google_event["conversion_date_time"]
    )
    assert "conversions" not in envelope["providerPayload"]
    assert "orderId" not in google_event
    assert envelope["readyForDispatch"] is True

    # Gate fechado: as intents nasceram já `blocked_external`.
    for stored_intent in intents:
        assert stored_intent.status.value == "blocked_external"

    # Mesmo promovendo para a fila de aprovação, sem credencial real do
    # Data Manager configurada (`google_data_manager`) o worker só grava
    # recibo `blocked`/simulado — nada sai.
    for stored_intent in intents:
        stored_intent.status = ExternalActionIntentStatus.awaiting_approval
    await db_session.flush()
    counts = await ads_feedback_tasks.dispatch_workspace_conversion_feedback(
        auth_context.workspace_id, db=db_session
    )
    assert counts["dispatchedReal"] == 0
    assert counts["blockedSimulated"] == 2
    receipts = (
        await db_session.scalars(
            select(AdsConversionFeedbackRecord).where(
                AdsConversionFeedbackRecord.workspace_id == auth_context.workspace_id,
                AdsConversionFeedbackRecord.intent_id.in_([i.id for i in intents]),
                AdsConversionFeedbackRecord.record_type == "provider_receipt",
            )
        )
    ).all()
    assert len(receipts) == 2
    assert all(receipt.state == "blocked" for receipt in receipts)
    assert all(receipt.provider_receipt["simulated"] is True for receipt in receipts)

    summary_response = await client.get("/api/v1/klm/tracking/summary")
    assert summary_response.status_code == 200, summary_response.text
    rows = summary_response.json()["attributionRows"]
    google_row = next(row for row in rows if row.get("provider") == "google_ads")
    assert google_row["attributableRevenue"] == 3000.0
    assert google_row["clients"] >= 1
