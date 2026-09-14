from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.db.tenant import get_workspace_context
from app.models.agent_run import AgentRun, AgentRunStatus
from app.models.client import Client
from app.models.human_override import HumanOverrideDecision
from app.models.matter import Matter, PracticeArea
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.services.agent_runtime_service import AgentRuntimeService
from app.services.knowledge_service import KnowledgeService
from app.services.workspace_policy_service import WorkspacePolicyService
from app.workers import agent_tasks
from app.workers.agent_tasks import (
    AGENT_RUNNING_STALE_TIMEOUT,
    _execute_agent_run_async,
    _finalize_owned_agent_run,
    _recover_agent_runs_async,
    execute_agent_run,
)


async def _queued_run(
    db_session,
    auth_context,
    *,
    agent_name: str,
    input_payload: dict | None = None,
) -> tuple[Matter, AgentRun]:
    client = Client(
        workspace_id=auth_context.workspace_id,
        name="Cliente Worker",
    )
    db_session.add(client)
    await db_session.flush()
    matter = Matter(
        workspace_id=auth_context.workspace_id,
        client_id=client.id,
        practice_area=PracticeArea.trabalhista,
        demand_type="Revisao contratual",
    )
    db_session.add(matter)
    await db_session.flush()
    run = AgentRun(
        workspace_id=auth_context.workspace_id,
        matter_id=matter.id,
        agent_name=agent_name,
        trigger_source="test:external-boundary",
        status=AgentRunStatus.queued,
        input_payload=input_payload or {},
        attempt_count=1,
    )
    db_session.add(run)
    await db_session.commit()
    return matter, run


def _session_factory(db_session):
    @asynccontextmanager
    async def factory():
        yield db_session

    return factory


async def _disable_broadcasts(monkeypatch):
    async def no_broadcast(*args, **kwargs):
        return None

    monkeypatch.setattr(AgentRuntimeService, "_broadcast_run_event", no_broadcast)


@pytest.mark.asyncio
async def test_worker_gate_records_waiting_human_and_duplicate_delivery_is_ignored(
    db_session,
    auth_context,
    monkeypatch,
):
    matter, run = await _queued_run(db_session, auth_context, agent_name="klm_drafter")
    await _disable_broadcasts(monkeypatch)
    monkeypatch.setattr(
        "app.workers.agent_tasks.model_api_execution_allowed",
        lambda: False,
    )

    first = await _execute_agent_run_async(
        str(run.id),
        "celery-first",
        workspace_id=auth_context.workspace_id,
        session_factory=_session_factory(db_session),
    )
    second = await _execute_agent_run_async(
        str(run.id),
        "celery-redelivery",
        workspace_id=auth_context.workspace_id,
        session_factory=_session_factory(db_session),
    )
    await db_session.refresh(run)
    await db_session.refresh(matter)

    assert first["status"] == "blocked"
    assert second["status"] == "ignored"
    assert run.status == AgentRunStatus.waiting_human
    assert run.output_payload["externalActionsExecuted"] == 0
    assert matter.needs_approval is True
    assert get_workspace_context(db_session) == auth_context.workspace_id


@pytest.mark.asyncio
async def test_drafter_records_zapsign_intent_without_creating_document_before_approval(
    db_session,
    auth_context,
    monkeypatch,
):
    matter, run = await _queued_run(db_session, auth_context, agent_name="klm_drafter")
    original_column = matter.column_id
    await _disable_broadcasts(monkeypatch)
    monkeypatch.setattr(
        "app.workers.agent_tasks.model_api_execution_allowed",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.workers.agent_tasks.external_delivery_allowed",
        lambda: False,
    )

    async def resolve_policy(self, workspace_id, stage_key):
        return {}

    draft_calls: list[dict] = []
    draft_document_id = str(uuid4())

    async def draft(*args, **kwargs):
        draft_calls.append({"args": args, "kwargs": kwargs})
        return {
            "message": "Minuta pronta para revisao humana.",
            "draft_persisted": True,
            "draft_document_id": draft_document_id,
            "draft_storage_version": 1,
        }

    monkeypatch.setattr(WorkspacePolicyService, "resolve_stage_policy", resolve_policy)
    monkeypatch.setattr("app.agents.stubs.klm_drafter.run_drafter", draft)
    zapsign_factory = AsyncMock(
        side_effect=AssertionError("ZapSign must not be created before human approval")
    )
    monkeypatch.setattr(
        "app.services.zapsign_service.get_zapsign_service",
        zapsign_factory,
    )

    result = await _execute_agent_run_async(
        str(run.id),
        "celery-drafter",
        workspace_id=auth_context.workspace_id,
        session_factory=_session_factory(db_session),
    )
    await db_session.refresh(run)
    await db_session.refresh(matter)

    assert result["status"] == "ok"
    assert run.status == AgentRunStatus.waiting_human
    assert run.output_payload["external_action_intent"] == {
        "provider": "zapsign",
        "action": "create_document",
        "status": "awaiting_human_approval",
        "draft_document_id": draft_document_id,
    }
    assert matter.zapsign_doc_token is None
    assert matter.zapsign_sign_url is None
    assert matter.zapsign_status is None
    assert matter.column_id == original_column
    assert draft_calls[0]["kwargs"]["workspace_id"] == auth_context.workspace_id
    zapsign_factory.assert_not_awaited()


@pytest.mark.asyncio
async def test_drafter_without_persisted_artifact_records_no_zapsign_intent(
    db_session,
    auth_context,
    monkeypatch,
):
    """Fail-closed: sem minuta persistida como documento, não nasce intenção ZapSign."""
    _, run = await _queued_run(db_session, auth_context, agent_name="klm_drafter")
    await _disable_broadcasts(monkeypatch)
    monkeypatch.setattr(
        "app.workers.agent_tasks.model_api_execution_allowed",
        lambda: True,
    )

    async def resolve_policy(self, workspace_id, stage_key):
        return {}

    async def draft(*args, **kwargs):
        return {
            "message": "Minuta pronta para revisao humana.",
            "draft_persisted": False,
            "draft_document_id": None,
            "draft_persistence_status": "falha_na_persistencia",
        }

    monkeypatch.setattr(WorkspacePolicyService, "resolve_stage_policy", resolve_policy)
    monkeypatch.setattr("app.agents.stubs.klm_drafter.run_drafter", draft)

    result = await _execute_agent_run_async(
        str(run.id),
        "celery-drafter-sem-artefato",
        workspace_id=auth_context.workspace_id,
        session_factory=_session_factory(db_session),
    )
    await db_session.refresh(run)

    assert result["status"] == "ok"
    assert run.status == AgentRunStatus.waiting_human
    assert "external_action_intent" not in run.output_payload


@pytest.mark.asyncio
async def test_successful_flow_agent_run_schedules_and_reconciles_flow_lifecycle(
    db_session,
    auth_context,
    monkeypatch,
):
    _, run = await _queued_run(
        db_session,
        auth_context,
        agent_name="klm_sdr",
        input_payload={"flow_run_id": str(uuid4())},
    )
    await _disable_broadcasts(monkeypatch)
    monkeypatch.setattr(
        "app.workers.agent_tasks.model_api_execution_allowed",
        lambda: True,
    )

    async def resolve_policy(self, workspace_id, stage_key):
        return {}

    async def run_sdr(*args, **kwargs):
        return {"summary": "AgentRun concluído"}

    scheduled: list[AgentRunStatus] = []
    reconciled: list[AgentRunStatus] = []

    def record_schedule(agent_run, *, countdown=1):
        scheduled.append(agent_run.status)

    async def record_reconcile(db, agent_run):
        reconciled.append(agent_run.status)

    monkeypatch.setattr(WorkspacePolicyService, "resolve_stage_policy", resolve_policy)
    monkeypatch.setattr("app.agents.stubs.klm_sdr.run_sdr", run_sdr)
    monkeypatch.setattr(
        "app.workers.agent_tasks._schedule_flow_agent_settlement",
        record_schedule,
    )
    monkeypatch.setattr(
        "app.workers.agent_tasks._settle_linked_flow_run",
        record_reconcile,
    )

    result = await _execute_agent_run_async(
        str(run.id),
        "celery-flow-success",
        workspace_id=auth_context.workspace_id,
        session_factory=_session_factory(db_session),
    )

    await db_session.refresh(run)
    assert result["status"] == "ok"
    assert run.status == AgentRunStatus.completed
    assert scheduled == [AgentRunStatus.completed]
    assert reconciled == [AgentRunStatus.completed]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("agent_name", "stub_path"),
    [
        ("klm_sdr", "app.agents.stubs.klm_sdr.run_sdr"),
        (
            "administrativo_ops",
            "app.agents.stubs.klm_onboarding_ops.run_onboarding_ops",
        ),
    ],
)
async def test_parse_failed_payload_marks_run_waiting_human_without_chained_dispatch(
    db_session,
    auth_context,
    monkeypatch,
    agent_name,
    stub_path,
):
    """Fail-closed: stub reporta parse_failed → run espera humano, sem dispatch em cadeia."""
    _, run = await _queued_run(db_session, auth_context, agent_name=agent_name)
    await _disable_broadcasts(monkeypatch)
    monkeypatch.setattr(
        "app.workers.agent_tasks.model_api_execution_allowed",
        lambda: True,
    )

    async def resolve_policy(self, workspace_id, stage_key):
        return {}

    async def parse_failed_stub(*args, **kwargs):
        return {
            "status": "waiting_human",
            "parse_failed": True,
            "message": "Resposta do modelo em formato JSON inválido ou incompleto.",
        }

    monkeypatch.setattr(WorkspacePolicyService, "resolve_stage_policy", resolve_policy)
    monkeypatch.setattr(stub_path, parse_failed_stub)
    drafter_dispatch = AsyncMock()
    monkeypatch.setattr("app.workers.event_bus.dispatch_drafter", drafter_dispatch)

    result = await _execute_agent_run_async(
        str(run.id),
        f"celery-parse-failed-{agent_name}",
        workspace_id=auth_context.workspace_id,
        session_factory=_session_factory(db_session),
    )
    await db_session.refresh(run)

    assert result["status"] == "ok"
    assert run.status == AgentRunStatus.waiting_human
    assert run.output_payload["parse_failed"] is True
    drafter_dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_agent_task_rejects_run_from_another_workspace(
    db_session,
    auth_context,
):
    _, run = await _queued_run(db_session, auth_context, agent_name="klm_drafter")
    other_workspace = Workspace(
        name="Outro workspace AgentTask",
        slug="agent-task-other-workspace",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(other_workspace)
    await db_session.commit()

    with pytest.raises(RuntimeError, match="não encontrado"):
        await _execute_agent_run_async(
            str(run.id),
            "celery-cross-tenant",
            workspace_id=other_workspace.id,
            session_factory=_session_factory(db_session),
        )

    assert run.status == AgentRunStatus.queued
    assert get_workspace_context(db_session) == other_workspace.id


def test_agent_task_uses_late_ack_and_worker_lost_redelivery_without_autoretry():
    assert getattr(execute_agent_run, "autoretry_for", None) is None
    assert execute_agent_run.max_retries == 0
    assert execute_agent_run.acks_late is True
    assert execute_agent_run.reject_on_worker_lost is True


@pytest.mark.asyncio
async def test_stale_running_recovery_requires_human_without_repeating_model_call(
    db_session,
    auth_context,
):
    _, run = await _queued_run(db_session, auth_context, agent_name="klm_drafter")
    old_task_id = "celery-worker-lost"
    now = datetime.now(UTC)
    run.status = AgentRunStatus.running
    run.celery_task_id = old_task_id
    run.started_at = now - AGENT_RUNNING_STALE_TIMEOUT - timedelta(seconds=1)
    run.updated_at = run.started_at
    run.output_payload = {
        "billingReconciliation": {
            "status": "reserved_before_provider",
            "requestStarted": False,
            "reconciliationRequired": False,
            "reservationId": "reservation-durable-1",
            "reservationDay": now.date().isoformat(),
            "reservedAmountUsd": "0.0075",
            "gatewayRequestId": "gateway-durable-1",
        },
        "modelCheckpoint": {"status": "reserved_before_provider"},
    }
    await db_session.commit()

    published: list[dict] = []
    result = await _recover_agent_runs_async(
        session_factory=_session_factory(db_session),
        workspace_ids=[auth_context.workspace_id],
        now=now,
        publisher=lambda **intent: published.append(intent),
    )
    await db_session.refresh(run)

    assert result == {
        "published": 0,
        "publish_failed": 0,
        "waiting_human": 1,
        "failed": 0,
    }
    assert run.status == AgentRunStatus.waiting_human
    assert run.celery_task_id == old_task_id
    assert run.attempt_count == 1
    assert run.output_payload["executionUncertain"] is True
    assert run.output_payload["automaticRetryBlocked"] is True
    billing = run.output_payload["billingReconciliation"]
    assert billing["status"] == "reconciliation_required"
    assert billing["reconciliationRequired"] is True
    assert billing["reservationId"] == "reservation-durable-1"
    assert billing["reservedAmountUsd"] == "0.0075"
    assert run.output_payload["modelCheckpoint"]["status"] == "execution_uncertain"
    assert published == []

    stale_delivery = await _execute_agent_run_async(
        str(run.id),
        old_task_id,
        workspace_id=auth_context.workspace_id,
        session_factory=_session_factory(db_session),
    )
    await db_session.refresh(run)

    assert stale_delivery == {
        "status": "ignored",
        "agent_run_id": str(run.id),
        "run_status": AgentRunStatus.waiting_human.value,
    }
    assert run.status == AgentRunStatus.waiting_human


@pytest.mark.asyncio
async def test_agent_recovery_zero_work_never_enters_idle_tenant_sessions(
    db_session,
    monkeypatch,
):
    db_session.add_all(
        Workspace(
            name=f"Agent idle {index}",
            slug=f"agent-idle-{uuid4().hex}",
            status=WorkspaceStatus.active,
            setup_mode=WorkspaceSetupMode.self_service,
            is_active=True,
            settings={},
        )
        for index in range(12)
    )
    await db_session.commit()

    async def forbidden_context(*args, **kwargs):
        raise AssertionError("idle agent tenant was entered")

    monkeypatch.setattr(agent_tasks, "set_workspace_context", forbidden_context)
    result = await _recover_agent_runs_async(
        session_factory=_session_factory(db_session),
        publisher=lambda **intent: (_ for _ in ()).throw(
            AssertionError(f"unexpected intent: {intent}")
        ),
    )

    assert result == {
        "published": 0,
        "publish_failed": 0,
        "waiting_human": 0,
        "failed": 0,
    }


@pytest.mark.asyncio
async def test_agent_recovery_batch_cap_and_repeated_runs_do_not_duplicate(
    db_session,
    auth_context,
    monkeypatch,
):
    runs = [
        (await _queued_run(db_session, auth_context, agent_name="klm_drafter"))[1]
        for _ in range(3)
    ]
    monkeypatch.setattr(agent_tasks, "AGENT_RECOVERY_RUN_BATCH_SIZE", 2)
    published: list[dict] = []

    first = await _recover_agent_runs_async(
        session_factory=_session_factory(db_session),
        publisher=lambda **intent: published.append(intent),
    )
    second = await _recover_agent_runs_async(
        session_factory=_session_factory(db_session),
        publisher=lambda **intent: published.append(intent),
    )

    assert first["published"] == 2
    assert second["published"] == 1
    assert len(published) == 3
    assert {intent["run_id"] for intent in published} == {run.id for run in runs}


@pytest.mark.asyncio
async def test_late_worker_result_cannot_overwrite_human_override(
    db_session,
    auth_context,
    monkeypatch,
):
    matter, run = await _queued_run(db_session, auth_context, agent_name="klm_drafter")
    run.status = AgentRunStatus.waiting_human
    run.celery_task_id = "celery-old-worker"
    run.output_payload = {"draft": "reviewed-version"}
    matter.needs_approval = True
    await db_session.commit()
    await _disable_broadcasts(monkeypatch)
    monkeypatch.setattr(
        KnowledgeService,
        "index_internal_conversation",
        AsyncMock(return_value=None),
    )

    runtime = AgentRuntimeService(db_session)
    await runtime.create_override(
        run=run,
        workspace_id=auth_context.workspace_id,
        decided_by_user_id=auth_context.user_id,
        decision=HumanOverrideDecision.accepted,
        notes="Versão revisada aprovada",
    )

    late_result = await _finalize_owned_agent_run(
        db_session,
        runtime,
        run_id=run.id,
        workspace_id=auth_context.workspace_id,
        celery_task_id="celery-old-worker",
        status=AgentRunStatus.completed,
        output_payload={"draft": "late-worker-version"},
    )
    await db_session.refresh(run)

    assert late_result is None
    assert run.status == AgentRunStatus.completed
    assert run.output_payload == {"draft": "reviewed-version"}


async def _seed_whatsapp_contact(db_session, workspace_id, *, inbound_hours_ago=None):
    from app.models.whatsapp import (
        WaMessageDirection,
        WhatsAppConnection,
        WhatsAppContact,
        WhatsAppMessage,
    )

    conn = WhatsAppConnection(workspace_id=workspace_id)
    db_session.add(conn)
    await db_session.flush()
    contact = WhatsAppContact(
        workspace_id=workspace_id,
        whatsapp_connection_id=conn.id,
        phone="+5511988887777",
    )
    db_session.add(contact)
    await db_session.flush()
    if inbound_hours_ago is not None:
        db_session.add(
            WhatsAppMessage(
                contact_id=contact.id,
                whatsapp_connection_id=conn.id,
                direction=WaMessageDirection.inbound,
                body="oi",
                timestamp=datetime.now(UTC) - timedelta(hours=inbound_hours_ago),
            )
        )
        await db_session.flush()
    return contact


@pytest.mark.asyncio
async def test_whatsapp_sdr_blocks_free_text_outside_24h_window(
    db_session,
    auth_context,
    monkeypatch,
):
    # A1: SDR do WhatsApp com canal externo aberto, mas SEM inbound recente,
    # não pode auto-enviar texto livre (janela de 24h fechada) — degrada.
    contact = await _seed_whatsapp_contact(
        db_session, auth_context.workspace_id, inbound_hours_ago=None
    )
    _, run = await _queued_run(
        db_session,
        auth_context,
        agent_name="klm_sdr",
        input_payload={"contact_id": str(contact.id), "inbound_text": "quero saber"},
    )
    await _disable_broadcasts(monkeypatch)
    monkeypatch.setattr(agent_tasks, "model_api_execution_allowed", lambda: True)
    monkeypatch.setattr(agent_tasks, "external_delivery_allowed", lambda: True)

    async def resolve_policy(self, workspace_id, stage_key):
        return {}

    async def fake_sdr_on_message(*args, **kwargs):
        return "Olá, posso ajudar?"

    sent_calls: list[str] = []

    async def fail_if_sent(self, *, phone, body, workspace_id):
        sent_calls.append(body)
        return True

    monkeypatch.setattr(WorkspacePolicyService, "resolve_stage_policy", resolve_policy)
    monkeypatch.setattr(
        "app.agents.stubs.klm_sdr.run_sdr_on_message", fake_sdr_on_message
    )
    monkeypatch.setattr(
        "app.services.whatsapp_service.WhatsAppService._send_text_direct",
        fail_if_sent,
    )

    result = await _execute_agent_run_async(
        str(run.id),
        "celery-sdr-window-closed",
        workspace_id=auth_context.workspace_id,
        session_factory=_session_factory(db_session),
    )

    await db_session.refresh(run)
    assert result["status"] == "ok"
    assert sent_calls == []
    assert run.output_payload["delivery_status"] == "blocked_by_policy_window"
    assert run.output_payload["externalActionsExecuted"] == 0


@pytest.mark.asyncio
async def test_whatsapp_sdr_sends_free_text_inside_24h_window(
    db_session,
    auth_context,
    monkeypatch,
):
    # A1: com inbound recente (janela aberta) + canal aberto, o SDR auto-envia.
    contact = await _seed_whatsapp_contact(
        db_session, auth_context.workspace_id, inbound_hours_ago=1
    )
    _, run = await _queued_run(
        db_session,
        auth_context,
        agent_name="klm_sdr",
        input_payload={"contact_id": str(contact.id), "inbound_text": "quero saber"},
    )
    await _disable_broadcasts(monkeypatch)
    monkeypatch.setattr(agent_tasks, "model_api_execution_allowed", lambda: True)
    monkeypatch.setattr(agent_tasks, "external_delivery_allowed", lambda: True)

    async def resolve_policy(self, workspace_id, stage_key):
        return {}

    async def fake_sdr_on_message(*args, **kwargs):
        return "Olá, posso ajudar?"

    sent_calls: list[str] = []

    async def record_sent(self, *, phone, body, workspace_id, idempotency_key):
        sent_calls.append(body)
        assert idempotency_key == f"agent-run-reply:{run.id}"
        return True

    monkeypatch.setattr(WorkspacePolicyService, "resolve_stage_policy", resolve_policy)
    monkeypatch.setattr(
        "app.agents.stubs.klm_sdr.run_sdr_on_message", fake_sdr_on_message
    )
    monkeypatch.setattr(
        "app.services.whatsapp_service.WhatsAppService._send_text_direct",
        record_sent,
    )

    result = await _execute_agent_run_async(
        str(run.id),
        "celery-sdr-window-open",
        workspace_id=auth_context.workspace_id,
        session_factory=_session_factory(db_session),
    )

    await db_session.refresh(run)
    assert result["status"] == "ok"
    assert sent_calls == ["Olá, posso ajudar?"]
    assert run.output_payload["delivery_status"] == "sent"
    assert run.output_payload["externalActionsExecuted"] == 1


@pytest.mark.asyncio
async def test_whatsapp_sdr_nao_roda_nem_envia_com_humano_na_conversa(
    db_session, auth_context, monkeypatch
):
    """S1: contato em atendimento humano: o SDR nem chama o modelo nem envia."""
    from app.models.whatsapp import WaQueueStatus

    contact = await _seed_whatsapp_contact(
        db_session, auth_context.workspace_id, inbound_hours_ago=1
    )
    contact.queue_status = WaQueueStatus.human
    await db_session.flush()
    _, run = await _queued_run(
        db_session,
        auth_context,
        agent_name="klm_sdr",
        input_payload={"contact_id": str(contact.id), "inbound_text": "quero saber"},
    )
    await _disable_broadcasts(monkeypatch)
    monkeypatch.setattr(agent_tasks, "model_api_execution_allowed", lambda: True)
    monkeypatch.setattr(agent_tasks, "external_delivery_allowed", lambda: True)

    async def resolve_policy(self, workspace_id, stage_key):
        return {}

    modelo_chamado: list[bool] = []

    async def fake_sdr_on_message(*args, **kwargs):
        modelo_chamado.append(True)
        return "Olá, posso ajudar?"

    sent_calls: list[str] = []

    async def fail_if_sent(self, *, phone, body, workspace_id, **kwargs):
        sent_calls.append(body)
        return True

    monkeypatch.setattr(WorkspacePolicyService, "resolve_stage_policy", resolve_policy)
    monkeypatch.setattr("app.agents.stubs.klm_sdr.run_sdr_on_message", fake_sdr_on_message)
    monkeypatch.setattr(
        "app.services.whatsapp_service.WhatsAppService._send_text_direct", fail_if_sent
    )

    result = await _execute_agent_run_async(
        str(run.id),
        "celery-sdr-humano",
        workspace_id=auth_context.workspace_id,
        session_factory=_session_factory(db_session),
    )

    await db_session.refresh(run)
    assert result["status"] == "ok"
    assert modelo_chamado == []
    assert sent_calls == []
    assert run.output_payload["delivery_status"] == "blocked_by_human"
    assert run.output_payload["externalActionsExecuted"] == 0


@pytest.mark.asyncio
async def test_whatsapp_sdr_checkpoint_pos_modelo_barra_envio_apos_escalada(
    db_session, auth_context, monkeypatch
):
    """S1: a escalada da mesma mensagem acontece enquanto o modelo roda; a
    releitura do contato antes do envio barra a resposta."""
    from app.models.whatsapp import WaQueueStatus

    contact = await _seed_whatsapp_contact(
        db_session, auth_context.workspace_id, inbound_hours_ago=1
    )
    _, run = await _queued_run(
        db_session,
        auth_context,
        agent_name="klm_sdr",
        input_payload={"contact_id": str(contact.id), "inbound_text": "quero um advogado"},
    )
    await _disable_broadcasts(monkeypatch)
    monkeypatch.setattr(agent_tasks, "model_api_execution_allowed", lambda: True)
    monkeypatch.setattr(agent_tasks, "external_delivery_allowed", lambda: True)

    async def resolve_policy(self, workspace_id, stage_key):
        return {}

    async def fake_sdr_on_message(*args, **kwargs):
        # A freshness em produção vem de sessão nova por run (WorkerSessionLocal);
        # aqui o teste compartilha a sessão, e a releitura por refresh prova o
        # checkpoint, não o isolamento.
        contact.queue_status = WaQueueStatus.human
        await db_session.flush()
        return "Olá, posso ajudar?"

    sent_calls: list[str] = []

    async def fail_if_sent(self, *, phone, body, workspace_id, **kwargs):
        sent_calls.append(body)
        return True

    monkeypatch.setattr(WorkspacePolicyService, "resolve_stage_policy", resolve_policy)
    monkeypatch.setattr("app.agents.stubs.klm_sdr.run_sdr_on_message", fake_sdr_on_message)
    monkeypatch.setattr(
        "app.services.whatsapp_service.WhatsAppService._send_text_direct", fail_if_sent
    )

    result = await _execute_agent_run_async(
        str(run.id),
        "celery-sdr-corrida",
        workspace_id=auth_context.workspace_id,
        session_factory=_session_factory(db_session),
    )

    await db_session.refresh(run)
    assert result["status"] == "ok"
    assert sent_calls == []
    assert run.output_payload["delivery_status"] == "blocked_by_human"
