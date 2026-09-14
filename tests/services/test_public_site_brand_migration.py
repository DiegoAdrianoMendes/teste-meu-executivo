from __future__ import annotations

from copy import deepcopy

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.public_site_defaults import get_default_public_site_documents
from app.models.public_site_document import PublicSiteDocument
from app.services.public_site_service import PublicSiteService


@pytest.mark.asyncio
async def test_existing_public_documents_are_rebranded_without_losing_custom_content(
    db_session: AsyncSession,
) -> None:
    settings = deepcopy(get_default_public_site_documents()["settings"])
    content = settings["content"]
    content["brandName"] = "Desburocratize"
    content["supportEmail"] = "contato@desburocratize.com"
    content["contactEmail"] = "contato@desburocratize.com"
    content["tagline"] = "Texto customizado da Desburocratize"
    content["whatsappUrl"] = "https://wa.me/5562999999999"
    document = PublicSiteDocument(
        key="settings",
        title=settings["title"],
        description=settings["description"],
        draft_content=deepcopy(content),
        published_content=deepcopy(content),
        version=7,
    )
    db_session.add(document)
    await db_session.commit()

    await PublicSiteService(db_session).ensure_defaults()
    await db_session.refresh(document)

    assert document.version == 8
    assert document.draft_content["brandName"] == "Meu Executivo"
    assert document.published_content["supportEmail"] == "contato@klmadvogados.com"
    assert document.published_content["whatsappUrl"] == "https://wa.me/5561993328108"
    assert document.published_content["tagline"] == "Texto customizado do Meu Executivo"


def test_brand_normalizer_is_recursive_and_idempotent() -> None:
    legacy = {
        "title": "DESBUROCRATIZE",
        "sections": [{"body": "Na Desburocratize, os dados ficam organizados."}],
    }

    normalized = PublicSiteService._normalize_legacy_brand(legacy)

    assert normalized == {
        "title": "MEU EXECUTIVO",
        "sections": [{"body": "No Meu Executivo, os dados ficam organizados."}],
    }
    assert PublicSiteService._normalize_legacy_brand(normalized) == normalized


def test_brand_normalizer_migrates_already_normalized_klm_hub_content() -> None:
    """Bancos já rebrandeados para "KLM Hub" (antes deste rebrand) também
    devem convergir para "Meu Executivo" — idempotente nos dois sentidos."""
    already_klm = {
        "title": "KLM HUB",
        "sections": [{"body": "No KLM Hub, os dados ficam organizados."}],
    }

    normalized = PublicSiteService._normalize_legacy_brand(already_klm)

    assert normalized == {
        "title": "MEU EXECUTIVO",
        "sections": [{"body": "No Meu Executivo, os dados ficam organizados."}],
    }
    assert PublicSiteService._normalize_legacy_brand(normalized) == normalized
