from __future__ import annotations

from uuid import uuid4

import pytest


pytestmark = pytest.mark.usefixtures("active_subscription")


async def _create_batch(client, **item_overrides) -> dict:
    payload = {
        "name": "Lote publicações TJDFT",
        "criterionType": "tribunal",
        "criterionValue": {"tribunal": "TJDFT"},
        "items": [
            {
                "itemRef": "pub-1",
                "action": "classificar",
                "decisionKind": "classificacao",
            },
            {
                "itemRef": "tese-1",
                "action": "revisar",
                "decisionKind": "tese",
            },
        ],
    }
    payload.update(item_overrides)
    response = await client.post("/api/v1/legal/batches", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def test_batch_lifecycle_process_panel_approve_rollback(client, db_session, auth_context):
    batch = await _create_batch(client)
    batch_id = batch["id"]
    assert batch["status"] == "aberto"

    items_response = await client.get(f"/api/v1/legal/batches/{batch_id}/items")
    assert items_response.status_code == 200
    items = items_response.json()
    assert len(items) == 2
    # Estado individual desde a criação — nenhum item nasce diferente do
    # outro.
    assert all(item["state"] == "pendente" for item in items)

    process_response = await client.post(f"/api/v1/legal/batches/{batch_id}/process")
    assert process_response.status_code == 200, process_response.text
    process_body = process_response.json()
    assert len(process_body["processedItemIds"]) == 2

    panel_response = await client.get(f"/api/v1/legal/batches/{batch_id}/panel")
    assert panel_response.status_code == 200
    panel = panel_response.json()
    assert panel["totalItems"] == 2
    # Sem política de autonomia cadastrada, fail-closed: ambos aguardam humano.
    assert panel["aguardandoHumano"] == 2

    approve_all_response = await client.post(f"/api/v1/legal/batches/{batch_id}/approve-all")
    assert approve_all_response.status_code == 200, approve_all_response.text
    approve_all_body = approve_all_response.json()
    assert len(approve_all_body["approvedItemIds"]) == 1
    assert len(approve_all_body["skippedProtectedItemIds"]) == 1

    items_after = (
        await client.get(f"/api/v1/legal/batches/{batch_id}/items")
    ).json()
    by_ref = {item["itemRef"]: item for item in items_after}
    assert by_ref["pub-1"]["state"] == "concluido"
    # A decisão crítica (tese) NUNCA sai de aguardando_humano via approve-all.
    assert by_ref["tese-1"]["state"] == "aguardando_humano"

    tese_item_id = by_ref["tese-1"]["id"]
    approve_item_response = await client.post(
        f"/api/v1/legal/batches/{batch_id}/items/{tese_item_id}/approve"
    )
    assert approve_item_response.status_code == 200, approve_item_response.text
    assert approve_item_response.json()["state"] == "concluido"

    rollback_response = await client.post(
        f"/api/v1/legal/batches/{batch_id}/items/{tese_item_id}/rollback",
        json={"reason": "Revisão humana pediu nova análise da tese."},
    )
    assert rollback_response.status_code == 200, rollback_response.text
    rolled_back = rollback_response.json()
    assert rolled_back["state"] == "aguardando_humano"
    assert rolled_back["approvedByUserId"] is None


async def test_batches_endpoints_require_authentication(unauthenticated_client):
    response = await unauthenticated_client.get(
        f"/api/v1/legal/batches/{uuid4()}/panel"
    )
    assert response.status_code in (401, 403)
