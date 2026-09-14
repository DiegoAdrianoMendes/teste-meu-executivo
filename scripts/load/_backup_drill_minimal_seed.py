#!/usr/bin/env python3
"""Seed mínimo (ORM direto) usado só por scripts/backup_restore_drill.sh para
popular tabelas-chave (users, workspaces, workspace_memberships, clients,
matters) num Postgres efêmero antes do dump. Não é um seed de demonstração
de produto — ver scripts/seed.py para isso.

Uso: chamado pelo drill com a URL asyncpg como único argumento.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


async def main(database_url: str) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from app.models.client import Client
    from app.models.matter import KanbanColumnId, Matter, MicroStatus, PracticeArea, Priority, Urgency
    from app.models.user import AgentType, User, UserRole
    from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
    from app.models.workspace_membership import MembershipStatus, WorkspaceMembership, WorkspaceRole

    engine = create_async_engine(database_url)
    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with Session() as db:
        workspace = Workspace(
            id=uuid4(),
            name="Backup Drill Workspace",
            slug=f"backup-drill-{uuid4().hex[:8]}",
            legal_name="Backup Drill LTDA",
            status=WorkspaceStatus.active,
            setup_mode=WorkspaceSetupMode.assisted,
            is_active=True,
            onboarding_completed=True,
            settings={"runtime": "backup_restore_drill"},
        )
        db.add(workspace)
        await db.flush()

        user = User(
            id=uuid4(),
            name="Backup Drill User",
            email="backup-drill-user@local.klm",
            agent_type=AgentType.human,
            role=UserRole.admin,
            initials="BD",
            color="#0a3b5c",
            is_active=True,
            email_verified_at=datetime.now(UTC),
        )
        db.add(user)
        await db.flush()

        db.add(
            WorkspaceMembership(
                id=uuid4(),
                workspace_id=workspace.id,
                user_id=user.id,
                role=WorkspaceRole.workspace_owner,
                status=MembershipStatus.active,
                is_default=True,
                access_package="office_owner",
            )
        )

        clients = []
        for index in range(1, 6):
            client = Client(
                id=uuid4(),
                workspace_id=workspace.id,
                name=f"Cliente Drill {index:02d}",
                email=f"cliente-drill-{index:02d}@local.klm",
            )
            clients.append(client)
            db.add(client)
        await db.flush()

        for index, client in enumerate(clients, start=1):
            db.add(
                Matter(
                    id=uuid4(),
                    workspace_id=workspace.id,
                    client_id=client.id,
                    practice_area=PracticeArea.trabalhista,
                    demand_type=f"Caso de drill {index:02d}",
                    column_id=KanbanColumnId.novo_lead,
                    micro_status=MicroStatus.lead_novo,
                    priority=Priority.media,
                    urgency=Urgency.normal,
                )
            )

        await db.commit()

    await engine.dispose()
    print("Seed mínimo do drill de backup/restore concluído.")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
