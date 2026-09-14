"""Rebrand KLM -> Meu Executivo: correção retroativa idempotente do
display_name das conexões semeadas por OmnichannelV6BootstrapService.

Um banco já semeado antes deste rebrand tem conexões cujo display_name
ainda é o antigo ("Instagram KLM" etc). O bootstrap faz upsert por
provider+connection_key (não recria a linha), então sem a correção
retroativa esse display_name antigo nunca seria atualizado. Este teste
garante que rodar o bootstrap novamente (a) renomeia o display_name
existente e (b) não duplica nem oscila o valor em execuções repetidas.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.omnichannel_v6 import OmnichannelChannelConnection
from app.models.user import AgentType, User, UserRole
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership, WorkspaceRole
from app.services.omnichannel_v6_bootstrap_service import OmnichannelV6BootstrapService


@pytest.mark.asyncio
async def test_stale_connection_display_name_is_renamed_retroactively_and_idempotently(
    db_session,
):
    workspace = Workspace(
        name="Workspace Rebrand",
        slug=f"workspace-rebrand-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(workspace)
    await db_session.flush()

    user = User(
        name="Usuário Rebrand",
        email=f"rebrand-{uuid4().hex[:8]}@example.com",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="UR",
        color="#111827",
        is_active=True,
        is_internal_staff=False,
        email_verified_at=datetime.now(UTC),
    )
    db_session.add(user)
    await db_session.flush()

    db_session.add(
        WorkspaceMembership(
            workspace_id=workspace.id,
            user_id=user.id,
            role=WorkspaceRole.workspace_owner,
            status=MembershipStatus.active,
            is_default=True,
        )
    )
    await db_session.flush()

    # Simula um banco semeado ANTES do rebrand: a conexão já existe com o
    # display_name antigo ("Instagram KLM"), como teria ficado se o
    # bootstrap tivesse rodado com o código pré-rebrand.
    stale_connection = OmnichannelChannelConnection(
        workspace_id=workspace.id,
        channel="instagram",
        provider="meta_instagram",
        connection_key="instagram-principal",
        display_name="Instagram KLM",
        transport_mode="official_api",
        is_default=True,
        configuration={},
        routing_policy={"canonicalConversation": True, "transferEnabled": True},
        cost_policy={"ledgerEnabled": True, "currency": "BRL"},
        health={"state": "verified"},
    )
    db_session.add(stale_connection)
    await db_session.flush()
    connection_id = stale_connection.id

    service = OmnichannelV6BootstrapService(db_session)

    # 1ª execução pós-rebrand: deve corrigir retroativamente sem criar
    # uma segunda linha (upsert continua sendo por provider+connection_key).
    await service.ensure_workspace(workspace.id, user.id, include_demo_events=False)
    await db_session.commit()

    rows = (
        await db_session.execute(
            select(OmnichannelChannelConnection).where(
                OmnichannelChannelConnection.workspace_id == workspace.id,
                OmnichannelChannelConnection.provider == "meta_instagram",
                OmnichannelChannelConnection.connection_key == "instagram-principal",
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].id == connection_id
    assert rows[0].display_name == "Instagram Meu Executivo"

    # 2ª execução (idempotência): rodar de novo não duplica nem reverte.
    await service.ensure_workspace(workspace.id, user.id, include_demo_events=False)
    await db_session.commit()

    rows_again = (
        await db_session.execute(
            select(OmnichannelChannelConnection).where(
                OmnichannelChannelConnection.workspace_id == workspace.id,
                OmnichannelChannelConnection.provider == "meta_instagram",
                OmnichannelChannelConnection.connection_key == "instagram-principal",
            )
        )
    ).scalars().all()
    assert len(rows_again) == 1
    assert rows_again[0].id == connection_id
    assert rows_again[0].display_name == "Instagram Meu Executivo"
