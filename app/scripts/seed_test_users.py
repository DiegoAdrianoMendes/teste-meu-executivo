"""Compatibility entrypoint for the guarded KLM Hub local bootstrap.

The historical command name is kept so old local runbooks do not break. It no
longer creates fixed-password users and is rejected outside the local-only
runtime.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def _assert_local_bootstrap_allowed(settings: object) -> None:
    if (
        not getattr(settings, "uses_local_infrastructure", False)
        or not getattr(settings, "is_local_only", False)
        or not getattr(settings, "KLM_LOCAL_BOOTSTRAP_ENABLED", False)
    ):
        raise RuntimeError(
            "O bootstrap demonstrativo só pode rodar localmente, com todos os "
            "efeitos externos bloqueados."
        )


async def seed(db: AsyncSession | None = None) -> None:
    if db is None:
        from scripts.run_local import configure_local_environment

        root = Path(__file__).resolve().parents[2]
        configure_local_environment(root=root, host="127.0.0.1", port=8010)

    from app.core.config import settings
    from app.db.session import AsyncSessionLocal
    from app.services.klm_local_seed_service import KlmLocalSeedService

    _assert_local_bootstrap_allowed(settings)
    if db is not None:
        user, workspace = await KlmLocalSeedService(db).ensure()
        await db.commit()
    else:
        async with AsyncSessionLocal() as local_db:
            user, workspace = await KlmLocalSeedService(local_db).ensure()
            await local_db.commit()
    print(
        "Bootstrap local idempotente concluído: "
        f"workspace={workspace.slug} usuário={user.email} efeitos_externos=0"
    )


if __name__ == "__main__":
    asyncio.run(seed())
