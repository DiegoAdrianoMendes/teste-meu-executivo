"""Focused contracts for the BottleCRM projection."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from app.models.oss_bridge import ExternalEntitySyncState
from app.services import bottlecrm_sync_service as bottlecrm_sync_module
from app.services.bottlecrm_client import (
    BottleCRMClient,
    BottleCRMClientError,
    BottleCRMNotFoundError,
)
from app.services.bottlecrm_sync_service import (
    BottleCRMSyncService,
    _bounded_label,
    _digest,
    _opportunity_probability,
)
from app.services.oss_bridge_service import ExternalEntityLinkConflictError


def test_bottlecrm_case_label_respects_upstream_limit_and_keeps_identity() -> None:
    entity_id = UUID("3c20e4c7-d8f3-4336-93af-733d4a7b9069")

    label = _bounded_label(
        "Cliente com nome deliberadamente longo · Negativa de tratamento complexo",
        entity_id,
        64,
    )

    assert len(label) <= 64
    assert label.endswith("· KLM 3c20e4c7")


def test_zero_score_projection_uses_bottlecrm_stage_probability_without_changing_klm() -> None:
    matter = SimpleNamespace(lead_score=0)

    assert _opportunity_probability(matter, "PROSPECTING") == 10
    assert _opportunity_probability(matter, "CLOSED_LOST") == 0

    matter.lead_score = 63
    assert _opportunity_probability(matter, "PROPOSAL") == 63


@pytest.mark.asyncio
async def test_bottlecrm_account_recovery_reads_open_accounts_contract() -> None:
    client = object.__new__(BottleCRMClient)
    client._request = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "active_accounts": {
                "open_accounts": [
                    {"id": "account-1", "name": "Operadora KLM"},
                ]
            }
        }
    )

    assert await client.find_account("Operadora KLM") == {
        "id": "account-1",
        "name": "Operadora KLM",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"active_accounts": []},
        {"active_accounts": {"results": []}},
    ],
)
async def test_bottlecrm_account_recovery_rejects_contract_drift(payload) -> None:
    client = object.__new__(BottleCRMClient)
    client._request = AsyncMock(return_value=payload)  # type: ignore[method-assign]

    with pytest.raises(BottleCRMClientError, match="active_accounts"):
        await client.find_account("Operadora KLM")


@pytest.mark.asyncio
async def test_bottlecrm_detail_match_normalizes_relations_and_decimals() -> None:
    client = object.__new__(BottleCRMClient)
    client._request = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "opportunity_obj": {
                "name": "Demanda KLM",
                "account": {"id": "account-1", "name": "Operadora KLM"},
                "contacts": [{"id": "contact-1", "first_name": "Ana"}],
                "amount": "1250.50",
                "stage": "PROPOSAL",
            }
        }
    )

    assert await client.entity_matches(
        "opportunity",
        "opportunity-1",
        {
            "name": "Demanda KLM",
            "account": "account-1",
            "contacts": ["contact-1"],
            "amount": 1250.5,
            "stage": "PROPOSAL",
        },
    )


@pytest.mark.asyncio
async def test_matching_digest_skips_update_only_after_detail_match() -> None:
    workspace_id = UUID("645e1341-a1ae-452c-8c99-884104dd06eb")
    entity_id = UUID("3c20e4c7-d8f3-4336-93af-733d4a7b9069")
    payload = {"name": "Operadora KLM"}
    links = SimpleNamespace(
        find_link_by_klm_entity=AsyncMock(
            return_value=SimpleNamespace(
                external_entity_id="account-1",
                link_metadata={"sourceDigest": _digest(payload)},
                sync_state=ExternalEntitySyncState.synced,
            )
        ),
        upsert_link=AsyncMock(),
    )
    service = object.__new__(BottleCRMSyncService)
    service.organization_id = "organization-1"
    service.links = links
    service.client = SimpleNamespace(entity_matches=AsyncMock(return_value=True))
    find = AsyncMock(return_value={"id": "account-1", "name": "Operadora KLM"})
    create = AsyncMock()
    update = AsyncMock()

    external_id, action = await service._sync_external(
        workspace_id=workspace_id,
        klm_entity_type="health_insurer",
        klm_entity_id=entity_id,
        external_entity_type="account",
        payload=payload,
        find=find,
        create=create,
        update=update,
    )

    assert (external_id, action) == ("account-1", "unchanged")
    service.client.entity_matches.assert_awaited_once_with("account", "account-1", payload)
    find.assert_not_awaited()
    create.assert_not_awaited()
    update.assert_not_awaited()
    links.upsert_link.assert_not_awaited()


@pytest.mark.asyncio
async def test_matching_digest_still_updates_when_bottlecrm_fields_drift() -> None:
    workspace_id = UUID("645e1341-a1ae-452c-8c99-884104dd06eb")
    entity_id = UUID("3c20e4c7-d8f3-4336-93af-733d4a7b9069")
    payload = {"name": "Operadora KLM", "industry": "INSURANCE"}
    links = SimpleNamespace(
        find_link_by_klm_entity=AsyncMock(
            return_value=SimpleNamespace(
                external_entity_id="account-1",
                link_metadata={"sourceDigest": _digest(payload)},
                sync_state=ExternalEntitySyncState.synced,
            )
        ),
        upsert_link=AsyncMock(),
    )
    service = object.__new__(BottleCRMSyncService)
    service.organization_id = "organization-1"
    service.links = links
    service.client = SimpleNamespace(entity_matches=AsyncMock(return_value=False))
    find = AsyncMock()
    update = AsyncMock()

    external_id, action = await service._sync_external(
        workspace_id=workspace_id,
        klm_entity_type="health_insurer",
        klm_entity_id=entity_id,
        external_entity_type="account",
        payload=payload,
        find=find,
        create=AsyncMock(),
        update=update,
    )

    assert (external_id, action) == ("account-1", "updated")
    update.assert_awaited_once_with("account-1", payload)
    find.assert_not_awaited()
    links.upsert_link.assert_awaited_once()


@pytest.mark.asyncio
async def test_matching_digest_adopts_replacement_instead_of_skipping_stale_link() -> None:
    workspace_id = UUID("645e1341-a1ae-452c-8c99-884104dd06eb")
    entity_id = UUID("3c20e4c7-d8f3-4336-93af-733d4a7b9069")
    payload = {"name": "Operadora KLM"}
    links = SimpleNamespace(
        find_link_by_klm_entity=AsyncMock(
            return_value=SimpleNamespace(
                external_entity_id="deleted-account",
                link_metadata={"sourceDigest": _digest(payload)},
                sync_state=ExternalEntitySyncState.synced,
            )
        ),
        upsert_link=AsyncMock(),
    )
    service = object.__new__(BottleCRMSyncService)
    service.organization_id = "organization-1"
    service.links = links
    service.client = SimpleNamespace(entity_matches=AsyncMock(return_value=False))
    update = AsyncMock(side_effect=[BottleCRMNotFoundError("gone"), None])

    external_id, action = await service._sync_external(
        workspace_id=workspace_id,
        klm_entity_type="health_insurer",
        klm_entity_id=entity_id,
        external_entity_type="account",
        payload=payload,
        find=AsyncMock(return_value={"id": "replacement-account"}),
        create=AsyncMock(),
        update=update,
    )

    assert (external_id, action) == ("replacement-account", "adopted")
    assert [call.args[0] for call in update.await_args_list] == [
        "deleted-account",
        "replacement-account",
    ]
    links.upsert_link.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_link_target_is_adopted_and_refreshed_before_relink() -> None:
    workspace_id = UUID("645e1341-a1ae-452c-8c99-884104dd06eb")
    entity_id = UUID("3c20e4c7-d8f3-4336-93af-733d4a7b9069")
    links = SimpleNamespace(
        find_link_by_klm_entity=AsyncMock(
            return_value=SimpleNamespace(external_entity_id="deleted-account")
        ),
        upsert_link=AsyncMock(),
    )
    service = object.__new__(BottleCRMSyncService)
    service.organization_id = "organization-1"
    service.links = links
    service.db = SimpleNamespace(commit=AsyncMock())
    update = AsyncMock(side_effect=[BottleCRMNotFoundError("gone"), None])
    find = AsyncMock(return_value={"id": "replacement-account"})

    external_id, action = await service._sync_external(
        workspace_id=workspace_id,
        klm_entity_type="health_insurer",
        klm_entity_id=entity_id,
        external_entity_type="account",
        payload={"name": "Operadora KLM"},
        find=find,
        create=AsyncMock(),
        update=update,
    )

    assert (external_id, action) == ("replacement-account", "adopted")
    assert update.await_args_list[1].args[0] == "replacement-account"
    service.db.commit.assert_not_awaited()
    links.upsert_link.assert_awaited_once()


@pytest.mark.asyncio
async def test_replacement_deleted_during_adoption_is_recreated() -> None:
    workspace_id = UUID("645e1341-a1ae-452c-8c99-884104dd06eb")
    entity_id = UUID("3c20e4c7-d8f3-4336-93af-733d4a7b9069")
    links = SimpleNamespace(
        find_link_by_klm_entity=AsyncMock(
            return_value=SimpleNamespace(external_entity_id="deleted-account")
        ),
        upsert_link=AsyncMock(),
    )
    service = object.__new__(BottleCRMSyncService)
    service.organization_id = "organization-1"
    service.links = links
    create = AsyncMock(return_value="recreated-account")
    update = AsyncMock(
        side_effect=[
            BottleCRMNotFoundError("original gone"),
            BottleCRMNotFoundError("replacement gone"),
        ]
    )

    external_id, action = await service._sync_external(
        workspace_id=workspace_id,
        klm_entity_type="health_insurer",
        klm_entity_id=entity_id,
        external_entity_type="account",
        payload={"name": "Operadora KLM"},
        find=AsyncMock(return_value={"id": "replacement-account"}),
        create=create,
        update=update,
    )

    assert (external_id, action) == ("recreated-account", "recreated")
    create.assert_awaited_once()
    links.upsert_link.assert_awaited_once()


@pytest.mark.asyncio
async def test_sync_workspace_serializes_concurrent_reconciliations(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(bottlecrm_sync_module, "_ROOT", tmp_path)
    monkeypatch.setattr(bottlecrm_sync_module, "_PROCESS_SYNC_LOCK", asyncio.Lock())
    workspace_id = UUID("645e1341-a1ae-452c-8c99-884104dd06eb")
    active = 0
    maximum_active = 0

    async def reconcile(_: UUID) -> dict[str, int]:
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        return {"accounts": 0}

    first = object.__new__(BottleCRMSyncService)
    second = object.__new__(BottleCRMSyncService)
    first._sync_workspace_unlocked = AsyncMock(side_effect=reconcile)
    second._sync_workspace_unlocked = AsyncMock(side_effect=reconcile)

    results = await asyncio.gather(
        first.sync_workspace(workspace_id),
        second.sync_workspace(workspace_id),
    )

    assert maximum_active == 1
    assert results == [{"accounts": 0}, {"accounts": 0}]


@pytest.mark.asyncio
async def test_stale_reconciliation_preserves_upstream_and_manual_links() -> None:
    class RowsResult:
        def __init__(self, rows):
            self.rows = rows

        def scalars(self):
            return self.rows

    workspace_id = UUID("645e1341-a1ae-452c-8c99-884104dd06eb")
    active_id = UUID("3c20e4c7-d8f3-4336-93af-733d4a7b9069")
    removed_id = UUID("9c1e9fe2-5745-4d96-ac87-8da52356dd9a")
    manual_id = UUID("e4d25e1c-8f90-46fc-a63a-b524a5cf4fc5")
    active = SimpleNamespace(
        klm_entity_type="client",
        klm_entity_id=active_id,
        external_entity_type="contact",
        sync_state=ExternalEntitySyncState.synced,
        sync_error=None,
        link_metadata={"managedBy": "klm-local-bridge"},
    )
    removed = SimpleNamespace(
        klm_entity_type="client",
        klm_entity_id=removed_id,
        external_entity_type="contact",
        sync_state=ExternalEntitySyncState.synced,
        sync_error="old error",
        link_metadata={"managedBy": "klm-local-bridge"},
    )
    manual = SimpleNamespace(
        klm_entity_type="client",
        klm_entity_id=manual_id,
        external_entity_type="contact",
        sync_state=ExternalEntitySyncState.synced,
        sync_error=None,
        link_metadata={"managedBy": "administrator"},
    )
    service = object.__new__(BottleCRMSyncService)
    service.organization_id = "organization-1"
    service.db = SimpleNamespace(
        execute=AsyncMock(return_value=RowsResult([active, removed, manual]))
    )

    changed = await service._reconcile_stale_links(
        workspace_id,
        {("client", active_id, "contact")},
    )

    assert changed == 1
    assert active.sync_state == ExternalEntitySyncState.synced
    assert manual.sync_state == ExternalEntitySyncState.synced
    assert removed.sync_state == ExternalEntitySyncState.stale
    assert removed.sync_error is None
    assert removed.link_metadata["staleReason"] == "canonical_entity_absent"
    assert removed.link_metadata["upstreamPreserved"] is True


@pytest.mark.asyncio
async def test_organization_binding_conflict_stops_before_workspace_reads() -> None:
    workspace_id = UUID("645e1341-a1ae-452c-8c99-884104dd06eb")
    service = object.__new__(BottleCRMSyncService)
    service.client = SimpleNamespace(organization_id=AsyncMock(return_value="organization-1"))
    service._ensure_workspace_binding = AsyncMock(
        side_effect=ExternalEntityLinkConflictError(
            "KLM or external entity is already linked in this workspace."
        )
    )
    service.db = SimpleNamespace(execute=AsyncMock(), commit=AsyncMock())

    with pytest.raises(ExternalEntityLinkConflictError):
        await service._sync_workspace_unlocked(workspace_id)

    service.db.execute.assert_not_awaited()
    service.db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_empty_workspace_projection_commits_local_links_once() -> None:
    class EmptyResult:
        @staticmethod
        def scalars():
            return []

    workspace_id = UUID("645e1341-a1ae-452c-8c99-884104dd06eb")
    service = object.__new__(BottleCRMSyncService)
    service.client = SimpleNamespace(organization_id=AsyncMock(return_value="organization-1"))
    service.links = SimpleNamespace(upsert_link=AsyncMock())
    service.db = SimpleNamespace(
        execute=AsyncMock(return_value=EmptyResult()),
        commit=AsyncMock(),
    )

    result = await service._sync_workspace_unlocked(workspace_id)

    service.db.commit.assert_awaited_once()
    assert result["created"] == 0
    assert result["organizationBound"] is True


@pytest.mark.asyncio
async def test_active_matter_with_deleted_client_fails_before_external_projection() -> None:
    class RowsResult:
        def __init__(self, rows):
            self.rows = rows

        def scalars(self):
            return self.rows

    workspace_id = UUID("645e1341-a1ae-452c-8c99-884104dd06eb")
    missing_client_id = UUID("3c20e4c7-d8f3-4336-93af-733d4a7b9069")
    service = object.__new__(BottleCRMSyncService)
    service.client = SimpleNamespace(organization_id=AsyncMock(return_value="organization-1"))
    service.links = SimpleNamespace(upsert_link=AsyncMock())
    service.db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                RowsResult([]),
                RowsResult([SimpleNamespace(client_id=missing_client_id)]),
            ]
        ),
        commit=AsyncMock(),
    )

    with pytest.raises(LookupError, match="cliente ativo íntegro"):
        await service._sync_workspace_unlocked(workspace_id)

    service.db.commit.assert_not_awaited()
