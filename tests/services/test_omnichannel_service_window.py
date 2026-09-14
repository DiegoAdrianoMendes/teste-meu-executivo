from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.klm_operations import (
    MessageAuthorType,
    MessageDeliveryStatus,
    MessageDirection,
    OmnichannelChannel,
    OmnichannelMessage,
    OmnichannelThread,
    ThreadStatus,
)
from app.models.omnichannel_v6 import (
    OmnichannelChannelConnection,
    OmnichannelIdentity,
)
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.schemas.klm_operations import OmnichannelMessageCreate
from app.services.klm_operations_service import KlmOperationsService
from app.services.omnichannel_service_window import latest_inbound_at_for_route


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


@pytest.mark.asyncio
async def test_service_window_is_scoped_to_identity_channel_and_connection(
    db_session: AsyncSession,
):
    workspace = Workspace(
        name="Service window",
        slug=f"service-window-{uuid4().hex}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(workspace)
    await db_session.flush()
    identity = OmnichannelIdentity(
        workspace_id=workspace.id,
        display_name="Cliente",
        source="test",
        identity_metadata={},
    )
    db_session.add(identity)
    await db_session.flush()
    thread = OmnichannelThread(
        workspace_id=workspace.id,
        identity_id=identity.id,
        primary_channel=OmnichannelChannel.whatsapp,
        status=ThreadStatus.open,
        subject="Janela por rota",
        policy_state={},
        cost_state={},
        thread_metadata={},
    )
    db_session.add(thread)
    await db_session.flush()

    selected_connection = OmnichannelChannelConnection(
        workspace_id=workspace.id,
        channel=OmnichannelChannel.whatsapp.value,
        provider="meta_cloud",
        connection_key=f"wa-selected-{uuid4().hex}",
        display_name="WhatsApp selecionado",
        status="connected",
        routing_policy={},
        capabilities={},
    )
    other_connection = OmnichannelChannelConnection(
        workspace_id=workspace.id,
        channel=OmnichannelChannel.whatsapp.value,
        provider="meta_cloud",
        connection_key=f"wa-other-{uuid4().hex}",
        display_name="Outro WhatsApp",
        status="connected",
        routing_policy={},
        capabilities={},
    )
    instagram_connection = OmnichannelChannelConnection(
        workspace_id=workspace.id,
        channel=OmnichannelChannel.instagram.value,
        provider="meta_cloud",
        connection_key=f"ig-{uuid4().hex}",
        display_name="Instagram",
        status="connected",
        routing_policy={},
        capabilities={},
    )
    db_session.add_all(
        [selected_connection, other_connection, instagram_connection]
    )
    await db_session.flush()

    now = datetime.now(UTC)

    def inbound(
        *,
        channel: OmnichannelChannel,
        connection_id,
        occurred_at: datetime,
        suffix: str,
    ) -> OmnichannelMessage:
        return OmnichannelMessage(
            workspace_id=workspace.id,
            thread_id=thread.id,
            connection_id=connection_id,
            channel=channel,
            direction=MessageDirection.inbound,
            author_type=MessageAuthorType.client,
            author_label="Cliente",
            body=suffix,
            idempotency_key=f"window-{suffix}-{uuid4().hex}",
            delivery_status=MessageDeliveryStatus.received,
            occurred_at=occurred_at,
        )

    selected_old = now - timedelta(hours=25)
    db_session.add_all(
        [
            inbound(
                channel=OmnichannelChannel.whatsapp,
                connection_id=selected_connection.id,
                occurred_at=selected_old,
                suffix="selected-old",
            ),
            inbound(
                channel=OmnichannelChannel.whatsapp,
                connection_id=other_connection.id,
                occurred_at=now - timedelta(hours=1),
                suffix="other-connection",
            ),
            inbound(
                channel=OmnichannelChannel.instagram,
                connection_id=instagram_connection.id,
                occurred_at=now - timedelta(minutes=30),
                suffix="other-channel",
            ),
        ]
    )
    await db_session.flush()

    route_latest = await latest_inbound_at_for_route(
        db_session,
        workspace_id=workspace.id,
        thread=thread,
        channel=OmnichannelChannel.whatsapp,
        connection_id=selected_connection.id,
    )
    assert route_latest is not None
    assert _aware(route_latest) == selected_old

    selected_new = now - timedelta(hours=2)
    db_session.add_all(
        [
            inbound(
                channel=OmnichannelChannel.whatsapp,
                connection_id=selected_connection.id,
                occurred_at=selected_new,
                suffix="selected-new",
            ),
            inbound(
                channel=OmnichannelChannel.whatsapp,
                connection_id=selected_connection.id,
                occurred_at=now - timedelta(days=3),
                suffix="selected-stale",
            ),
        ]
    )
    await db_session.flush()

    route_latest = await latest_inbound_at_for_route(
        db_session,
        workspace_id=workspace.id,
        thread=thread,
        channel=OmnichannelChannel.whatsapp,
        connection_id=selected_connection.id,
    )
    assert route_latest is not None
    assert _aware(route_latest) == selected_new


@pytest.mark.asyncio
async def test_old_inbound_does_not_regress_thread_timestamp_or_preferred_channel(
    db_session: AsyncSession,
):
    workspace = Workspace(
        name="Monotonic inbound",
        slug=f"monotonic-inbound-{uuid4().hex}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(workspace)
    await db_session.flush()
    thread = OmnichannelThread(
        workspace_id=workspace.id,
        primary_channel=OmnichannelChannel.whatsapp,
        status=ThreadStatus.open,
        subject="Timestamp monotônico",
        policy_state={},
        cost_state={},
        thread_metadata={},
    )
    db_session.add(thread)
    await db_session.flush()

    newest = datetime.now(UTC) - timedelta(minutes=5)
    service = KlmOperationsService(db_session)
    await service.create_message(
        workspace.id,
        thread.id,
        None,
        OmnichannelMessageCreate(
            channel=OmnichannelChannel.whatsapp,
            direction=MessageDirection.inbound,
            author_type=MessageAuthorType.client,
            author_label="Cliente",
            body="Evento mais novo",
            idempotency_key=f"new-{uuid4().hex}",
            occurred_at=newest,
        ),
        trusted_external_receipt=True,
    )
    await service.create_message(
        workspace.id,
        thread.id,
        None,
        OmnichannelMessageCreate(
            channel=OmnichannelChannel.instagram,
            direction=MessageDirection.inbound,
            author_type=MessageAuthorType.client,
            author_label="Cliente",
            body="Evento atrasado",
            idempotency_key=f"old-{uuid4().hex}",
            occurred_at=newest - timedelta(days=2),
        ),
        trusted_external_receipt=True,
    )
    await db_session.flush()
    await db_session.refresh(thread)

    assert thread.last_inbound_at is not None
    assert _aware(thread.last_inbound_at) == newest
    assert thread.preferred_channel == OmnichannelChannel.whatsapp.value
