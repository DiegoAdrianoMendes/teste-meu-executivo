#!/usr/bin/env python3
"""Define uma senha de teste conhecida para o usuário local semeado
(scripts/seed.py), exclusivamente para permitir POST /api/v1/auth/login
real durante o load test local (docs/LOAD_TEST_FASE1.md).

Guard: recusa rodar fora de KLM_RUNTIME_MODE=local_simulation. Nunca usar
contra staging/produção — não existe staging/produção nesta fase (Fase 1
ainda não subiu para AWS, ver docs/RUNBOOK_AWS_FASE1.md).

Uso:
    .venv/bin/python scripts/load/set_local_test_password.py
Imprime email + senha de teste na stdout.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TEST_PASSWORD = "LoadTestLocal!2026#klm"  # nosec: uso local efêmero, não é segredo de produção


async def _set_password() -> None:
    from scripts.run_local import configure_local_environment

    configure_local_environment(root=ROOT, host="127.0.0.1", port=8010)

    from sqlalchemy import select

    from app.core.config import settings
    from app.core.security import hash_password_async
    from app.db.session import AsyncSessionLocal
    from app.models.user import User
    from app.services.klm_local_seed_service import LOCAL_USER_EMAIL

    if not settings.is_local_only or not settings.KLM_LOCAL_BOOTSTRAP_ENABLED:
        raise RuntimeError(
            "Definir senha de teste só é permitido em runtime local_simulation."
        )

    async with AsyncSessionLocal() as db:
        user = await db.scalar(select(User).where(User.email == LOCAL_USER_EMAIL))
        if user is None:
            raise RuntimeError(
                "Usuário local não encontrado. Rode `.venv/bin/python scripts/seed.py` antes."
            )
        user.hashed_password = await hash_password_async(TEST_PASSWORD)
        await db.commit()

    print(f"email={LOCAL_USER_EMAIL}")
    print(f"password={TEST_PASSWORD}")


if __name__ == "__main__":
    asyncio.run(_set_password())
