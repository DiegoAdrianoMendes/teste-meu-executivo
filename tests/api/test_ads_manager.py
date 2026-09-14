from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

from app.db.append_only import ImmutableRecordError
from app.models.ads_manager import AdsManagerImportReceipt, AdsMetricObservation
from app.models.klm_operations import AcquisitionEvent

pytestmark = pytest.mark.usefixtures("active_subscription")

_WORKSPACE_TIMEZONE = ZoneInfo("America/Sao_Paulo")


def _workspace_today() -> date:
    return datetime.now(_WORKSPACE_TIMEZONE).date()


async def _connection(client) -> dict:
    suffix = uuid4().hex[-8:]
    response = await client.post(
        "/api/v1/klm/omnichannel/connections",
        json={
            "channel": "facebook",
            "provider": "meta_ads",
            "connectionKey": f"meta-ads:manager-{suffix}",
            "displayName": "Meta Ads Principal",
            "transportMode": "local_fixture",
            "configuration": {"mappingVersion": "v7"},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _snapshot(*, sync_id: str, spend: str, leads: int) -> dict:
    today = _workspace_today().isoformat()
    observed_at = datetime.now(UTC).isoformat()
    return {
        "externalSyncId": sync_id,
        "source": "local_fixture",
        "observedAt": observed_at,
        "metadata": {"fixture": True, "access_token": "must-not-persist"},
        "entities": [
            {
                "entityType": "account",
                "externalId": "act-001",
                "displayName": "Conta Saúde",
                "effectiveStatus": "active",
                "currency": "BRL",
                "timezone": "America/Sao_Paulo",
            },
            {
                "entityType": "campaign",
                "externalId": "campaign-001",
                "parentExternalId": "act-001",
                "displayName": "Negativas de plano de saúde",
                "effectiveStatus": "active",
                "objective": "lead_generation",
                "currency": "BRL",
                "hierarchyPath": ["act-001", "campaign-001"],
                "metadata": {"access_token": "redact-me", "strategy": "lowest_cost"},
                "metrics": [
                    {
                        "metricDate": today,
                        "currency": "BRL",
                        "spend": spend,
                        "impressions": 10000,
                        "reach": 8000,
                        "clicks": 200,
                        "conversations": 20,
                        "comments": 5,
                        "leads": leads,
                        "qualifiedLeads": 4,
                        "opportunities": 3,
                        "contracts": 2,
                        "revenue": "12000.00",
                    }
                ],
            },
            {
                "entityType": "ad_set",
                "externalId": "adset-001",
                "parentExternalId": "campaign-001",
                "displayName": "Oncologia",
                "effectiveStatus": "active",
            },
            {
                "entityType": "ad",
                "externalId": "ad-001",
                "parentExternalId": "adset-001",
                "displayName": "Medicamento negado",
                "effectiveStatus": "active",
            },
            {
                "entityType": "creative",
                "externalId": "creative-001",
                "parentExternalId": "ad-001",
                "displayName": "Vídeo protocolo 24h",
                "effectiveStatus": "active",
            },
            {
                "entityType": "form",
                "externalId": "form-001",
                "parentExternalId": "ad-001",
                "displayName": "Triagem de saúde",
                "effectiveStatus": "active",
                "metadata": {
                    "locale": "pt_BR",
                    "fields": ["full_name", "phone_number", "medicamento"],
                },
            },
        ],
    }


@pytest.mark.asyncio
async def test_ads_manager_import_is_idempotent_hierarchical_and_uses_latest_observation(
    client, db_session
):
    connection = await _connection(client)
    path = (
        f"/api/v1/klm/omnichannel/connections/{connection['id']}"
        "/ads-manager/snapshots"
    )
    first_payload = _snapshot(sync_id="sync-001", spend="1000.00", leads=10)
    first = await client.post(path, json=first_payload)
    replay = await client.post(path, json=first_payload)
    changed = await client.post(
        path,
        json={**first_payload, "metadata": {"fixture": "changed"}},
    )

    assert first.status_code == 201, first.text
    assert first.headers["x-klm-deduplicated"] == "false"
    assert len(first.json()["entities"]) == 6
    assert len(first.json()["observations"]) == 1
    assert replay.status_code == 201, replay.text
    assert replay.headers["x-klm-deduplicated"] == "true"
    assert changed.status_code == 409, changed.text
    assert (
        await db_session.scalar(select(func.count()).select_from(AdsManagerImportReceipt))
        == 1
    )
    assert (
        await db_session.scalar(select(func.count()).select_from(AdsMetricObservation))
        == 1
    )

    entities = await client.get(
        "/api/v1/klm/omnichannel/ads-manager/entities",
        params={"connection_id": connection["id"]},
    )
    assert entities.status_code == 200, entities.text
    assert len(entities.json()) == 6
    campaign = next(item for item in entities.json() if item["entityType"] == "campaign")
    assert campaign["entityMetadata"]["access_token"] == "[REDACTED]"
    assert campaign["entityMetadata"]["strategy"] == "lowest_cost"

    second_payload = _snapshot(sync_id="sync-002", spend="1500.00", leads=12)
    second_payload["entities"] = [
        item for item in second_payload["entities"] if item["entityType"] == "campaign"
    ]
    second = await client.post(path, json=second_payload)
    assert second.status_code == 201, second.text

    summary = await client.get(
        "/api/v1/klm/omnichannel/ads-manager/summary",
        params={
            "date_from": _workspace_today().isoformat(),
            "date_to": _workspace_today().isoformat(),
            "provider": "meta_ads",
        },
    )
    assert summary.status_code == 200, summary.text
    data = summary.json()
    assert data["spend"] == "1500.000000"
    assert data["providerLeads"] == 12
    assert data["clicks"] == 200
    assert data["ctr"] == "2.0000"
    assert data["cpl"] == "125.0000"
    assert data["cac"] == "750.0000"
    assert data["roas"] == "8.0000"
    assert len(data["campaigns"]) == 1


@pytest.mark.asyncio
async def test_ads_manager_summary_joins_actual_captures_and_receipts_are_immutable(
    client, db_session
):
    connection = await _connection(client)
    snapshot = await client.post(
        f"/api/v1/klm/omnichannel/connections/{connection['id']}/ads-manager/snapshots",
        json=_snapshot(sync_id="sync-capture", spend="400.00", leads=4),
    )
    assert snapshot.status_code == 201, snapshot.text
    lead = await client.post(
        f"/api/v1/klm/omnichannel/connections/{connection['id']}/lead-captures/ingest-raw",
        json={
            "rawPayload": {
                "id": "meta-manager-lead-001",
                "form_id": "form-001",
                "campaign_id": "campaign-001",
                "adset_id": "adset-001",
                "ad_id": "ad-001",
                "field_data": [
                    {"name": "full_name", "values": ["Ana Lopes"]},
                    {"name": "phone_number", "values": ["+5511912345678"]},
                ],
            }
        },
    )
    assert lead.status_code == 201, lead.text
    lead_result = lead.json()["receipt"]["result"]
    assert lead_result["acquisition"]["providerReferences"] == {
        "adId": "ad-001",
        "adSetId": "adset-001",
        "campaignId": "campaign-001",
        "formId": "form-001",
    }
    acquisition = await db_session.scalar(
        select(AcquisitionEvent).where(
            AcquisitionEvent.event_id == "meta-manager-lead-001"
        )
    )
    assert acquisition is not None
    assert acquisition.event_metadata["providerReferences"]["campaignId"] == (
        "campaign-001"
    )

    summary = await client.get(
        "/api/v1/klm/omnichannel/ads-manager/summary",
        params={
            "date_from": _workspace_today().isoformat(),
            "date_to": _workspace_today().isoformat(),
            "provider": "meta_ads",
        },
    )
    assert summary.status_code == 200, summary.text
    assert summary.json()["capturedLeads"] == 1
    assert summary.json()["cpl"] == "400.0000"
    assert summary.json()["campaigns"][0]["capturedLeads"] == 1
    assert summary.json()["campaigns"][0]["cpl"] == "400.0000"

    receipt = await db_session.get(
        AdsManagerImportReceipt,
        UUID(snapshot.json()["receipt"]["id"]),
    )
    assert receipt is not None
    receipt.import_metadata = {"tampered": True}
    with pytest.raises(ImmutableRecordError):
        await db_session.flush()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_ads_manager_rejects_invalid_metrics(client):
    connection = await _connection(client)
    payload = _snapshot(sync_id="sync-invalid", spend="-1.00", leads=1)
    response = await client.post(
        f"/api/v1/klm/omnichannel/connections/{connection['id']}/ads-manager/snapshots",
        json=payload,
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_ads_manager_accepts_manual_spend_entry(client, db_session):
    connection = await _connection(client)
    payload = {
        "externalSyncId": "manual:meta:2026-07-23",
        "source": "manual",
        "observedAt": datetime.now(UTC).isoformat(),
        "metadata": {"origin": "manual_entry"},
        "entities": [
            {
                "entityType": "campaign",
                "externalId": "manual-campaign-001",
                "displayName": "Negativas de plano de saúde (manual)",
                "effectiveStatus": "active",
                "currency": "BRL",
                "metrics": [
                    {
                        "metricDate": date.today().isoformat(),
                        "currency": "BRL",
                        "spend": "980.00",
                        "revenue": "9800.00",
                    }
                ],
            }
        ],
    }
    response = await client.post(
        f"/api/v1/klm/omnichannel/connections/{connection['id']}/ads-manager/snapshots",
        json=payload,
    )
    assert response.status_code == 201, response.text
    assert response.json()["receipt"]["source"] == "manual"

    imports = await client.get(
        "/api/v1/klm/omnichannel/ads-manager/imports",
        params={"provider": "meta_ads"},
    )
    assert imports.status_code == 200, imports.text
    assert imports.json()[0]["source"] == "manual"

    receipt = await db_session.scalar(
        select(AdsManagerImportReceipt).where(
            AdsManagerImportReceipt.external_sync_id == "manual:meta:2026-07-23"
        )
    )
    assert receipt is not None
    assert receipt.source == "manual"
