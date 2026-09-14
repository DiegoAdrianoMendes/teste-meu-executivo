from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models.whatsapp_campaign import MetaTemplateStatus
from app.schemas.flow import validate_flow_graph
from app.services.flow_sequence_service import (
    hold_or_skip_pending_template,
    interrupt_sequences_on_inbound,
    normalize_sequence_delay,
    resolve_sequence_message,
    sequence_tariff,
)
from app.workers import flow_tasks


def test_sequence_delay_accepts_hours_and_days_and_legacy_hours():
    assert normalize_sequence_delay({"delay_value": 2, "delay_unit": "hours"}) == (2.0, "hours")
    assert normalize_sequence_delay({"delay_value": 1, "delay_unit": "days"}) == (1.0, "days")
    assert normalize_sequence_delay({"delay_hours": 24}) == (24.0, "hours")
    assert normalize_sequence_delay({"send_now": True}) == (0.0, "hours")


def test_sequence_bindings_resolve_only_declared_field_keys():
    config = {
        "bindings": [{"variable": "name", "fieldKey": "lead.name"}],
    }
    assert resolve_sequence_message("Oi {name}", config, {"lead": {"name": "Ana"}}) == "Oi Ana"
    with pytest.raises(ValueError, match="sem valor"):
        resolve_sequence_message("Oi {name}", config, {"lead": {}})


def test_missing_tariff_is_unverified_without_a_default_amount():
    assert sequence_tariff({}) == {"status": "unverified", "amount": None}
    assert sequence_tariff({"tariff": {"amount": 12.5}}) == {
        "status": "verified",
        "amount": 12.5,
    }


def test_scheduled_sequence_requires_template_status_and_field_bindings():
    base = {
        "id": "stage-1",
        "type": "scheduled_message",
        "config": {
            "sequence_stage": True,
            "template_name": "follow_up",
            "template_status": "approved",
            "bindings": [{"variable": "name", "fieldKey": "lead.name"}],
            "delay_value": 2,
            "delay_unit": "hours",
            "message": "Oi {name}",
        },
    }
    validate_flow_graph([base], [])

    missing_binding = {**base, "config": {**base["config"], "bindings": []}}
    with pytest.raises(ValueError, match="bindings"):
        validate_flow_graph([missing_binding], [])

    bad_unit = {**base, "config": {**base["config"], "delay_unit": "minutes"}}
    with pytest.raises(ValueError, match="delay_unit"):
        validate_flow_graph([bad_unit], [])


def test_static_sequence_template_allows_empty_bindings():
    static_stage = {
        "id": "static-stage",
        "type": "scheduled_message",
        "config": {
            "sequence_stage": True,
            "template_name": "static_follow_up",
            "template_status": "approved",
            "bindings": [],
            "delay_value": 2,
            "delay_unit": "hours",
            "message": "Lembrete sem variáveis",
        },
    }
    validate_flow_graph([static_stage], [])

    extra_binding = {
        **static_stage,
        "config": {
            **static_stage["config"],
            "bindings": [{"variable": "name", "fieldKey": "lead.name"}],
        },
    }
    with pytest.raises(ValueError, match="bindings incompatíveis"):
        validate_flow_graph([extra_binding], [])


@pytest.mark.asyncio
async def test_pending_template_notifications_are_deduplicated_per_scheduled_stage():
    workspace_id = uuid4()
    owner_id = uuid4()
    matter_id = uuid4()
    now = datetime.now(UTC)

    class FakeDb:
        def __init__(self):
            self.added = []

        async def scalar(self, statement):
            sql = str(statement)
            if "klm_audit_events" in sql:
                return None
            if "matters.responsible_human_id" in sql:
                return owner_id
            if "notifications" in sql:
                params = statement.compile().params
                title = next(
                    (
                        value
                        for value in params.values()
                        if isinstance(value, str) and value.startswith("Etapa de sequência")
                    ),
                    None,
                )
                for item in self.added:
                    if (
                        item.__class__.__name__ == "Notification"
                        and item.user_id == owner_id
                        and item.title == title
                    ):
                        return item.id
            return None

        def add(self, value):
            if value.__class__.__name__ == "Notification" and value.id is None:
                value.id = uuid4()
            self.added.append(value)

        async def flush(self):
            return None

    db = FakeDb()
    run = SimpleNamespace(id=uuid4(), matter_id=matter_id, context={})

    def pending_message():
        return SimpleNamespace(
            id=uuid4(),
            workspace_id=workspace_id,
            matter_id=matter_id,
            pending_grace_until=now - timedelta(hours=1),
            pending_retry_at=now - timedelta(hours=1),
            status="pending",
            error_message=None,
            lease_token="lease",
            lease_expires_at=now,
            stage_id="stage",
            template_name="follow_up",
            template_status="pending",
            tariff_status="unverified",
        )

    first = pending_message()
    second = pending_message()
    assert await hold_or_skip_pending_template(db, first, run, now=now) == "skipped"
    assert await hold_or_skip_pending_template(db, first, run, now=now) == "skipped"
    assert await hold_or_skip_pending_template(db, second, run, now=now) == "skipped"
    notifications = [
        item for item in db.added if item.__class__.__name__ == "Notification"
    ]
    assert len(notifications) == 2
    assert notifications[0].title != notifications[1].title


def test_sequence_rejects_non_body_template_bindings():
    stage = {
        "id": "header-stage",
        "type": "scheduled_message",
        "config": {
            "sequence_stage": True,
            "template_name": "follow_up_with_header",
            "template_status": "approved",
            "bindings": [
                {
                    "variable": "name",
                    "fieldKey": "lead.name",
                    "scope": "header",
                }
            ],
            "delay_value": 1,
            "delay_unit": "hours",
            "message": "Olá {name}",
        },
    }
    with pytest.raises(ValueError, match="só suporta bindings do body"):
        validate_flow_graph([stage], [])


@pytest.mark.asyncio
async def test_template_dispatch_uses_official_endpoint_and_persisted_binding(monkeypatch):
    monkeypatch.setattr(flow_tasks, "external_delivery_allowed", lambda: True)
    template = SimpleNamespace(
        id=uuid4(),
        workspace_id=uuid4(),
        name="follow_up",
        language="pt_BR",
        status=MetaTemplateStatus.approved,
        removed_at_source_at=None,
        whatsapp_connection_id=uuid4(),
    )

    class FakeDb:
        async def scalar(self, _statement):
            return template

    class FakeMeta:
        def __init__(self):
            self.calls = []

        async def send_template_message(self, **kwargs):
            self.calls.append(kwargs)
            return {"messages": [{"id": "wamid.template"}]}

    fake_meta = FakeMeta()
    connection = SimpleNamespace(
        id=template.whatsapp_connection_id,
        phone_number_id="phone-number-id",
    )
    fake_service = SimpleNamespace(
        meta_client=fake_meta,
        message_service=SimpleNamespace(
            _resolve_outbound_connection=lambda **_: _resolved(connection)
        ),
        connection_service=SimpleNamespace(
            resolve_meta_access_token=lambda _connection: _token(),
        ),
    )

    async def _resolved(value):
        return value

    async def _token():
        return "token"

    monkeypatch.setattr(
        "app.services.whatsapp_service.WhatsAppService",
        lambda _db: fake_service,
    )
    checkpoint = []

    async def before_dispatch(supports_idempotency):
        checkpoint.append(supports_idempotency)

    receipt = await flow_tasks._dispatch_whatsapp_template_with_protocol(
        template.workspace_id,
        "+5511999999999",
        FakeDb(),
        template_id=template.id,
        template_name=template.name,
        template_language=template.language,
        bindings=[{"variable": "name", "fieldKey": "lead.name"}],
        context={"lead": {"name": "Ana"}},
        connection_id=template.whatsapp_connection_id,
        before_dispatch=before_dispatch,
    )

    assert receipt.provider_reference == "wamid.template"
    assert checkpoint == [False]
    assert fake_meta.calls[0]["template_name"] == "follow_up"
    assert fake_meta.calls[0]["body_parameters"] == ["Ana"]


@pytest.mark.asyncio
async def test_interrupt_on_reply_cancels_only_interruptible_sequence_rows():
    message = SimpleNamespace(
        id=uuid4(),
        status="pending",
        contact_phone="+5511999999999",
        interrupt_on_reply=True,
        lease_token="lease",
        lease_expires_at=object(),
        error_message=None,
        stage_id="stage-1",
    )
    run = SimpleNamespace(
        id=uuid4(),
        status="active",
        completed_at=None,
        paused_at=None,
        pause_reason=None,
        context={},
    )

    class FakeRows:
        def all(self):
            return [(message, run)]

    class FakeDb:
        def __init__(self):
            self.added = []

        async def execute(self, _statement):
            return FakeRows()

        async def scalar(self, _statement):
            return None

        def add(self, value):
            self.added.append(value)

        async def flush(self):
            return None

    db = FakeDb()
    interrupted = await interrupt_sequences_on_inbound(
        db,
        workspace_id=uuid4(),
        phone=message.contact_phone,
        inbound_message_id=uuid4(),
    )

    assert interrupted == 1
    assert message.status == "cancelled"
    assert run.status == "cancelled"
    assert db.added[0].event_type == "flow_sequence_interrupted_on_inbound"
