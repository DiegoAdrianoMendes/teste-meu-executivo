from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

from app.services.task_whatsapp_share_service import TaskWhatsAppShareService


def test_contact_gate_requires_recorded_consent_and_open_window_without_exposing_uuid():
    workspace_id = uuid4()
    thread = SimpleNamespace(id=uuid4(), last_inbound_at=datetime.now(UTC))
    identity = SimpleNamespace(display_name="Ana Cliente")
    endpoint = SimpleNamespace(
        consent_state="unknown",
        blocked_at=None,
        address="+55 11 99999-1234",
        normalized_address=None,
    )

    pending = TaskWhatsAppShareService._contact_state(
        thread, identity, endpoint, workspace_id
    )
    assert pending.label == "Ana Cliente"
    assert pending.masked_address == "WhatsApp terminado em 1234"
    assert pending.service_window_state == "open"
    assert pending.send_eligible is False
    assert pending.gate_reasons == ["consentimento_nao_comprovado"]
    assert str(thread.id) not in pending.contact_ref

    endpoint.consent_state = "opted_in"
    eligible = TaskWhatsAppShareService._contact_state(
        thread, identity, endpoint, workspace_id
    )
    assert eligible.send_eligible is True
    assert eligible.gate_reasons == []

    thread.last_inbound_at = datetime.now(UTC) - timedelta(hours=25)
    expired = TaskWhatsAppShareService._contact_state(
        thread, identity, endpoint, workspace_id
    )
    assert expired.service_window_state == "closed"
    assert expired.send_eligible is False
    assert expired.gate_reasons == ["fora_da_janela_de_24_horas"]
