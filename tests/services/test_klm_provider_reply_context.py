"""Tenant and route contracts for inbound provider reply context."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.models.conversation_collaboration import ConversationMessageReference
from app.models.klm_operations import (
    KlmAuditEvent,
    MessageAuthorType,
    MessageDeliveryStatus,
    MessageDirection,
    OmnichannelChannel,
    OmnichannelMessage,
    OmnichannelThread,
    ThreadStatus,
)
from app.models.omnichannel_v6 import OmnichannelChannelConnection
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.services.klm_operations_service import KlmOperationsService


def _message(
    *,
    workspace_id,
    thread_id,
    connection_id,
    idempotency_key,
    external_message_id,
    direction=MessageDirection.inbound,
):
    return OmnichannelMessage(
        workspace_id=workspace_id,
        thread_id=thread_id,
        connection_id=connection_id,
        channel=OmnichannelChannel.whatsapp,
        direction=direction,
        author_type=(
            MessageAuthorType.client
            if direction == MessageDirection.inbound
            else MessageAuthorType.human
        ),
        author_label="Contato" if direction == MessageDirection.inbound else "Equipe",
        body="Mensagem contextual",
        idempotency_key=idempotency_key,
        external_message_id=external_message_id,
        delivery_status=(
            MessageDeliveryStatus.received
            if direction == MessageDirection.inbound
            else MessageDeliveryStatus.sent
        ),
        occurred_at=datetime.now(UTC),
        message_metadata={},
    )


@pytest.mark.asyncio
async def test_provider_reply_context_is_tenant_thread_and_route_scoped(
    db_session,
    auth_context,
):
    workspace_id = auth_context.workspace_id
    current_thread = OmnichannelThread(
        workspace_id=workspace_id,
        primary_channel=OmnichannelChannel.whatsapp,
        status=ThreadStatus.open,
        subject="Conversa atual",
    )
    other_thread = OmnichannelThread(
        workspace_id=workspace_id,
        primary_channel=OmnichannelChannel.whatsapp,
        status=ThreadStatus.open,
        subject="Outra conversa",
    )
    connection_a = OmnichannelChannelConnection(
        workspace_id=workspace_id,
        channel="whatsapp",
        provider="meta_cloud",
        connection_key=f"whatsapp:context-a:{uuid4().hex[:8]}",
        display_name="WhatsApp A",
        transport_mode="official_api",
    )
    connection_b = OmnichannelChannelConnection(
        workspace_id=workspace_id,
        channel="whatsapp",
        provider="meta_cloud",
        connection_key=f"whatsapp:context-b:{uuid4().hex[:8]}",
        display_name="WhatsApp B",
        transport_mode="official_api",
    )
    foreign_workspace = Workspace(
        name="Workspace de outro tenant",
        slug=f"foreign-context-{uuid4().hex[:10]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add_all(
        [
            current_thread,
            other_thread,
            connection_a,
            connection_b,
            foreign_workspace,
        ]
    )
    await db_session.flush()
    foreign_thread = OmnichannelThread(
        workspace_id=foreign_workspace.id,
        primary_channel=OmnichannelChannel.whatsapp,
        status=ThreadStatus.open,
        subject="Conversa externa",
    )
    db_session.add(foreign_thread)
    await db_session.flush()

    provider_context_id = "wamid.provider.context.target"
    source = _message(
        workspace_id=workspace_id,
        thread_id=current_thread.id,
        connection_id=connection_a.id,
        idempotency_key="provider-context-source",
        external_message_id="wamid.provider.reply.source",
    )
    wrong_thread_target = _message(
        workspace_id=workspace_id,
        thread_id=other_thread.id,
        connection_id=connection_a.id,
        idempotency_key="provider-context-wrong-thread",
        external_message_id=provider_context_id,
        direction=MessageDirection.outbound,
    )
    wrong_route_target = _message(
        workspace_id=workspace_id,
        thread_id=current_thread.id,
        connection_id=connection_b.id,
        idempotency_key="provider-context-wrong-route",
        external_message_id=provider_context_id,
        direction=MessageDirection.outbound,
    )
    foreign_target = _message(
        workspace_id=foreign_workspace.id,
        thread_id=foreign_thread.id,
        connection_id=None,
        idempotency_key="provider-context-foreign",
        external_message_id=provider_context_id,
        direction=MessageDirection.outbound,
    )
    db_session.add_all(
        [source, wrong_thread_target, wrong_route_target, foreign_target]
    )
    await db_session.flush()

    service = KlmOperationsService(db_session)
    correlation_id = "provider-context:" + ("x" * 300)
    unresolved = await service.resolve_provider_reply_context(
        workspace_id=workspace_id,
        thread_id=current_thread.id,
        source_message_id=source.id,
        provider_message_id=provider_context_id,
        connection_id=connection_a.id,
        correlation_id=correlation_id,
    )
    assert unresolved is None

    correct_target = _message(
        workspace_id=workspace_id,
        thread_id=current_thread.id,
        connection_id=connection_a.id,
        idempotency_key="provider-context-correct",
        external_message_id=provider_context_id,
        direction=MessageDirection.outbound,
    )
    db_session.add(correct_target)
    await db_session.flush()
    reference = await service.resolve_provider_reply_context(
        workspace_id=workspace_id,
        thread_id=current_thread.id,
        source_message_id=source.id,
        provider_message_id=provider_context_id,
        connection_id=connection_a.id,
        correlation_id=correlation_id,
    )
    replay = await service.resolve_provider_reply_context(
        workspace_id=workspace_id,
        thread_id=current_thread.id,
        source_message_id=source.id,
        provider_message_id=provider_context_id,
        connection_id=connection_a.id,
        correlation_id=correlation_id,
    )

    assert reference is not None
    assert replay is not None and replay.id == reference.id
    assert reference.source_message_id == source.id
    assert reference.target_message_id == correct_target.id
    audit = await db_session.scalar(
        select(KlmAuditEvent).where(
            KlmAuditEvent.event_type == "conversation_provider_reply_resolved",
            KlmAuditEvent.entity_id == reference.id,
        )
    )
    assert audit is not None
    assert audit.correlation_id == reference.idempotency_key
    assert len(audit.correlation_id) <= 120
    assert int(
        await db_session.scalar(
            select(func.count()).select_from(ConversationMessageReference).where(
                ConversationMessageReference.source_message_id == source.id
            )
        )
        or 0
    ) == 1
