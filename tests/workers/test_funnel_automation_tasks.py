from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.client import Client
from app.models.klm_operations import KlmAuditEvent
from app.models.matter import Matter, PracticeArea
from app.services.klm_agent_service import KlmAgentService
from app.workers import funnel_automation_tasks


@pytest.mark.asyncio
async def test_funnel_worker_processes_intent_once_and_audits(
    db_session, auth_context, monkeypatch
):
    client = Client(workspace_id=auth_context.workspace_id, name="Worker client")
    db_session.add(client)
    await db_session.flush()
    matter = Matter(
        workspace_id=auth_context.workspace_id,
        client_id=client.id,
        practice_area=PracticeArea.saude_suplementar,
        demand_type="Worker funnel",
    )
    db_session.add(matter)
    await db_session.flush()
    operation_id = f"worker-test-{uuid4()}"
    intent = KlmAuditEvent(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        actor_type="system",
        actor_label="Orquestração do CRM",
        event_type="funnel_move_automation_requested",
        entity_type="matter",
        entity_id=matter.id,
        summary="queued",
        correlation_id=operation_id,
        after_data={
            "columnId": "triagem_sdr",
            "agentsTriggered": ["crm", "orquestrador", "sdr"],
            "status": "queued",
        },
    )
    db_session.add(intent)
    await db_session.commit()

    calls = []

    async def execute_stub(self, **kwargs):
        calls.append(kwargs["agent_key"])
        return {"runs": [SimpleNamespace(id=uuid4())], "approvalId": None}

    monkeypatch.setattr(KlmAgentService, "execute", execute_stub)

    @asynccontextmanager
    async def worker_session():
        yield db_session

    monkeypatch.setattr(funnel_automation_tasks, "WorkerSessionLocal", worker_session)

    await funnel_automation_tasks._process(auth_context.workspace_id, operation_id)
    assert calls == ["crm", "orquestrador", "sdr"]
    events = (
        (
            await db_session.execute(
                select(KlmAuditEvent).where(KlmAuditEvent.correlation_id == operation_id)
            )
        )
        .scalars()
        .all()
    )
    assert any(event.event_type == "funnel_move_automation_completed" for event in events)

    calls.clear()
    await funnel_automation_tasks._process(auth_context.workspace_id, operation_id)
    assert calls == []


@pytest.mark.asyncio
async def test_funnel_worker_records_failed_intent(db_session, auth_context, monkeypatch):
    client = Client(workspace_id=auth_context.workspace_id, name="Worker failure client")
    db_session.add(client)
    await db_session.flush()
    matter = Matter(
        workspace_id=auth_context.workspace_id,
        client_id=client.id,
        practice_area=PracticeArea.saude_suplementar,
        demand_type="Worker failure",
    )
    db_session.add(matter)
    await db_session.flush()
    operation_id = f"worker-failure-{uuid4()}"
    intent = KlmAuditEvent(
        workspace_id=auth_context.workspace_id,
        actor_user_id=auth_context.user_id,
        actor_type="system",
        actor_label="Orquestração do CRM",
        event_type="funnel_move_automation_requested",
        entity_type="matter",
        entity_id=matter.id,
        summary="queued",
        correlation_id=operation_id,
        after_data={
            "columnId": "triagem_sdr",
            "agentsTriggered": ["crm", "orquestrador", "sdr"],
            "status": "queued",
        },
    )
    db_session.add(intent)
    await db_session.commit()

    async def fail_stub(self, **kwargs):
        raise TimeoutError("slow executor")

    monkeypatch.setattr(KlmAgentService, "execute", fail_stub)

    @asynccontextmanager
    async def worker_session():
        yield db_session

    monkeypatch.setattr(funnel_automation_tasks, "WorkerSessionLocal", worker_session)
    await funnel_automation_tasks._process(auth_context.workspace_id, operation_id)
    events = (
        (
            await db_session.execute(
                select(KlmAuditEvent).where(KlmAuditEvent.correlation_id == operation_id)
            )
        )
        .scalars()
        .all()
    )
    assert any(event.event_type == "funnel_move_automation_failed" for event in events)


@pytest.mark.asyncio
async def test_funnel_worker_abre_o_contexto_do_workspace_antes_de_ler(
    db_session, auth_context, monkeypatch
):
    """S6: a task recebia o workspace e filtrava por ele, mas nunca chamava
    set_workspace_context; sob FORCE RLS a leitura devolvia zero e toda automação
    pós-movimento retornava em silêncio. Em SQLite a leitura já passa sem contexto,
    então só o espião discrimina."""
    contextos: list = []

    async def espiao(db, workspace_id):
        contextos.append(workspace_id)

    monkeypatch.setattr(funnel_automation_tasks, "set_workspace_context", espiao)

    @asynccontextmanager
    async def sessao():
        yield db_session

    monkeypatch.setattr(funnel_automation_tasks, "WorkerSessionLocal", sessao)

    await funnel_automation_tasks._process(auth_context.workspace_id, f"sem-intent-{uuid4()}")

    assert contextos == [auth_context.workspace_id]
