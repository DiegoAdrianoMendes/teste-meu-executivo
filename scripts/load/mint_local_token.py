#!/usr/bin/env python3
"""Emite um access token válido para o usuário local semeado (scripts/seed.py),
exclusivamente para uso em load test contra o runtime local (127.0.0.1).

Nunca usar contra um ambiente com KLM_RUNTIME_MODE != local_simulation: o guard
abaixo recusa a execução fora do modo local.

Uso:
    .venv/bin/python scripts/load/mint_local_token.py
Imprime o access_token (JWT) na stdout, nada mais — pronto para
`export KLM_LOAD_TOKEN=$(...)`.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


async def _mint() -> str:
    from scripts.run_local import configure_local_environment

    configure_local_environment(root=ROOT, host="127.0.0.1", port=8010)

    from sqlalchemy import select

    from app.core.config import settings
    from app.db.session import AsyncSessionLocal
    from app.models.user import User
    from app.services.auth_service import AuthService
    from app.services.klm_local_seed_service import LOCAL_USER_EMAIL

    if not settings.is_local_only or not settings.KLM_LOCAL_BOOTSTRAP_ENABLED:
        raise RuntimeError(
            "Emissão de token de teste só é permitida em runtime local_simulation."
        )

    async with AsyncSessionLocal() as db:
        user = await db.scalar(
            select(User)
            .where(User.email == LOCAL_USER_EMAIL)
            .execution_options(populate_existing=True)
        )
        if user is None:
            raise RuntimeError(
                "Usuário local não encontrado. Rode `.venv/bin/python scripts/seed.py` antes."
            )
        await db.refresh(user, attribute_names=["memberships"])
        for membership in user.memberships:
            await db.refresh(membership, attribute_names=["workspace"])
        token_response = await AuthService(db).issue_new_session(user, request=None)
        await db.commit()
        return token_response.access_token


if __name__ == "__main__":
    print(asyncio.run(_mint()))
