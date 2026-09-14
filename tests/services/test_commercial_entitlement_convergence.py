from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.models.entitlement import EntitlementKey
from app.schemas.commercial_catalog import (
    COMMERCIAL_ENTITLEMENT_KEYS_V2,
    COMMERCIAL_ENTITLEMENT_KEYS_V3,
    CommercialOfferSnapshotV2Read,
    CommercialOfferSnapshotWrite,
)
from app.services.commercial_catalog_service import (
    CommercialCatalogService,
    _default_offer_requests,
)
from app.services.entitlement_service import (
    ENTITLEMENT_CATALOG,
    FEATURE_ENTITLEMENTS,
    METERED_ENTITLEMENTS,
    QUANTITATIVE_ENTITLEMENTS,
    STOCK_ENTITLEMENTS,
    EntitlementUnit,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _frontend_commercial_entitlement_keys() -> list[str]:
    source = (REPOSITORY_ROOT / "frontend/src/services/platform-commercial-catalog.ts").read_text(
        encoding="utf-8"
    )
    match = re.search(
        r"export const COMMERCIAL_ENTITLEMENT_KEYS\s*=\s*\[(?P<body>.*?)\]\s*as const;",
        source,
        flags=re.DOTALL,
    )
    assert match is not None, "Frontend canonical entitlement export was not found."
    return re.findall(r"['\"]([a-z][a-z0-9_]*)['\"]", match.group("body"))


def test_canonical_entitlement_contract_covers_backend_schema_and_frontend() -> None:
    """One canonical enum must be represented everywhere, with no silent gap."""

    canonical = [item.value for item in EntitlementKey]
    schema_v2 = [item.value for item in COMMERCIAL_ENTITLEMENT_KEYS_V2]
    schema_v3 = [item.value for item in COMMERCIAL_ENTITLEMENT_KEYS_V3]
    frontend = _frontend_commercial_entitlement_keys()
    categories = STOCK_ENTITLEMENTS | METERED_ENTITLEMENTS | FEATURE_ENTITLEMENTS

    assert "contacts" not in schema_v2
    assert schema_v3 == canonical
    assert frontend == canonical
    assert set(ENTITLEMENT_CATALOG) == set(EntitlementKey)
    assert categories == set(EntitlementKey)
    assert QUANTITATIVE_ENTITLEMENTS == STOCK_ENTITLEMENTS | METERED_ENTITLEMENTS
    assert QUANTITATIVE_ENTITLEMENTS.isdisjoint(FEATURE_ENTITLEMENTS)
    assert len(frontend) == len(set(frontend))
    assert len(schema_v3) == len(set(schema_v3))
    assert schema_v3.count("contacts") == 1
    assert (
        ENTITLEMENT_CATALOG[EntitlementKey.contacts].unit
        is EntitlementUnit.contacts
    )
    assert (
        ENTITLEMENT_CATALOG[EntitlementKey.meeting_participants].unit
        is EntitlementUnit.participants
    )


def test_frozen_v2_reader_upgrades_without_rewriting_historical_contacts() -> None:
    current = _default_offer_requests()[0].snapshot
    payload = current.model_dump(mode="python")
    payload["schema_version"] = "2.0"
    payload["entitlements"].pop(EntitlementKey.contacts)

    historical = CommercialOfferSnapshotV2Read.model_validate(payload)
    assert historical.schema_version == "2.0"
    assert EntitlementKey.contacts not in historical.entitlements
    with pytest.raises(ValidationError):
        CommercialOfferSnapshotWrite.model_validate(payload)

    upgraded = CommercialCatalogService._upgrade_snapshot(historical)
    assert upgraded.schema_version == "3.0"
    assert upgraded.entitlements[EntitlementKey.contacts].enabled is False
    assert upgraded.entitlements[EntitlementKey.contacts].limit_quantity is None
