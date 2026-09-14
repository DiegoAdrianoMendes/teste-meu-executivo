"""S23: negação individual do módulo financeiro fecha as rotas financeiras."""

import pytest

from app.models.workspace_membership import WorkspaceMembership

# As rotas financeiras passam pela matriz da assinatura: sem assinatura ativa, o
# ambiente de teste (APP_ENV=test, como no CI) responde 403 subscription_missing
# antes de chegar à negação individual. Só o modo desenvolvimento isenta isso.
pytestmark = pytest.mark.usefixtures("active_subscription")


@pytest.mark.asyncio
async def test_individual_finance_denial_closes_finance_routes(client, db_session, auth_context):
    membership = await db_session.get(WorkspaceMembership, auth_context.membership_id)
    assert membership is not None

    aberto = await client.get("/api/v1/finance/entries")
    assert aberto.status_code == 200, aberto.text

    membership.permission_denials = [{"module": "finance", "action": "view", "scope": "workspace"}]
    await db_session.flush()
    negado = await client.get("/api/v1/finance/entries")
    assert negado.status_code == 403, negado.text
    assert negado.json()["detail"]["code"] == "AUTH-012"

    membership.permission_denials = []
    await db_session.flush()
    reaberto = await client.get("/api/v1/finance/entries")
    assert reaberto.status_code == 200, reaberto.text
