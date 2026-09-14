from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.mcp.grants import GrantClaims
from app.mcp.runtime import KlmMcpRuntime
from app.models.client import Client
from app.models.matter import HealthSubtheme, Matter, PracticeArea

ALL_CAPABILITIES = (
    "crm.read",
    "crm.sensitive.read",
    "crm.write",
    "handoff.request",
    "approval.request",
)


class NonClosingSessionContext:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def __aenter__(self) -> AsyncSession:
        return self.session

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None


def runtime_for(
    auth_context,
    db_session: AsyncSession,
    state_dir: Path,
    *,
    capabilities: tuple[str, ...] = ALL_CAPABILITIES,
    expires_at: datetime | None = None,
) -> KlmMcpRuntime:
    claims = GrantClaims(
        grant_id=uuid4(),
        profile="pytest",
        workspace_id=auth_context.workspace_id,
        user_id=auth_context.user_id,
        membership_id=auth_context.membership_id,
        capabilities=capabilities,
        issued_at=datetime.now(UTC) - timedelta(minutes=1),
        expires_at=expires_at or datetime.now(UTC) + timedelta(hours=1),
        database_fingerprint="a" * 64,
    )
    return KlmMcpRuntime(
        claims,
        lambda: NonClosingSessionContext(db_session),
        state_dir=state_dir,
    )


@pytest_asyncio.fixture
async def legal_matter(db_session: AsyncSession, auth_context):
    client = Client(
        workspace_id=auth_context.workspace_id,
        name="Ana Saúde",
        cpf="123.456.789-01",
        email="ana@example.com",
        phone="+55 61 99999-0000",
        notes="Cliente prefere contato por WhatsApp.",
    )
    db_session.add(client)
    await db_session.flush()
    matter = Matter(
        workspace_id=auth_context.workspace_id,
        client_id=client.id,
        practice_area=PracticeArea.saude_suplementar,
        demand_type="Negativa de medicamento de alto custo",
        health_subtheme=HealthSubtheme.medicamento_alto_custo,
        health_object_name="Medicamento Exemplo",
        health_insurer="Plano Exemplo",
        executive_summary="Negativa urgente para Ana Saúde.",
    )
    db_session.add(matter)
    await db_session.flush()
    return client, matter


@pytest_asyncio.fixture
async def mcp_runtime(auth_context, db_session: AsyncSession, tmp_path: Path):
    return runtime_for(auth_context, db_session, tmp_path)
