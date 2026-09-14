#!/usr/bin/env python3
"""Semeia N usuários adicionais no workspace local (além do usuário local
padrão de scripts/seed.py), todos com a mesma senha de teste conhecida,
para permitir medir POST /api/v1/auth/login sob concorrência real sem
esbarrar no rate limiter por identidade (5 tentativas / 15 min por email,
ver app/services/auth_rate_limit_service.py) usando sempre a mesma conta.

Guard: só roda em runtime local_simulation. Usuários marcados como fixture
de load test (name com prefixo "Load Test") para facilitar limpeza manual.

Uso:
    .venv/bin/python scripts/load/seed_load_test_users.py --count 25
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.load.set_local_test_password import TEST_PASSWORD  # noqa: E402

USER_EMAIL_TEMPLATE = "load-test-user-{index:03d}@local.klm"


async def _seed(count: int) -> list[str]:
    from scripts.run_local import configure_local_environment

    configure_local_environment(root=ROOT, host="127.0.0.1", port=8010)

    from sqlalchemy import select

    from datetime import UTC, datetime

    from app.core.config import settings
    from app.core.security import hash_password_async
    from app.db.session import AsyncSessionLocal
    from app.models.user import AgentType, User, UserRole
    from app.models.workspace import Workspace
    from app.models.workspace_membership import MembershipStatus, WorkspaceMembership, WorkspaceRole
    from app.services.klm_local_seed_service import LOCAL_USER_EMAIL

    if not settings.is_local_only or not settings.KLM_LOCAL_BOOTSTRAP_ENABLED:
        raise RuntimeError("Seed de usuários de load test só é permitido em runtime local_simulation.")

    password_hash = await hash_password_async(TEST_PASSWORD)
    emails: list[str] = []

    async with AsyncSessionLocal() as db:
        local_user = await db.scalar(select(User).where(User.email == LOCAL_USER_EMAIL))
        if local_user is None:
            raise RuntimeError("Rode `.venv/bin/python scripts/seed.py` antes deste script.")
        membership = await db.scalar(
            select(WorkspaceMembership).where(WorkspaceMembership.user_id == local_user.id)
        )
        if membership is None:
            raise RuntimeError("Usuário local sem workspace. Rode `scripts/seed.py` novamente.")
        workspace = await db.get(Workspace, membership.workspace_id)

        for index in range(1, count + 1):
            email = USER_EMAIL_TEMPLATE.format(index=index)
            emails.append(email)
            user = await db.scalar(select(User).where(User.email == email))
            if user is None:
                user = User(
                    name=f"Load Test User {index:03d}",
                    email=email,
                    agent_type=AgentType.human,
                    role=UserRole.lawyer,
                    initials="LT",
                    color="#777777",
                    is_active=True,
                    is_internal_staff=False,
                    is_platform_admin=False,
                    email_verified_at=datetime.now(UTC),
                    hashed_password=password_hash,
                )
                db.add(user)
                await db.flush()
            else:
                user.hashed_password = password_hash
                if user.email_verified_at is None:
                    user.email_verified_at = datetime.now(UTC)

            existing_membership = await db.scalar(
                select(WorkspaceMembership).where(
                    WorkspaceMembership.workspace_id == workspace.id,
                    WorkspaceMembership.user_id == user.id,
                )
            )
            if existing_membership is None:
                db.add(
                    WorkspaceMembership(
                        workspace_id=workspace.id,
                        user_id=user.id,
                        role=WorkspaceRole.operator,
                        status=MembershipStatus.active,
                        is_default=True,
                        access_package="office_member",
                    )
                )
        await db.commit()

    return emails


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=25)
    args = parser.parse_args()
    emails = asyncio.run(_seed(args.count))
    print(f"Usuários de load test semeados: {len(emails)}")
    print(f"password={TEST_PASSWORD}")
    print(f"exemplo_email={emails[0]}..{emails[-1]}" if emails else "nenhum usuário criado")


if __name__ == "__main__":
    main()
