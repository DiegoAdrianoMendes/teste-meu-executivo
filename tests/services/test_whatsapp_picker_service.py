from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from starlette.requests import Request

from app.models.whatsapp import (
    WaConnectionStatus,
    WaMessageDirection,
    WhatsAppConnection,
    WhatsAppContact,
    WhatsAppMessage,
)
from app.models.whatsapp_campaign import (
    CampaignRecipientStatus,
    CampaignStatus,
    MetaTemplateStatus,
    WhatsAppCampaign,
    WhatsAppCampaignRecipient,
    WhatsAppMetaTemplate,
    WhatsAppTemplateIntent,
)
from app.services.whatsapp_picker_service import WhatsAppPickerService


async def _connection(db_session, workspace_id):
    connection = WhatsAppConnection(
        workspace_id=workspace_id,
        phone_number_id=f"phone-{uuid4().hex[:12]}",
        status=WaConnectionStatus.connected,
    )
    db_session.add(connection)
    await db_session.flush()
    return connection


async def _template(
    db_session, workspace_id, connection_id, *, intent, status, raw_payload=None, components=None
):
    template = WhatsAppMetaTemplate(
        workspace_id=workspace_id,
        whatsapp_connection_id=connection_id,
        name=f"template_{uuid4().hex[:8]}",
        external_template_id=f"meta-{uuid4().hex[:8]}",
        language="pt_BR",
        category="UTILITY",
        intent=intent,
        status=status,
        components=components
        or [
            {"type": "BODY", "text": "Olá {{1}}, seu caso está atualizado."},
            {
                "type": "BUTTONS",
                "buttons": [{"type": "QUICK_REPLY", "text": "Falar com o escritório"}],
            },
        ],
        raw_payload=raw_payload or {},
    )
    db_session.add(template)
    await db_session.flush()
    return template


@pytest.mark.asyncio
async def test_picker_preserves_prompt_but_promotes_explicit_field_bindings(
    db_session, auth_context
):
    connection = await _connection(db_session, auth_context.workspace_id)
    await _template(
        db_session,
        auth_context.workspace_id,
        connection.id,
        intent=WhatsAppTemplateIntent.audiencia,
        status=MetaTemplateStatus.approved,
        components=[
            {"type": "HEADER", "format": "TEXT", "text": "Olá {{1}}"},
            {"type": "BODY", "text": "Caso {{1}}: {{2}}"},
            {
                "type": "BUTTONS",
                "buttons": [{"type": "URL", "text": "Abrir", "url": "https://x/{{1}}"}],
            },
        ],
        raw_payload={
            "variableBindings": [
                {"scope": "header", "position": 1, "fieldKey": "contact.first_name"},
                {"scope": "body", "position": 1, "fieldKey": "matter.case_number"},
                {"scope": "button", "position": 0, "fieldKey": "matter.case_number"},
            ]
        },
    )

    card = (await WhatsAppPickerService(db_session).picker(auth_context.workspace_id)).cards[0]
    bindings = {
        (variable.scope, variable.position): variable.binding for variable in card.variables
    }
    assert bindings[("header", 1)].kind == "field"
    assert bindings[("header", 1)].field_key == "contact.first_name"
    assert bindings[("body", 1)].kind == "field"
    assert bindings[("body", 1)].field_key == "matter.case_number"
    assert bindings[("body", 2)].kind == "prompt"
    assert bindings[("button", 0)].kind == "field"
    assert bindings[("button", 0)].field_key == "matter.case_number"


@pytest.mark.asyncio
async def test_picker_buttons_use_real_zero_based_indexes_and_keep_url_examples(
    db_session, auth_context
):
    connection = await _connection(db_session, auth_context.workspace_id)
    await _template(
        db_session,
        auth_context.workspace_id,
        connection.id,
        intent=WhatsAppTemplateIntent.proposta,
        status=MetaTemplateStatus.approved,
        components=[
            {"type": "BODY", "text": "Confira os links"},
            {
                "type": "BUTTONS",
                "buttons": [
                    {"type": "QUICK_REPLY", "text": "Falar"},
                    {
                        "type": "URL",
                        "text": "Caso",
                        "url": "https://x/caso/{{1}}",
                        "example": ["case-123"],
                    },
                    {
                        "type": "URL",
                        "text": "Proposta",
                        "url": "https://x/proposta/{{1}}",
                        "example": ["proposal-456"],
                    },
                ],
            },
        ],
    )

    card = (await WhatsAppPickerService(db_session).picker(auth_context.workspace_id)).cards[0]
    buttons = [variable for variable in card.variables if variable.scope == "button"]
    assert [(variable.position, variable.example) for variable in buttons] == [
        (1, "case-123"),
        (2, "proposal-456"),
    ]


@pytest.mark.asyncio
async def test_picker_returns_only_approved_normalized_cards_and_30_day_usage(
    db_session, auth_context
):
    connection = await _connection(db_session, auth_context.workspace_id)
    approved = await _template(
        db_session,
        auth_context.workspace_id,
        connection.id,
        intent=WhatsAppTemplateIntent.proposta,
        status=MetaTemplateStatus.approved,
    )
    await _template(
        db_session,
        auth_context.workspace_id,
        connection.id,
        intent=WhatsAppTemplateIntent.documentos,
        status=MetaTemplateStatus.pending,
    )
    campaign = WhatsAppCampaign(
        workspace_id=auth_context.workspace_id,
        whatsapp_connection_id=connection.id,
        title="picker test",
        status=CampaignStatus.running,
    )
    db_session.add(campaign)
    await db_session.flush()
    recipient = WhatsAppCampaignRecipient(
        campaign_id=campaign.id,
        workspace_id=auth_context.workspace_id,
        template_id=approved.id,
        phone="5511999999999",
        status=CampaignRecipientStatus.sent,
        created_at=datetime.now(UTC) - timedelta(days=2),
        sent_at=datetime.now(UTC) - timedelta(days=2),
    )
    db_session.add(recipient)
    await db_session.flush()

    picker = await WhatsAppPickerService(db_session).picker(auth_context.workspace_id)

    assert [card.id for card in picker.cards] == [approved.id]
    assert picker.cards[0].body_text == "Olá {{1}}, seu caso está atualizado."
    assert picker.cards[0].buttons[0].label == "Falar com o escritório"
    assert picker.cards[0].usage_count30d == 1
    assert picker.sector_mode == "condensed"


@pytest.mark.asyncio
async def test_picker_exposes_pending_templates_only_in_sequence_catalog(
    db_session, auth_context
):
    connection = await _connection(db_session, auth_context.workspace_id)
    pending = await _template(
        db_session,
        auth_context.workspace_id,
        connection.id,
        intent=WhatsAppTemplateIntent.follow_up,
        status=MetaTemplateStatus.pending,
        raw_payload={"isSequence": True, "sequenceStepCount": 2},
    )

    picker = await WhatsAppPickerService(db_session).picker(auth_context.workspace_id)

    assert pending.id not in {card.id for card in picker.cards}
    assert pending.id in {card.id for card in picker.sequence_templates}
    sequence_card = next(card for card in picker.sequence_templates if card.id == pending.id)
    assert sequence_card.status == "pending"


@pytest.mark.asyncio
async def test_picker_suggestion_requires_reliable_cold_conversation_context(
    db_session, auth_context
):
    connection = await _connection(db_session, auth_context.workspace_id)
    template = await _template(
        db_session,
        auth_context.workspace_id,
        connection.id,
        intent=WhatsAppTemplateIntent.reativacao,
        status=MetaTemplateStatus.approved,
    )
    contact = WhatsAppContact(
        workspace_id=auth_context.workspace_id,
        whatsapp_connection_id=connection.id,
        phone="5511888888888",
        name="Cliente Frio",
    )
    db_session.add(contact)
    await db_session.flush()
    db_session.add(
        WhatsAppMessage(
            contact_id=contact.id,
            whatsapp_connection_id=connection.id,
            direction=WaMessageDirection.inbound,
            body="Oi",
            timestamp=datetime.now(UTC) - timedelta(days=5),
        )
    )
    await db_session.flush()

    picker = await WhatsAppPickerService(db_session).picker(
        auth_context.workspace_id, thread_id=contact.id
    )

    assert picker.suggestions
    assert picker.suggestions[0].template_id == template.id
    assert picker.suggestions[0].reason_code == "conversation_cold"
    assert "sem resposta" in picker.suggestions[0].reason_text


@pytest.mark.asyncio
async def test_variables_keep_unknown_values_null(db_session, auth_context):
    variables = await WhatsAppPickerService(db_session).variables(
        auth_context.workspace_id, contact_id=None, matter_id=None
    )
    fields = [
        field for group in variables.groups if group.key != "office" for field in group.values
    ]
    assert fields
    assert all(field.resolved_value is None for field in fields)


@pytest.mark.asyncio
async def test_quick_send_accepts_conversation_payload_and_reuses_campaign_path(
    db_session, auth_context, monkeypatch
):
    from app.api.v1.endpoints import whatsapp_campaigns as endpoint
    from app.schemas.whatsapp_campaign import WhatsAppQuickSendCreate

    contact = SimpleNamespace(id=uuid4())
    calls: dict[str, object] = {}
    campaign_by_key: dict[str, object] = {}

    class Picker:
        db = db_session

        async def _resolve_context(self, *_args, **_kwargs):
            return contact, None

    class Service:
        async def create_campaign(self, _workspace_id, payload, _user_id, **kwargs):
            calls["create"] = payload
            calls["idempotency_key"] = kwargs.get("idempotency_key")
            key = kwargs.get("idempotency_key")
            campaign_by_key.setdefault(key, SimpleNamespace(id=uuid4()))
            return campaign_by_key[key]

        async def launch_campaign(self, _workspace_id, campaign_id, payload, **_kwargs):
            calls["launch"] = (campaign_id, payload)
            return SimpleNamespace(id=campaign_id, status="running")

    monkeypatch.setattr(endpoint, "external_delivery_allowed", lambda: True)
    payload = WhatsAppQuickSendCreate.model_validate(
        {
            "threadId": str(uuid4()),
            "matterId": None,
            "templateId": str(uuid4()),
            "bindings": {
                "header_1": "Cliente",
                "{{1}}": "Caso",
                "{{2}}": "03/09",
                "button_1": "suffix",
            },
            "connectionId": str(uuid4()),
            "idempotencyKey": "quick-send-test-001",
        }
    )

    result = await endpoint.quick_send(
        payload,
        request=Request({"type": "http", "headers": []}),
        svc=Service(),
        picker=Picker(),
        workspace_id=auth_context.workspace_id,
        current_user_id=auth_context.user_id,
        can_view_sensitive=True,
    )

    created = calls["create"]
    assert result.status == "running"
    assert created.audience_filter.contact_ids == [contact.id]
    assert created.header_parameters == ["Cliente"]
    assert created.body_parameters == ["Caso", "03/09"]
    assert created.button_parameters == ["suffix"]
    assert calls["idempotency_key"] == "quick-send-test-001"
    assert calls["launch"][1].dry_run is False

    retry = await endpoint.quick_send(
        payload,
        request=Request({"type": "http", "headers": []}),
        svc=Service(),
        picker=Picker(),
        workspace_id=auth_context.workspace_id,
        current_user_id=auth_context.user_id,
        can_view_sensitive=True,
    )
    assert retry.id == result.id
