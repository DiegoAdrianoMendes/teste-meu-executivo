from datetime import UTC, datetime

import pytest

from app.models.omnichannel_v6 import OmnichannelProviderWebhookEnvelope
from app.models.whatsapp import ConnectionType, WaConnectionStatus, WhatsAppConnection
from app.services.message_evidence_service import ProviderEnvelopeContext
from app.services.whatsapp.errors import WhatsAppProviderError
from app.services.whatsapp.meta_webhook_operations_service import (
    META_WEBHOOK_FIELD_CATALOG,
    META_WEBHOOK_SEMANTIC_CONTRACTS,
    META_WEBHOOK_SPRINTS,
    _safe_operational_payload,
    meta_webhook_catalog,
    meta_webhook_catalog_with_events,
)
from app.services.whatsapp.webhooks.automation_policy_service import (
    MetaWebhookAutomationPolicyService,
)
from app.services.whatsapp.webhooks.dispatcher import (
    MetaWebhookDispatcher,
    receipt_item_fingerprint,
)
from app.services.whatsapp.webhooks.registry import (
    META_WEBHOOK_HANDLERS,
    META_WEBHOOK_REGISTRY,
)
from app.services.whatsapp.webhooks.subscription_service import (
    _is_transient_provider_error,
)

EXPECTED_FIELDS = {
    "account_alerts",
    "account_review_update",
    "account_settings_update",
    "account_update",
    "automatic_events",
    "business_capability_update",
    "business_status_update",
    "business_username_updates",
    "calls",
    "flows",
    "group_lifecycle_update",
    "group_participants_update",
    "group_settings_update",
    "group_status_update",
    "history",
    "message_echoes",
    "message_template_components_update",
    "message_template_quality_update",
    "message_template_status_update",
    "messages",
    "messaging_handovers",
    "partner_solutions",
    "payment_configuration_update",
    "phone_number_name_update",
    "phone_number_quality_update",
    "security",
    "smb_app_state_sync",
    "smb_message_echoes",
    "standby",
    "template_category_update",
    "template_correct_category_detection",
    "tracking_events",
    "user_preferences",
}


def test_catalog_covers_every_screenshot_field_in_batches_of_ten_ten_ten_three():
    assert set(META_WEBHOOK_FIELD_CATALOG) == EXPECTED_FIELDS
    assert set(META_WEBHOOK_SEMANTIC_CONTRACTS) == EXPECTED_FIELDS
    assert tuple(map(len, META_WEBHOOK_SPRINTS)) == (10, 10, 10, 3)
    assert set().union(*map(set, META_WEBHOOK_SPRINTS)) == EXPECTED_FIELDS

    catalog = meta_webhook_catalog()
    assert len(catalog) == 33
    assert all(item["destination"] for item in catalog)
    assert all(item["description"] for item in catalog)
    assert all(item["operator_action"] for item in catalog)
    assert all(item["observation_state"] == "not_observed" for item in catalog)


def test_catalog_keeps_unobserved_fields_and_rolls_up_observed_fields():
    occurred_at = datetime(2026, 8, 14, 12, tzinfo=UTC)
    catalog = meta_webhook_catalog_with_events(
        [
            {
                "field": "phone_number_quality_update",
                "summary": "Qualidade alterada para amarelo",
                "occurredAt": occurred_at,
            },
        ]
    )
    by_field = {item["field"]: item for item in catalog}
    assert len(by_field) == 33
    assert by_field["phone_number_quality_update"]["observation_state"] == "observed"
    assert by_field["phone_number_quality_update"]["event_count"] == 1
    assert by_field["messages"]["observation_state"] == "not_observed"
    assert by_field["messages"]["event_count"] == 0


def test_catalog_marks_only_existing_domain_handlers_as_native():
    catalog = {item["field"]: item for item in meta_webhook_catalog()}
    expected_native = {
        "history",
        "message_echoes",
        "message_template_components_update",
        "message_template_quality_update",
        "message_template_status_update",
        "messages",
        "phone_number_name_update",
        "phone_number_quality_update",
        "smb_app_state_sync",
        "smb_message_echoes",
        "template_category_update",
        "template_correct_category_detection",
    }
    assert {
        field for field, item in catalog.items() if item["implementation"] == "native"
    } == expected_native
    assert all(
        item["implementation"] in {"native", "domain_projection"}
        for item in catalog.values()
    )
    assert not any(
        item["implementation"] == "semantic_projection" for item in catalog.values()
    )


def test_semantic_projection_retains_safe_facts_without_secrets_or_private_content():
    payload = _safe_operational_payload(
        "security",
        {
            "event": "credential_changed",
            "status": "observed",
            "access_token": "must-not-leak",
            "nested": {"password": "must-not-leak"},
        },
    )
    assert payload["event"] == "credential_changed"
    assert payload["status"] == "observed"
    assert payload["payloadHash"]
    assert "access_token" not in payload
    assert "nested" not in payload

    message_payload = _safe_operational_payload(
        "messages",
        {"messages": [{"text": {"body": "conteúdo privado"}}], "contacts": [{}]},
    )
    assert message_payload == {
        "messageCount": 1,
        "echoCount": 0,
        "statusCount": 0,
        "contactCount": 1,
        "historyBatchCount": 0,
    }


def test_every_registry_field_has_a_typed_handler_and_operator_destination():
    assert set(META_WEBHOOK_REGISTRY) == EXPECTED_FIELDS
    assert set(META_WEBHOOK_HANDLERS) == EXPECTED_FIELDS

    for field in EXPECTED_FIELDS:
        contract = META_WEBHOOK_REGISTRY[field]
        result = META_WEBHOOK_HANDLERS[field](
            {
                "id": f"provider-{field}",
                "status": "observed",
                "payloadHash": f"hash-{field}",
            },
            contract,
        )
        assert result.resource_type == contract.resource_type
        assert result.state_code == "observed"
        assert result.severity == contract.severity
        assert result.snapshot["payloadHash"] == f"hash-{field}"
        assert contract.app_href.startswith("/app/") or contract.app_href.startswith("/whatsapp/")
        assert contract.blackbox_href.startswith("/blackbox/official-whatsapp?tab=webhooks")


async def test_policy_rejects_unknown_severity_before_database_access():
    service = MetaWebhookAutomationPolicyService(db=None)  # type: ignore[arg-type]

    try:
        await service.update(
            workspace_id=None,  # type: ignore[arg-type]
            field="security",
            enabled=True,
            mode="approval_each_time",
            minimum_severity="urgent",
            expected_version=1,
            actor_id=None,  # type: ignore[arg-type]
        )
    except ValueError as exc:
        assert str(exc) == "META_WEBHOOK_AUTOMATION_SEVERITY_INVALID"
    else:  # pragma: no cover - protects the fail-closed contract
        raise AssertionError("invalid severity must fail before persistence")


def test_subscription_retry_only_accepts_transient_provider_failures():
    assert _is_transient_provider_error(
        WhatsAppProviderError(
            code="META_RATE_LIMIT",
            message="rate limited",
            provider_status_code=429,
        )
    )
    assert _is_transient_provider_error(
        WhatsAppProviderError(
            code="META_UNAVAILABLE",
            message="provider unavailable",
            provider_status_code=503,
        )
    )
    assert not _is_transient_provider_error(
        WhatsAppProviderError(
            code="META_PERMISSION_DENIED",
            message="permission denied",
            provider_status_code=403,
        )
    )


def test_receipt_fingerprint_uses_full_sanitized_item_not_lossy_ui_summary():
    first = {
        "messages": [{"id": "wamid.first", "text": {"body": "primeiro conteúdo"}}],
        "contacts": [{}],
    }
    second = {
        "messages": [{"id": "wamid.second", "text": {"body": "segundo conteúdo"}}],
        "contacts": [{}],
    }
    assert _safe_operational_payload("messages", first) == _safe_operational_payload(
        "messages", second
    )
    assert receipt_item_fingerprint(first) != receipt_item_fingerprint(second)


@pytest.mark.asyncio
async def test_native_receipt_only_completes_after_native_domain_handler(
    db_session,
    auth_context,
):
    connection = WhatsAppConnection(
        workspace_id=auth_context.workspace_id,
        connection_type=ConnectionType.TECH_PROVIDER,
        phone_number_id="pn-native-receipt",
        status=WaConnectionStatus.connected,
        webhook_registered=True,
    )
    envelope = OmnichannelProviderWebhookEnvelope(
        provider="meta_whatsapp",
        payload_sha256="a" * 64,
        delivery_fingerprint_sha256="b" * 64,
        raw_payload=b"{}",
        byte_size=2,
        signature_header="sha256=test",
        signature_sha256="c" * 64,
        signature_scheme="x-hub-signature-256",
        signature_verified=True,
        request_metadata={},
        received_at=datetime.now(UTC),
    )
    db_session.add_all([connection, envelope])
    await db_session.flush()
    context = ProviderEnvelopeContext(
        id=envelope.id,
        payload_sha256=envelope.payload_sha256,
        signature_sha256=envelope.signature_sha256,
        received_at=envelope.received_at,
    )
    dispatcher = MetaWebhookDispatcher(db_session)
    receipt = await dispatcher.dispatch(
        workspace_id=auth_context.workspace_id,
        field="messages",
        value={"messages": [{"id": "wamid.native.receipt"}]},
        connection=connection,
        envelope=context,
    )
    assert receipt is not None
    assert receipt.status == "processing"
    assert receipt.processed_at is None

    await dispatcher.complete_native(receipt)

    assert receipt.status == "processed"
    assert receipt.processed_at is not None
