from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.models.klm_operations import (
    MessageAuthorType,
    MessageDeliveryStatus,
    MessageDirection,
    OmnichannelChannel,
    OmnichannelMessage,
)
from app.models.omnichannel_v6 import (
    OmnichannelAuditFinding,
    OmnichannelScheduledMessage,
)
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.schemas.klm_operations import OmnichannelMessageCreate, OmnichannelThreadCreate
from app.schemas.omnichannel_v6 import OmnichannelAuditFindingAction
from app.services.conversation_auditor_service import (
    ConversationAuditVersionConflictError,
    ConversationAuditorService,
)
from app.services.klm_operations_service import KlmOperationsService


async def _thread(db_session, auth_context, *, subject: str = "Auditoria"):
    return await KlmOperationsService(db_session).create_thread(
        auth_context.workspace_id,
        auth_context.user_id,
        OmnichannelThreadCreate(
            primary_channel=OmnichannelChannel.whatsapp,
            subject=subject,
        ),
    )


async def _message(
    db_session,
    auth_context,
    thread_id,
    *,
    direction: MessageDirection,
    author_type: MessageAuthorType,
    body: str,
    occurred_at: datetime,
):
    return (
        await KlmOperationsService(db_session).create_message(
            auth_context.workspace_id,
            thread_id,
            auth_context.user_id if author_type == MessageAuthorType.human else None,
            OmnichannelMessageCreate(
                channel=OmnichannelChannel.whatsapp,
                direction=direction,
                author_type=author_type,
                author_label="Lead" if author_type == MessageAuthorType.client else "Agente KLM",
                body=body,
                occurred_at=occurred_at,
                idempotency_key=f"test-audit:{thread_id}:{direction.value}:{occurred_at.timestamp()}",
            ),
        )
    )[0]


@pytest.mark.asyncio
async def test_unanswered_lead_is_deduplicated_and_creates_one_internal_note(
    db_session,
    auth_context,
):
    thread = await _thread(db_session, auth_context)
    now = datetime.now(UTC)
    inbound = await _message(
        db_session,
        auth_context,
        thread.id,
        direction=MessageDirection.inbound,
        author_type=MessageAuthorType.client,
        body="Ainda estou aguardando retorno.",
        occurred_at=now - timedelta(minutes=12),
    )
    service = ConversationAuditorService(db_session)

    first = await service.scan_workspace(
        auth_context.workspace_id,
        thread_id=thread.id,
        now=now,
    )
    second = await service.scan_workspace(
        auth_context.workspace_id,
        thread_id=thread.id,
        now=now + timedelta(minutes=1),
    )

    assert first.coverage_percent == 0
    assert first.response_coverage_percent == 0
    assert second.open_count == 1
    finding = await db_session.scalar(select(OmnichannelAuditFinding))
    assert finding is not None
    assert finding.finding_type == "unanswered_lead"
    assert finding.message_id == inbound.id
    assert finding.internal_note_message_id is not None
    assert (
        await db_session.scalar(
            select(func.count(OmnichannelMessage.id)).where(
                OmnichannelMessage.direction == MessageDirection.internal,
                OmnichannelMessage.author_label == "Auditor KLM",
            )
        )
        == 1
    )


@pytest.mark.asyncio
async def test_local_draft_is_never_reported_as_delivery_failure(db_session, auth_context):
    thread = await _thread(db_session, auth_context)
    now = datetime.now(UTC)
    message = await _message(
        db_session,
        auth_context,
        thread.id,
        direction=MessageDirection.outbound,
        author_type=MessageAuthorType.human,
        body="Mensagem salva apenas no ambiente local.",
        occurred_at=now - timedelta(hours=2),
    )
    assert message.delivery_status == MessageDeliveryStatus.local_draft

    dashboard = await ConversationAuditorService(db_session).scan_workspace(
        auth_context.workspace_id,
        thread_id=thread.id,
        now=now,
    )

    assert dashboard.delivery_integrity_percent == 100
    assert not dashboard.findings


@pytest.mark.asyncio
async def test_failed_delivery_records_evidence_but_local_gate_prevents_resend(
    db_session,
    auth_context,
):
    thread = await _thread(db_session, auth_context)
    now = datetime.now(UTC)
    message = await _message(
        db_session,
        auth_context,
        thread.id,
        direction=MessageDirection.outbound,
        author_type=MessageAuthorType.agent,
        body="Retorno importante ao lead.",
        occurred_at=now - timedelta(minutes=20),
    )
    message.delivery_status = MessageDeliveryStatus.failed
    await db_session.flush()

    dashboard = await ConversationAuditorService(db_session).scan_workspace(
        auth_context.workspace_id,
        thread_id=thread.id,
        now=now,
    )

    finding = next(item for item in dashboard.findings if item.finding_type == "delivery_failed")
    assert finding.state == "waiting_human"
    assert finding.remediation["externalActionsExecuted"] == 0
    assert "ambiente local" in finding.remediation["retryBlockedReason"]
    assert await db_session.scalar(select(func.count(OmnichannelScheduledMessage.id))) == 0


@pytest.mark.asyncio
async def test_projection_without_provider_id_requires_human_review(db_session, auth_context):
    thread = await _thread(db_session, auth_context)
    now = datetime.now(UTC)
    message = await _message(
        db_session,
        auth_context,
        thread.id,
        direction=MessageDirection.outbound,
        author_type=MessageAuthorType.human,
        body="Mensagem supostamente enviada.",
        occurred_at=now - timedelta(minutes=3),
    )
    message.delivery_status = MessageDeliveryStatus.sent
    await db_session.flush()

    dashboard = await ConversationAuditorService(db_session).scan_workspace(
        auth_context.workspace_id,
        thread_id=thread.id,
        now=now,
    )

    assert dashboard.delivery_integrity_percent == 0
    finding = next(item for item in dashboard.findings if item.finding_type == "projection_mismatch")
    assert finding.state == "waiting_human"
    assert finding.remediation["automaticRetryEligible"] is False


@pytest.mark.asyncio
async def test_manual_resolution_uses_version_lock(db_session, auth_context):
    thread = await _thread(db_session, auth_context)
    now = datetime.now(UTC)
    await _message(
        db_session,
        auth_context,
        thread.id,
        direction=MessageDirection.inbound,
        author_type=MessageAuthorType.client,
        body="Alguém pode responder?",
        occurred_at=now - timedelta(minutes=8),
    )
    service = ConversationAuditorService(db_session)
    dashboard = await service.scan_workspace(
        auth_context.workspace_id,
        thread_id=thread.id,
        now=now,
    )
    finding = dashboard.findings[0]

    resolved = await service.act_on_finding(
        auth_context.workspace_id,
        finding.id,
        OmnichannelAuditFindingAction(
            action="resolve",
            expected_version=finding.version,
            reason="Resposta confirmada por outro canal.",
        ),
        user_id=auth_context.user_id,
    )

    assert resolved.state == "resolved"
    with pytest.raises(ConversationAuditVersionConflictError):
        await service.act_on_finding(
            auth_context.workspace_id,
            finding.id,
            OmnichannelAuditFindingAction(
                action="dismiss",
                expected_version=finding.version,
            ),
            user_id=auth_context.user_id,
        )


@pytest.mark.asyncio
async def test_auditor_never_crosses_workspace_boundary(db_session, auth_context):
    other = Workspace(
        name="Outro workspace",
        slug="outro-workspace-auditor",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(other)
    await db_session.flush()
    own_thread = await _thread(db_session, auth_context, subject="Workspace principal")
    other_thread = await KlmOperationsService(db_session).create_thread(
        other.id,
        None,
        OmnichannelThreadCreate(
            primary_channel=OmnichannelChannel.whatsapp,
            subject="Outro tenant",
        ),
        actor_type="system",
        actor_label="Fixture",
    )
    now = datetime.now(UTC)
    await _message(
        db_session,
        auth_context,
        own_thread.id,
        direction=MessageDirection.inbound,
        author_type=MessageAuthorType.client,
        body="Mensagem do workspace principal.",
        occurred_at=now - timedelta(minutes=8),
    )
    await KlmOperationsService(db_session).create_message(
        other.id,
        other_thread.id,
        None,
        OmnichannelMessageCreate(
            channel=OmnichannelChannel.whatsapp,
            direction=MessageDirection.inbound,
            author_type=MessageAuthorType.client,
            author_label="Outro lead",
            body="Mensagem de outro tenant.",
            occurred_at=now - timedelta(minutes=8),
            idempotency_key="other-tenant-audit-message",
        ),
    )

    dashboard = await ConversationAuditorService(db_session).scan_workspace(
        auth_context.workspace_id,
        now=now,
    )

    assert {finding.thread_id for finding in dashboard.findings} == {own_thread.id}
    assert await db_session.scalar(
        select(func.count(OmnichannelAuditFinding.id)).where(
            OmnichannelAuditFinding.workspace_id == other.id
        )
    ) == 0
