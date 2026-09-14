from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.client import Client
from app.models.document import Document, DocumentStatus
from app.models.klm_operations import EmailIngestReceipt, OmnichannelMessage, OmnichannelThread
from app.models.knowledge_chunk import KnowledgeChunk
from app.models.matter import Matter
from app.models.user import AgentType, User, UserRole
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.schemas.klm_operations import EmailIngestRequest
from app.services.klm_operations_service import KlmOperationsService

MASTER_URL = os.getenv("KLM_TEST_POSTGRES_MASTER_URL")
DISPOSABLE_QA = os.getenv("KLM_TEST_POSTGRES_QA_DISPOSABLE") == "1"

if not MASTER_URL or not DISPOSABLE_QA:
    raise RuntimeError(
        "PostgreSQL QA requires KLM_TEST_POSTGRES_MASTER_URL and an explicit "
        "KLM_TEST_POSTGRES_QA_DISPOSABLE=1 gate; "
        "run scripts/verify_postgresql_qa.sh."
    )


async def _cleanup_workspace(sessions, workspace_id, user_id) -> None:
    protected_tables = (
        (
            "klm_omnichannel_message_evidence_events",
            "trg_klm_omnichannel_message_evidence_events_immutable",
        ),
        ("klm_audit_events", "trg_klm_audit_events_immutable_row"),
    )
    async with sessions.begin() as db:
        await db.execute(text("SET LOCAL lock_timeout = '10s'"))
        for table_name, trigger_name in protected_tables:
            await db.execute(
                text(f'ALTER TABLE "{table_name}" DISABLE TRIGGER "{trigger_name}"')
            )
        await db.execute(
            text(
                "DELETE FROM klm_omnichannel_message_evidence_events "
                "WHERE workspace_id = :workspace_id"
            ),
            {"workspace_id": workspace_id},
        )
        await db.execute(delete(Workspace).where(Workspace.id == workspace_id))
        await db.execute(delete(User).where(User.id == user_id))
        for table_name, trigger_name in protected_tables:
            await db.execute(
                text(f'ALTER TABLE "{table_name}" ENABLE TRIGGER "{trigger_name}"')
            )


@pytest.mark.asyncio
async def test_concurrent_email_delivery_materializes_one_card_and_one_rag_set() -> None:
    assert MASTER_URL is not None
    engine = create_async_engine(MASTER_URL, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.connect() as connection:
        database_name = await connection.scalar(text("SELECT current_database()"))
    assert str(database_name).startswith("klm_legal_hub_qa_")
    token = uuid4().hex[:12]
    sender_email = f"email-race-{token}@example.com"

    async with sessions() as db:
        workspace = Workspace(
            name=f"Email race {token}",
            slug=f"email-race-{token}",
            status=WorkspaceStatus.active,
            setup_mode=WorkspaceSetupMode.self_service,
            is_active=True,
            settings={},
        )
        user = User(
            name=f"Email worker {token}",
            email=f"email-worker-{token}@example.com",
            agent_type=AgentType.human,
            role=UserRole.admin,
            initials="EW",
            color="#111827",
            is_active=True,
            is_internal_staff=False,
        )
        db.add_all((workspace, user))
        await db.commit()
        workspace_id = workspace.id
        user_id = user.id

    payload = EmailIngestRequest(
        message_id=f"provider-race-{token}",
        sender_email=sender_email,
        sender_name="Lead concorrente",
        subject="Negativa de medicamento",
        body="O plano negou o medicamento prescrito.",
        attachments=[
            {
                "name": "negativa.pdf",
                "mimeType": "application/pdf",
                "contentText": "Negativa formal do plano.",
            },
            {
                "name": "prescricao.pdf",
                "mimeType": "application/pdf",
                "contentText": "Prescrição médica urgente.",
            },
        ],
    )
    start = asyncio.Event()

    async def ingest() -> tuple[bool, str, str]:
        async with sessions() as db:
            await start.wait()
            result = await KlmOperationsService(db).ingest_email(
                workspace_id,
                user_id,
                payload,
            )
            await db.commit()
            return (
                result["deduplicated"],
                str(result["matter_id"]),
                str(result["message"].id),
            )

    try:
        tasks = (asyncio.create_task(ingest()), asyncio.create_task(ingest()))
        start.set()
        outcomes = await asyncio.gather(*tasks)

        assert sorted(item[0] for item in outcomes) == [False, True]
        assert len({item[1] for item in outcomes}) == 1
        assert len({item[2] for item in outcomes}) == 1

        async with sessions() as db:
            matter_id = outcomes[0][1]
            counts = {
                "clients": await db.scalar(
                    select(func.count(Client.id)).where(Client.email == sender_email)
                ),
                "matters": await db.scalar(
                    select(func.count(Matter.id)).where(Matter.id == matter_id)
                ),
                "threads": await db.scalar(
                    select(func.count(OmnichannelThread.id)).where(
                        OmnichannelThread.workspace_id == workspace_id
                    )
                ),
                "messages": await db.scalar(
                    select(func.count(OmnichannelMessage.id)).where(
                        OmnichannelMessage.workspace_id == workspace_id
                    )
                ),
                "documents": await db.scalar(
                    select(func.count(Document.id)).where(Document.matter_id == matter_id)
                ),
                "chunks": await db.scalar(
                    select(func.count(KnowledgeChunk.id)).where(
                        KnowledgeChunk.matter_id == matter_id
                    )
                ),
                "receipts": await db.scalar(
                    select(func.count(EmailIngestReceipt.id)).where(
                        EmailIngestReceipt.workspace_id == workspace_id
                    )
                ),
            }
        assert counts == {
            "clients": 1,
            "matters": 1,
            "threads": 1,
            "messages": 1,
            "documents": 2,
            "chunks": 3,
            "receipts": 1,
        }
        async with sessions() as db:
            documents = list(
                (
                    await db.scalars(
                        select(Document)
                        .where(Document.matter_id == matter_id)
                        .order_by(Document.name)
                    )
                ).all()
            )
            assert all(item.status is DocumentStatus.aguardando for item in documents)
            assert all(item.s3_key is None for item in documents)
    finally:
        await _cleanup_workspace(sessions, workspace_id, user_id)
        await engine.dispose()
