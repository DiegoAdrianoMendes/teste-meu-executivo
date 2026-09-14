"""V31: o esquema do rascunho aceita a grafia da Meta (UTILITY) e normaliza para o literal."""

import pytest
from pydantic import ValidationError

from app.schemas.whatsapp_campaign import TemplateDraftUpdate


@pytest.mark.parametrize("raw", ["UTILITY", " Utility ", "utility"])
def test_update_normalizes_category_case(raw: str) -> None:
    assert TemplateDraftUpdate(category=raw).category == "utility"


def test_update_still_rejects_unknown_category() -> None:
    with pytest.raises(ValidationError):
        TemplateDraftUpdate(category="PROMOTIONAL")
