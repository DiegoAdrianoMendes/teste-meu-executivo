"""psid/igsid propagam de `AcquisitionEvent.click_ids` para o envelope de
feedback Meta, espelhando o caminho já existente para `ctwa_clid`."""
from __future__ import annotations

from types import SimpleNamespace

from app.services.acquisition_attribution_service import feedback_click_ids


def _event(click_ids: dict[str, str]) -> SimpleNamespace:
    return SimpleNamespace(click_ids=click_ids)


def test_feedback_click_ids_passes_through_psid():
    result = feedback_click_ids(_event({"psid": "1234567890123456"}), "meta_ads")
    assert result == {"psid": "1234567890123456"}


def test_feedback_click_ids_passes_through_igsid():
    result = feedback_click_ids(_event({"igsid": "9876543210987654"}), "meta_ads")
    assert result == {"igsid": "9876543210987654"}


def test_feedback_click_ids_ignores_psid_for_non_meta_providers():
    result = feedback_click_ids(_event({"psid": "1234567890123456"}), "google_ads")
    assert result == {}
