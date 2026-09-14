"""V29: o sync com a Meta não pode apagar o motivo da rejeição nem as chaves locais."""

from __future__ import annotations

from app.services.whatsapp.campaign_service import _merge_synced_raw_payload


def test_meta_none_keeps_reason_recorded_by_webhook() -> None:
    previous = {
        "source": "meta_sync",
        "rejected_reason": "INVALID_FORMAT",
        "last_status_event": "REJECTED",
    }
    item = {
        "id": "1",
        "name": "t",
        "language": "pt_BR",
        "status": "REJECTED",
        "rejected_reason": "NONE",
    }

    merged = _merge_synced_raw_payload(previous, item)

    assert merged["rejected_reason"] == "INVALID_FORMAT"
    assert merged["last_status_event"] == "REJECTED"
    assert merged["status"] == "REJECTED"
    assert merged["source"] == "meta_sync"


def test_meta_missing_reason_keeps_previous_and_real_reason_overrides() -> None:
    previous = {"rejected_reason": "INVALID_FORMAT"}
    for item in (
        {"id": "1", "status": "REJECTED"},
        {"id": "1", "status": "REJECTED", "rejected_reason": ""},
        {"id": "1", "status": "rejected", "rejected_reason": "none"},
    ):
        assert _merge_synced_raw_payload(previous, item)["rejected_reason"] == "INVALID_FORMAT"

    overridden = _merge_synced_raw_payload(
        previous, {"id": "1", "status": "REJECTED", "rejected_reason": "ABUSIVE_CONTENT"}
    )
    assert overridden["rejected_reason"] == "ABUSIVE_CONTENT"


def test_local_keys_survive_and_source_never_stays_local_draft() -> None:
    previous = {
        "source": "local_draft",
        "utility_catalog": {"key": "confirmacao_consulta", "version": 1},
        "builder_components": {"body": "Olá {{1}}"},
        "submitted_at": "2026-08-10T20:00:00Z",
        "variableBindings": {"1": "client.name"},
        "ai_analysis": {"risk": "low"},
    }
    item = {"id": "9", "name": "confirmacao_consulta", "language": "pt_BR", "status": "APPROVED"}

    merged = _merge_synced_raw_payload(previous, item)

    assert merged["utility_catalog"] == {"key": "confirmacao_consulta", "version": 1}
    assert merged["builder_components"] == {"body": "Olá {{1}}"}
    assert merged["submitted_at"] == "2026-08-10T20:00:00Z"
    assert merged["variableBindings"] == {"1": "client.name"}
    assert merged["ai_analysis"] == {"risk": "low"}
    assert merged["source"] == "meta_sync"
    assert merged["id"] == "9" and merged["status"] == "APPROVED"
    assert "rejected_reason" not in merged


def test_no_previous_payload_is_the_meta_item_plus_source() -> None:
    merged = _merge_synced_raw_payload(None, {"id": "3", "rejected_reason": "NONE"})
    assert merged == {"id": "3", "source": "meta_sync"}


def test_reason_does_not_survive_approval_or_pending() -> None:
    previous = {"rejected_reason": "INVALID_FORMAT", "last_status_event": "REJECTED"}
    for status in ("APPROVED", "PENDING", "PAUSED"):
        merged = _merge_synced_raw_payload(
            previous, {"id": "1", "status": status, "rejected_reason": "NONE"}
        )
        assert "rejected_reason" not in merged, status
