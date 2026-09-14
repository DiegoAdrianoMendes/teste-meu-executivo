import pytest

from app.services.local_billing_service import LocalBillingService


@pytest.mark.asyncio
async def test_local_provisioning_api_previews_applies_and_replays(
    client,
    db_session,
    auth_context,
):
    await LocalBillingService(db_session).provision_trial(
        workspace_id=auth_context.workspace_id,
        plan_tier="pro",
        idempotency_key="provisioning-api-trial",
        actor_user_id=auth_context.user_id,
    )
    await db_session.commit()

    preview = await client.get("/api/v1/local-provisioning/plan")
    created = await client.post(
        "/api/v1/local-provisioning/apply",
        json={
            "idempotencyKey": "provisioning-api-apply",
            "reason": "Teste integrado da SPA",
        },
    )
    replayed = await client.post(
        "/api/v1/local-provisioning/apply",
        json={
            "idempotencyKey": "provisioning-api-apply",
            "reason": "Teste integrado da SPA",
        },
    )
    state = await client.get("/api/v1/local-provisioning/status")

    assert preview.status_code == 200, preview.text
    assert created.status_code == 200, created.text
    assert replayed.status_code == 200, replayed.text
    assert state.status_code == 200, state.text
    assert preview.json()["creates"] == 10
    assert created.json()["externalEffects"] == 0
    assert created.json()["state"]["ready"] is True
    assert replayed.json()["replayed"] is True
    assert replayed.json()["intentId"] == created.json()["intentId"]
    assert state.json()["ready"] is True
