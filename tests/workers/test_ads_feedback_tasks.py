from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.deps import AuthContext
from app.models.conversion_feedback import AdsConversionFeedbackRecord
from app.models.finance import IntegrationConnection, IntegrationProvider, IntegrationStatus
from app.models.klm_operations import ExternalActionIntent, ExternalActionIntentStatus
from app.services.integration_secrets import protect_integration_settings
from app.workers import ads_feedback_tasks

TEST_INTEGRATION_SECRET_KEY = "test-only-integration-key-7Hf9pQ2wX4mK8cR6vN3z"


@pytest.fixture(autouse=True)
def _configure_integration_secret_key(monkeypatch):
    monkeypatch.setattr(
        settings, "INTEGRATION_SECRET_ENCRYPTION_KEY", TEST_INTEGRATION_SECRET_KEY
    )


def _session_factory(db_session: AsyncSession):
    assert db_session.bind is not None
    return async_sessionmaker(db_session.bind, class_=AsyncSession, expire_on_commit=False)


def _envelope(payload_sha256: str = "a" * 64) -> dict:
    return {
        "schemaVersion": "klm.ads.provider-envelope.v2",
        "payloadSha256": payload_sha256,
        "providerPayload": {
            "data": [
                {
                    "event_name": "Lead",
                    "event_time": 1721565000,
                    "event_id": "klm-abc",
                    "action_source": "business_messaging",
                    "messaging_channel": "whatsapp",
                    "user_data": {"ctwa_clid": "ARDp1234567890abcdefWA"},
                    "custom_data": {"currency": "BRL"},
                }
            ]
        },
    }


async def _create_intent(
    db_session: AsyncSession,
    auth_context: AuthContext,
    *,
    status: ExternalActionIntentStatus,
    ready_for_dispatch: bool = True,
    payload_sha256: str = "a" * 64,
    provider: str = "meta_ads",
) -> ExternalActionIntent:
    envelope = _envelope(payload_sha256)
    intent = ExternalActionIntent(
        workspace_id=auth_context.workspace_id,
        requested_by_user_id=auth_context.user_id,
        action_type="ads_conversion_feedback",
        provider=provider,
        idempotency_key=f"ads-conversion-feedback:{provider}:{uuid4()}",
        status=status,
        payload={
            "schemaVersion": "klm.ads.conversion-feedback.v2",
            "sourceEventId": str(uuid4()),
            "sourceTouchEventId": str(uuid4()),
            "providerEventId": "klm-abc",
            "conversionEvent": "contract_created",
            "conversionHappenedAt": datetime(2026, 7, 21, 12, 30, tzinfo=UTC).isoformat(),
            "value": 4800.0,
            "currency": "BRL",
            "readyForDispatch": ready_for_dispatch,
            "missingConfiguration": [] if ready_for_dispatch else ["dataset_id"],
            "payloadSha256": payload_sha256,
            "providerEnvelope": envelope,
            "externalActionsExecuted": 0,
        },
        reason="Conversao preparada para teste.",
    )
    db_session.add(intent)
    await db_session.flush()
    return intent


async def _configure_meta_capi(
    db_session: AsyncSession, auth_context: AuthContext, *, token: str = "capi-token-value"
) -> None:
    connection = IntegrationConnection(
        workspace_id=auth_context.workspace_id,
        provider=IntegrationProvider.custom,
        status=IntegrationStatus.connected,
        label=ads_feedback_tasks.MEASUREMENT_LABEL,
        settings=protect_integration_settings(
            {"datasetId": "dataset-123", "pixelId": "pixel-123", "apiToken": token}
        ),
    )
    db_session.add(connection)
    await db_session.flush()


@pytest.mark.asyncio
async def test_gate_closed_writes_simulated_blocked_receipt_and_is_idempotent(
    db_session: AsyncSession, auth_context: AuthContext, monkeypatch
):
    monkeypatch.setattr(
        "app.workers.ads_feedback_tasks.external_delivery_allowed", lambda: False
    )
    intent = await _create_intent(
        db_session, auth_context, status=ExternalActionIntentStatus.awaiting_approval
    )

    counts = await ads_feedback_tasks.dispatch_workspace_conversion_feedback(
        auth_context.workspace_id, db=db_session
    )
    assert counts["preparedProcessed"] == 1
    assert counts["blockedSimulated"] == 1
    assert counts["dispatchedReal"] == 0

    receipts = (
        await db_session.scalars(
            select(AdsConversionFeedbackRecord).where(
                AdsConversionFeedbackRecord.intent_id == intent.id,
                AdsConversionFeedbackRecord.record_type == "provider_receipt",
            )
        )
    ).all()
    assert len(receipts) == 1
    assert receipts[0].state == "blocked"
    assert receipts[0].provider_receipt["simulated"] is True
    assert receipts[0].external_actions_executed == 0

    # Second run must not create a duplicate receipt (idempotent).
    counts_again = await ads_feedback_tasks.dispatch_workspace_conversion_feedback(
        auth_context.workspace_id, db=db_session
    )
    assert counts_again["alreadyDispatched"] == 1
    assert counts_again["blockedSimulated"] == 0
    receipts_after = (
        await db_session.scalars(
            select(AdsConversionFeedbackRecord).where(
                AdsConversionFeedbackRecord.intent_id == intent.id,
                AdsConversionFeedbackRecord.record_type == "provider_receipt",
            )
        )
    ).all()
    assert len(receipts_after) == 1


@pytest.mark.asyncio
async def test_awaiting_approval_never_dispatches_even_with_gate_open(
    db_session: AsyncSession, auth_context: AuthContext, monkeypatch
):
    monkeypatch.setattr(
        "app.workers.ads_feedback_tasks.external_delivery_allowed", lambda: True
    )
    await _configure_meta_capi(db_session, auth_context)
    intent = await _create_intent(
        db_session, auth_context, status=ExternalActionIntentStatus.awaiting_approval
    )

    async def _fail_dispatch(**_kwargs):
        pytest.fail("Não deveria despachar de verdade sem aprovação local.")

    monkeypatch.setattr(ads_feedback_tasks, "_dispatch_meta_real", _fail_dispatch)

    counts = await ads_feedback_tasks.dispatch_workspace_conversion_feedback(
        auth_context.workspace_id, db=db_session
    )
    assert counts["blockedSimulated"] == 1
    assert counts["dispatchedReal"] == 0
    record = await db_session.scalar(
        select(AdsConversionFeedbackRecord).where(
            AdsConversionFeedbackRecord.intent_id == intent.id
        )
    )
    assert record.provider_receipt["simulated"] is True


@pytest.mark.asyncio
async def test_approved_local_with_gate_open_and_config_dispatches_real_and_writes_receipt(
    db_session: AsyncSession, auth_context: AuthContext, monkeypatch
):
    monkeypatch.setattr(
        "app.workers.ads_feedback_tasks.external_delivery_allowed", lambda: True
    )
    await _configure_meta_capi(db_session, auth_context)
    intent = await _create_intent(
        db_session, auth_context, status=ExternalActionIntentStatus.approved_local
    )

    captured_urls: list[str] = []

    async def fake_dispatch(*, envelope, config):
        captured_urls.append(config.dataset_id or "")
        assert config.access_token == "capi-token-value"
        return {"events_received": 1}, 200, None

    monkeypatch.setattr(ads_feedback_tasks, "_dispatch_meta_real", fake_dispatch)

    counts = await ads_feedback_tasks.dispatch_workspace_conversion_feedback(
        auth_context.workspace_id, db=db_session
    )
    assert counts["dispatchedReal"] == 1
    assert counts["blockedSimulated"] == 0
    assert captured_urls == ["dataset-123"]

    record = await db_session.scalar(
        select(AdsConversionFeedbackRecord).where(
            AdsConversionFeedbackRecord.intent_id == intent.id
        )
    )
    assert record.state == "accepted"
    assert record.http_status == 200
    assert record.provider_receipt["simulated"] is False
    assert record.external_actions_executed == 1


@pytest.mark.asyncio
async def test_missing_measurement_config_stays_blocked_even_when_approved_and_gate_open(
    db_session: AsyncSession, auth_context: AuthContext, monkeypatch
):
    monkeypatch.setattr(
        "app.workers.ads_feedback_tasks.external_delivery_allowed", lambda: True
    )
    await _create_intent(
        db_session, auth_context, status=ExternalActionIntentStatus.approved_local
    )

    counts = await ads_feedback_tasks.dispatch_workspace_conversion_feedback(
        auth_context.workspace_id, db=db_session
    )
    assert counts["blockedSimulated"] == 1
    assert counts["dispatchedReal"] == 0


@pytest.mark.asyncio
async def test_transport_failure_records_retryable_receipt_without_faking_success(
    db_session: AsyncSession, auth_context: AuthContext, monkeypatch
):
    monkeypatch.setattr(
        "app.workers.ads_feedback_tasks.external_delivery_allowed", lambda: True
    )
    await _configure_meta_capi(db_session, auth_context)
    intent = await _create_intent(
        db_session, auth_context, status=ExternalActionIntentStatus.approved_local
    )

    async def broken_dispatch(*, envelope, config):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(ads_feedback_tasks, "_dispatch_meta_real", broken_dispatch)

    counts = await ads_feedback_tasks.dispatch_workspace_conversion_feedback(
        auth_context.workspace_id, db=db_session
    )
    assert counts["failed"] == 1
    record = await db_session.scalar(
        select(AdsConversionFeedbackRecord).where(
            AdsConversionFeedbackRecord.intent_id == intent.id
        )
    )
    assert record.state == "retryable"
    assert record.provider_receipt["simulated"] is False


@pytest.mark.asyncio
async def test_blocked_receipt_does_not_shadow_later_real_dispatch(
    db_session: AsyncSession, auth_context: AuthContext, monkeypatch
):
    """Regressão C1: recibo 'blocked' (gate fechado) NÃO pode impedir o
    despacho real depois que a intenção é aprovada e o gate abre."""
    await _configure_meta_capi(db_session, auth_context)
    intent = await _create_intent(
        db_session, auth_context, status=ExternalActionIntentStatus.awaiting_approval
    )

    # 1ª passagem: gate fechado -> grava recibo 'blocked' com o hash do envelope.
    monkeypatch.setattr(
        "app.workers.ads_feedback_tasks.external_delivery_allowed", lambda: False
    )
    first = await ads_feedback_tasks.dispatch_workspace_conversion_feedback(
        auth_context.workspace_id, db=db_session
    )
    assert first["blockedSimulated"] == 1
    assert first["dispatchedReal"] == 0

    # Aprova a intenção e abre o gate.
    intent.status = ExternalActionIntentStatus.approved_local
    await db_session.flush()

    dispatched: list[str] = []

    async def fake_dispatch(*, envelope, config):
        dispatched.append(config.dataset_id or "")
        return {"events_received": 1}, 200, None

    monkeypatch.setattr(
        "app.workers.ads_feedback_tasks.external_delivery_allowed", lambda: True
    )
    monkeypatch.setattr(ads_feedback_tasks, "_dispatch_meta_real", fake_dispatch)

    # 2ª passagem: o recibo 'blocked' NÃO pode sombrear; despacho real acontece.
    second = await ads_feedback_tasks.dispatch_workspace_conversion_feedback(
        auth_context.workspace_id, db=db_session
    )
    assert second["dispatchedReal"] == 1, "recibo blocked sombreou o despacho real"
    assert dispatched == ["dataset-123"]


def _google_envelope(payload_sha256: str = "b" * 64) -> dict:
    return {
        "schemaVersion": "klm.ads.provider-envelope.v2",
        "payloadSha256": payload_sha256,
        "destination": {
            "customerId": "1234567890",
            "endpoint": "https://datamanager.googleapis.com/v1/accounts/1234567890:ingestEvents",
        },
        "providerPayload": {
            "events": [
                {
                    "conversion_action": "customers/123/conversionActions/456",
                    "conversion_date_time": "2026-07-21 12:30:00+00:00",
                    "gclid": "EAIaIQobChMI1234567890",
                    "order_id": "klm-abc",
                    "currency_code": "BRL",
                    "user_identifiers": [],
                    "consent": {"ad_user_data": "GRANTED"},
                }
            ],
            "validateOnly": False,
        },
    }


async def _create_google_intent(
    db_session: AsyncSession,
    auth_context: AuthContext,
    *,
    status: ExternalActionIntentStatus,
    ready_for_dispatch: bool = True,
    payload_sha256: str = "b" * 64,
) -> ExternalActionIntent:
    envelope = _google_envelope(payload_sha256)
    intent = ExternalActionIntent(
        workspace_id=auth_context.workspace_id,
        requested_by_user_id=auth_context.user_id,
        action_type="ads_conversion_feedback",
        provider="google_ads",
        idempotency_key=f"ads-conversion-feedback:google_ads:{uuid4()}",
        status=status,
        payload={
            "schemaVersion": "klm.ads.conversion-feedback.v2",
            "sourceEventId": str(uuid4()),
            "sourceTouchEventId": str(uuid4()),
            "providerEventId": "klm-abc",
            "conversionEvent": "contract_created",
            "conversionHappenedAt": datetime(2026, 7, 21, 12, 30, tzinfo=UTC).isoformat(),
            "value": 4800.0,
            "currency": "BRL",
            "readyForDispatch": ready_for_dispatch,
            "missingConfiguration": [] if ready_for_dispatch else ["customer_id"],
            "payloadSha256": payload_sha256,
            "providerEnvelope": envelope,
            "externalActionsExecuted": 0,
        },
        reason="Conversao preparada para teste.",
    )
    db_session.add(intent)
    await db_session.flush()
    return intent


async def _configure_google_data_manager(
    db_session: AsyncSession,
    auth_context: AuthContext,
    *,
    access_token: str = "dm-access-token-value",
) -> None:
    from app.services.google_conversions_service import GOOGLE_CONVERSIONS_LABEL

    connection = IntegrationConnection(
        workspace_id=auth_context.workspace_id,
        provider=IntegrationProvider.google_ads,
        status=IntegrationStatus.connected,
        label=GOOGLE_CONVERSIONS_LABEL,
        settings=protect_integration_settings(
            {
                "customerId": "1234567890",
                "developerToken": "dev-token-value",
                "accessToken": access_token,
            }
        ),
    )
    db_session.add(connection)
    await db_session.flush()


@pytest.mark.asyncio
async def test_google_ads_missing_measurement_config_stays_blocked(
    db_session: AsyncSession, auth_context: AuthContext, monkeypatch
):
    monkeypatch.setattr(
        "app.workers.ads_feedback_tasks.external_delivery_allowed", lambda: True
    )
    await _create_google_intent(
        db_session, auth_context, status=ExternalActionIntentStatus.approved_local
    )

    counts = await ads_feedback_tasks.dispatch_workspace_conversion_feedback(
        auth_context.workspace_id, db=db_session
    )
    assert counts["blockedSimulated"] == 1
    assert counts["dispatchedReal"] == 0


@pytest.mark.asyncio
async def test_google_ads_approved_with_gate_open_and_config_dispatches_real(
    db_session: AsyncSession, auth_context: AuthContext, monkeypatch
):
    monkeypatch.setattr(
        "app.workers.ads_feedback_tasks.external_delivery_allowed", lambda: True
    )
    await _configure_google_data_manager(db_session, auth_context)
    intent = await _create_google_intent(
        db_session, auth_context, status=ExternalActionIntentStatus.approved_local
    )

    captured: list[str] = []

    async def fake_dispatch(*, envelope, config):
        captured.append(config.customer_id or "")
        assert config.access_token == "dm-access-token-value"
        assert config.developer_token == "dev-token-value"
        return {"eventCount": 1}, 200, None

    monkeypatch.setattr(ads_feedback_tasks, "_dispatch_google_real", fake_dispatch)

    counts = await ads_feedback_tasks.dispatch_workspace_conversion_feedback(
        auth_context.workspace_id, db=db_session
    )
    assert counts["dispatchedReal"] == 1
    assert counts["blockedSimulated"] == 0
    assert captured == ["1234567890"]

    record = await db_session.scalar(
        select(AdsConversionFeedbackRecord).where(
            AdsConversionFeedbackRecord.intent_id == intent.id
        )
    )
    assert record.state == "accepted"
    assert record.http_status == 200
    assert record.provider_receipt["simulated"] is False


@pytest.mark.asyncio
async def test_google_ads_never_dispatched_via_meta_real_function(
    db_session: AsyncSession, auth_context: AuthContext, monkeypatch
):
    """Regressão do item 6 da auditoria: `dispatch_intent` recusava
    silenciosamente qualquer provider != meta_ads. Confirma que o Google
    passa pelo seu próprio dispatcher, nunca pelo `_dispatch_meta_real`."""
    monkeypatch.setattr(
        "app.workers.ads_feedback_tasks.external_delivery_allowed", lambda: True
    )
    await _configure_google_data_manager(db_session, auth_context)
    await _create_google_intent(
        db_session, auth_context, status=ExternalActionIntentStatus.approved_local
    )

    async def _fail_if_called(**_kwargs):
        pytest.fail("Google não pode ser despachado pelo dispatcher do Meta.")

    monkeypatch.setattr(ads_feedback_tasks, "_dispatch_meta_real", _fail_if_called)

    async def fake_google_dispatch(*, envelope, config):
        return {"eventCount": 1}, 200, None

    monkeypatch.setattr(ads_feedback_tasks, "_dispatch_google_real", fake_google_dispatch)

    counts = await ads_feedback_tasks.dispatch_workspace_conversion_feedback(
        auth_context.workspace_id, db=db_session
    )
    assert counts["dispatchedReal"] == 1
