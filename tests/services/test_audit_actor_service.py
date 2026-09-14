from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.services.audit_actor_service import resolve_human_actor_label


@pytest.mark.asyncio
async def test_resolve_human_actor_label_uses_persisted_name() -> None:
    db = SimpleNamespace(scalar=AsyncMock(return_value="  Usuário Teste  "))

    label = await resolve_human_actor_label(db, uuid4())

    assert label == "Usuário Teste"
    db.scalar.assert_awaited_once()


@pytest.mark.asyncio
async def test_resolve_human_actor_label_rejects_missing_identity() -> None:
    db = SimpleNamespace(scalar=AsyncMock(return_value=None))

    with pytest.raises(ValueError, match="não existe ou está inativo"):
        await resolve_human_actor_label(db, uuid4())


@pytest.mark.asyncio
async def test_resolve_human_actor_label_requires_user_id() -> None:
    db = SimpleNamespace(scalar=AsyncMock())

    with pytest.raises(ValueError, match="usuário responsável"):
        await resolve_human_actor_label(db, None)

    db.scalar.assert_not_awaited()
