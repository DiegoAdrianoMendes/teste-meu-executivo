"""Operational BottleCRM bridge contracts exposed to the KLM SPA."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from app.models.oss_bridge import ExternalEntitySyncState
from app.services.bottlecrm_operations_service import (
    BottleCRMOperationsService,
    _is_current_matter_link,
    _link_index,
)


def projection(*, linked: bool = True) -> dict:
    return {
        "linked": linked,
        "organizationId": "organization-1",
        "totalLinks": 4 if linked else 0,
        "syncedLinks": 4 if linked else 0,
        "failedLinks": 0,
        "staleLinks": 0,
        "byEntityType": {"lead": 1, "opportunity": 1, "case": 1, "task": 1}
        if linked
        else {},
        "lastSyncedAt": None,
        "sourceOfTruth": "klm",
        "externalExecutionEnabled": False,
    }


@pytest.mark.asyncio
async def test_funnel_context_reads_real_contracts_and_masks_lead_identity() -> None:
    service = object.__new__(BottleCRMOperationsService)
    lead_id = UUID("9c1e9fe2-5745-4d96-ac87-8da52356dd9a")
    external_link = SimpleNamespace(
        external_entity_id="lead-1",
        external_entity_type="lead",
        klm_entity_id=lead_id,
        klm_entity_type="client",
        sync_state=ExternalEntitySyncState.synced,
    )
    service._projection = AsyncMock(return_value=(projection(), [external_link]))
    service.client = SimpleNamespace(
        read_payload=AsyncMock(
            side_effect=[
                {
                    "accounts_count": 1,
                    "contacts_count": 8,
                    "leads_count": 8,
                    "opportunities_count": 8,
                    "urgent_counts": {"overdue_tasks": 2, "hot_leads": 3},
                    "pipeline_by_stage": {
                        "PROPOSAL": {"label": "Proposta", "count": 2, "value": 12500}
                    },
                    "revenue_metrics": {
                        "weighted_pipeline": 9000,
                        "conversion_rate": 37.5,
                        "currency": "BRL",
                    },
                },
                {"columns": []},
                {
                    "columns": [
                        {
                            "id": "new",
                            "name": "Novo",
                            "order": 0,
                            "lead_count": 1,
                            "leads": [
                                {
                                    "id": "lead-1",
                                    "full_name": "Nome protegido",
                                    "status": "assigned",
                                    "rating": "HOT",
                                }
                            ],
                        }
                    ]
                },
                {"columns": []},
                {"goals": [], "goals_count": 0},
                [{"stage": "PROPOSAL", "expected_days": 7, "warning_days": 5}],
                {"count": 2, "unread_count": 2, "results": []},
            ]
        )
    )

    result = await service.funnel_context(
        UUID("645e1341-a1ae-452c-8c99-884104dd06eb"),
        include_sensitive=False,
    )

    assert result["counts"] == {
        "accounts": 1,
        "contacts": 8,
        "leads": 8,
        "opportunities": 8,
    }
    assert result["pipeline"] == [
        {"id": "PROPOSAL", "label": "Proposta", "count": 2, "value": 12500.0}
    ]
    assert result["leadColumns"][0]["items"][0]["name"] == "Lead restrito"
    assert result["leadColumns"][0]["items"][0]["klmEntityId"] == str(lead_id)
    assert result["agingConfig"][0]["expectedDays"] == 7
    assert result["notificationsUnread"] == 2
    assert result["sensitiveDataHidden"] is True


def test_stale_links_are_not_mapped_back_to_active_klm_entities() -> None:
    synced = SimpleNamespace(
        external_entity_id="contact-live",
        external_entity_type="contact",
        sync_state=ExternalEntitySyncState.synced,
    )
    stale = SimpleNamespace(
        external_entity_id="contact-orphan",
        external_entity_type="contact",
        sync_state=ExternalEntitySyncState.stale,
    )

    assert _link_index([synced, stale]) == {"contact-live": synced}


def test_matter_context_excludes_stale_client_and_task_links() -> None:
    matter_id = UUID("40c81ee4-b61a-4935-868f-91a1ad7f11f8")
    client_id = UUID("97e8b2fc-80f0-4bec-b36f-082d7387b3aa")
    task_id = UUID("dfd4ebdf-022b-4a69-832c-48c1931a4adc")

    for entity_type, entity_id in (("client", client_id), ("task", task_id)):
        stale = SimpleNamespace(
            klm_entity_type=entity_type,
            klm_entity_id=entity_id,
            sync_state=ExternalEntitySyncState.stale,
        )
        assert not _is_current_matter_link(
            stale,
            matter_id=matter_id,
            client_id=client_id,
            task_ids={task_id},
        )

    synced_client = SimpleNamespace(
        klm_entity_type="client",
        klm_entity_id=client_id,
        sync_state=ExternalEntitySyncState.synced,
    )
    assert _is_current_matter_link(
        synced_client,
        matter_id=matter_id,
        client_id=client_id,
        task_ids={task_id},
    )


@pytest.mark.asyncio
async def test_unlinked_funnel_does_not_read_foreign_sidecar_records() -> None:
    service = object.__new__(BottleCRMOperationsService)
    service._projection = AsyncMock(return_value=(projection(linked=False), []))
    service.client = SimpleNamespace(read_payload=AsyncMock())

    result = await service.funnel_context(
        UUID("645e1341-a1ae-452c-8c99-884104dd06eb"),
        include_sensitive=True,
    )

    assert result["projection"]["linked"] is False
    assert result["opportunityColumns"] == []
    service.client.read_payload.assert_not_awaited()


@pytest.mark.asyncio
async def test_finance_context_normalizes_brl_and_hides_client_titles() -> None:
    service = object.__new__(BottleCRMOperationsService)
    service._projection = AsyncMock(return_value=(projection(), []))
    service.client = SimpleNamespace(
        read_payload=AsyncMock(
            side_effect=[
                {
                    "summary": {
                        "total_invoiced": "10000.50",
                        "total_paid": "6000.25",
                        "total_due": "4000.25",
                    },
                    "status_counts": {"Sent": 1},
                    "overdue": {"count": 1, "amount": "2500.25"},
                    "recent_activity": {
                        "revenue_30d": "6000.25",
                        "invoiced_30d": "10000.50",
                        "invoices_created_30d": 1,
                        "invoices_paid_30d": 1,
                    },
                    "estimates": {"pending": 1, "accepted": 0, "declined": 0},
                },
                {
                    "count": 1,
                    "results": [
                        {
                            "id": "invoice-1",
                            "invoice_title": "Honorários de Maria",
                            "invoice_number": "INV-1",
                            "status": "Sent",
                            "total_amount": "10000.50",
                            "amount_due": "4000.25",
                            "currency": "BRL",
                        }
                    ],
                },
                {"count": 0, "results": []},
                {"count": 0, "results": []},
                {
                    "current": {"count": 0, "amount": "0"},
                    "1_30_days": {"count": 1, "amount": "2500.25"},
                    "31_60_days": {"count": 0, "amount": "0"},
                    "61_90_days": {"count": 0, "amount": "0"},
                    "over_90_days": {"count": 0, "amount": "0"},
                },
                {"data": [{"period": "2026-07-01", "revenue": "6000.25", "count": 1}]},
                {"count": 0, "unread_count": 0, "results": []},
            ]
        )
    )

    result = await service.finance_context(
        UUID("645e1341-a1ae-452c-8c99-884104dd06eb"),
        include_sensitive=False,
    )

    assert result["summary"]["total_invoiced"] == 10000.5
    assert result["invoices"][0]["name"] == "Fatura restrita"
    assert result["invoices"][0]["amountDue"] == 4000.25
    assert result["aging"][1] == {
        "bucket": "1_30_days",
        "count": 1,
        "amount": 2500.25,
    }
    assert result["revenueSeries"][0]["revenue"] == 6000.25
