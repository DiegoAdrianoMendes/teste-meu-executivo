from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.agents.stubs import klm_sdr
from app.services import conversation_insight_service, meta_ai_service


class ScalarRows:
    def __init__(self, values: list[object] | None = None) -> None:
        self.values = values or []

    def all(self) -> list[object]:
        return self.values


class CapturingScalarsDB:
    def __init__(self) -> None:
        self.statements: list[object] = []

    async def scalars(self, statement):
        self.statements.append(statement)
        return ScalarRows()


def _limit_value(statement) -> int:
    return int(statement._limit_clause.value)


@pytest.mark.asyncio
async def test_sdr_database_query_is_bounded_to_twenty_four() -> None:
    db = CapturingScalarsDB()

    records = await klm_sdr._load_recent_whatsapp_messages(db, contact_id=uuid4())

    assert records == []
    assert _limit_value(db.statements[0]) == klm_sdr.SDR_CONTEXT_QUERY_LIMIT == 24


@pytest.mark.asyncio
async def test_conversation_insight_database_query_is_bounded_to_twenty_four() -> None:
    db = CapturingScalarsDB()
    service = conversation_insight_service.ConversationInsightService(db)

    records = await service._load_whatsapp_history(uuid4(), uuid4())

    assert records == []
    assert (
        _limit_value(db.statements[0])
        == conversation_insight_service.CONVERSATION_CONTEXT_QUERY_LIMIT
        == 24
    )


@pytest.mark.asyncio
async def test_meta_database_query_is_bounded_to_twenty_four() -> None:
    db = CapturingScalarsDB()
    service = meta_ai_service.MetaAIService(db)
    conversation = SimpleNamespace(id=uuid4(), workspace_id=uuid4(), messages=[])

    records = await service._load_recent_messages(conversation)

    assert records == []
    assert _limit_value(db.statements[0]) == meta_ai_service.META_CONTEXT_QUERY_LIMIT == 24
