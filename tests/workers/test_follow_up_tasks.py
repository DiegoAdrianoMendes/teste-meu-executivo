from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.klm_operations import FollowUpStatus, FollowUpTask, OmnichannelChannel
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.workers import follow_up_tasks


def _session_factory(db_session: AsyncSession):
    assert db_session.bind is not None
    return async_sessionmaker(
        db_session.bind,
        class_=AsyncSession,
        expire_on_commit=False,
    )


def _workspace(name: str, *, is_active: bool = True, deleted: bool = False) -> Workspace:
    return Workspace(
        name=name,
        slug=f"follow-up-worker-{uuid4().hex}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=is_active,
        deleted_at=datetime.now(UTC) if deleted else None,
        settings={},
    )


def _due_follow_up(workspace: Workspace, *, due_at: datetime, last_notified_at=None) -> FollowUpTask:
    return FollowUpTask(
        workspace_id=workspace.id,
        channel=OmnichannelChannel.whatsapp,
        status=FollowUpStatus.scheduled,
        due_at=due_at,
        reason="Retomar contato",
        last_notified_at=last_notified_at,
    )


@pytest.mark.asyncio
async def test_follow_up_dispatcher_enqueues_only_active_workspaces_with_due_items(
    db_session: AsyncSession,
    monkeypatch,
):
    active_a = _workspace("Follow-up A")
    active_b = _workspace("Follow-up B")
    inactive = _workspace("Follow-up inactive", is_active=False)
    deleted = _workspace("Follow-up deleted", deleted=True)
    already_notified_ws = _workspace("Follow-up already notified")
    not_yet_due_ws = _workspace("Follow-up not yet due")
    db_session.add_all(
        (active_a, active_b, inactive, deleted, already_notified_ws, not_yet_due_ws)
    )
    await db_session.flush()

    now = datetime.now(UTC)
    db_session.add_all(
        [
            _due_follow_up(active_a, due_at=now - timedelta(minutes=5)),
            _due_follow_up(active_b, due_at=now - timedelta(minutes=1)),
            _due_follow_up(inactive, due_at=now - timedelta(minutes=5)),
            _due_follow_up(deleted, due_at=now - timedelta(minutes=5)),
            _due_follow_up(
                already_notified_ws,
                due_at=now - timedelta(minutes=5),
                last_notified_at=now - timedelta(minutes=1),
            ),
            _due_follow_up(not_yet_due_ws, due_at=now + timedelta(hours=1)),
        ]
    )
    await db_session.commit()

    enqueued: list[str] = []
    monkeypatch.setattr(follow_up_tasks, "external_delivery_allowed", lambda: True)
    monkeypatch.setattr(
        follow_up_tasks.process_due,
        "apply_async",
        lambda **options: enqueued.append(options["kwargs"]["workspace_id"]),
    )

    count = await follow_up_tasks._async_dispatch_due_follow_ups(
        session_factory=_session_factory(db_session),
        now=now,
    )

    assert count == 2
    assert set(enqueued) == {str(active_a.id), str(active_b.id)}


@pytest.mark.asyncio
async def test_follow_up_process_due_sends_once_and_second_run_is_noop(
    db_session: AsyncSession,
    auth_context,
    monkeypatch,
):
    """Teste do tick: vencido -> enfileira 1x; rodar 2x não duplica."""
    sent_emails: list[str] = []

    async def fake_send_email(**kwargs):
        sent_emails.append(kwargs["to_email"])
        return True

    async def fake_send_whatsapp(self, to: str, body: str, **_kwargs):
        return True

    from app.models.user import User

    user = await db_session.get(User, auth_context.user_id)
    assert user is not None
    user.phone = "+5511999991111"

    workspace = await db_session.get(Workspace, auth_context.workspace_id)
    assert workspace is not None
    workspace.settings = {
        **(workspace.settings or {}),
        "notifications": {
            "whatsappEnabled": True,
            "emailEnabled": True,
            "dailyDigestHour": 7,
            "recipients": [
                {
                    "id": "ops-1",
                    "name": "Secretaria",
                    "email": "secretaria@example.com",
                    "phone": "+5511999992222",
                    "role": "custom",
                }
            ],
            "rules": [{"key": "followUpDue", "enabled": True}],
        },
    }

    now = datetime.now(UTC)
    follow_up = FollowUpTask(
        workspace_id=auth_context.workspace_id,
        channel=OmnichannelChannel.whatsapp,
        status=FollowUpStatus.scheduled,
        due_at=now - timedelta(minutes=10),
        reason="Retomar contato",
    )
    db_session.add(follow_up)
    await db_session.commit()

    monkeypatch.setattr(follow_up_tasks, "external_delivery_allowed", lambda: True)
    monkeypatch.setattr(
        "app.services.operational_alert_service.external_delivery_allowed",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.services.operational_alert_service.send_operational_alert_email",
        fake_send_email,
    )
    monkeypatch.setattr(
        "app.services.platform_whatsapp_alert_service.PlatformWhatsAppAlertService.send_text",
        fake_send_whatsapp,
    )

    session_factory = _session_factory(db_session)

    first = await follow_up_tasks._async_process_due_follow_ups(
        auth_context.workspace_id,
        session_factory=session_factory,
    )
    second = await follow_up_tasks._async_process_due_follow_ups(
        auth_context.workspace_id,
        session_factory=session_factory,
    )

    assert first == 1
    assert second == 0
    assert "secretaria@example.com" in sent_emails


@pytest.mark.asyncio
async def test_follow_up_workers_reject_invalid_workspace_before_database_access():
    def forbidden_session_factory():
        raise AssertionError("invalid workspace reached the database")

    with pytest.raises(ValueError, match="workspace_id"):
        await follow_up_tasks._async_process_due_follow_ups(
            "not-a-uuid", session_factory=forbidden_session_factory
        )


@pytest.mark.asyncio
async def test_follow_up_worker_rechecks_external_gate_before_database_access(monkeypatch):
    def forbidden_session_factory():
        raise AssertionError("blocked delivery reached the database")

    monkeypatch.setattr(follow_up_tasks, "external_delivery_allowed", lambda: False)

    assert (
        await follow_up_tasks._async_process_due_follow_ups(
            uuid4(), session_factory=forbidden_session_factory
        )
        == 0
    )


@pytest.mark.asyncio
async def test_follow_up_dispatcher_does_not_enqueue_when_external_gate_is_closed(monkeypatch):
    def forbidden_session_factory():
        raise AssertionError("blocked dispatcher reached the database")

    def forbidden_apply_async(*args, **kwargs):
        raise AssertionError("blocked dispatcher enqueued a follow-up notification")

    monkeypatch.setattr(follow_up_tasks, "external_delivery_allowed", lambda: False)
    monkeypatch.setattr(follow_up_tasks.process_due, "apply_async", forbidden_apply_async)

    assert (
        await follow_up_tasks._async_dispatch_due_follow_ups(
            session_factory=forbidden_session_factory
        )
        == 0
    )


@pytest.mark.asyncio
async def test_descoberta_de_follow_up_abre_o_escopo_de_descoberta(db_session, monkeypatch):
    """S6: sem o escopo, a leitura sob FORCE RLS devolve zero e o beat despacha nada.
    Em SQLite a leitura passa de qualquer jeito, então só o espião discrimina; a
    prova de verdade vive no harness PostgreSQL."""
    familias: list[str] = []

    async def espiao(session, *, family):
        familias.append(family)

    monkeypatch.setattr(follow_up_tasks, "set_worker_discovery_context", espiao)
    await follow_up_tasks._due_follow_up_workspace_ids(session_factory=_session_factory(db_session))
    assert familias == ["follow_up"]
