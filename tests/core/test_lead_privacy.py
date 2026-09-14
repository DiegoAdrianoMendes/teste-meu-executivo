from datetime import datetime, timezone
from uuid import uuid4

from app.core.lead_privacy import mask_audit_event
from app.schemas.klm_operations import AuditEventRead


def test_mask_audit_event_never_exposes_free_text_for_unprivileged_reader() -> None:
    event = AuditEventRead(
        id=uuid4(),
        workspace_id=uuid4(),
        actor_user_id=uuid4(),
        actor_type="human",
        actor_label="Operador",
        event_type="task_created",
        entity_type="task",
        entity_id=uuid4(),
        summary="Ligar para Ana Paula no +55 11 99999-1234",
        correlation_id="privacy-regression",
        before_data=None,
        after_data={"description": "ana.ribeiro@example.com"},
        occurred_at=datetime.now(timezone.utc),
    )

    masked = mask_audit_event(event)

    assert masked.summary.startswith("Evento protegido ")
    assert "Ana Paula" not in masked.summary
    assert "99999-1234" not in masked.summary
    assert masked.after_data == {"sensitiveDataHidden": True}


def test_redact_sensitive_metadata_ignores_missing_names() -> None:
    """V37: rótulo de cliente ausente (None) não pode derrubar a redação com 500."""
    from app.core.lead_privacy import redact_sensitive_metadata

    assert (
        redact_sensitive_metadata("Ligar para Ana Paula", (None, "", "  "))
        == "Ligar para Ana Paula"
    )
    assert (
        redact_sensitive_metadata("Ligar para Ana Paula", (None, "Ana Paula"))
        == "Ligar para [nome oculto]"
    )
    assert redact_sensitive_metadata(None, (None,)) is None
    assert redact_sensitive_metadata({"title": "Ana Paula"}, (None, "Ana Paula")) == {
        "title": "[nome oculto]"
    }
