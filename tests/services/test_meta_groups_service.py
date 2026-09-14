from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import func, select

import app.services.meta_groups_service as meta_groups_service_module
from app.db.append_only import ImmutableRecordError
from app.models.omnichannel_v6 import (
    MetaGroup,
    MetaGroupLifecycleEvent,
    MetaGroupOperation,
    MetaGroupParticipant,
    OmnichannelChannelConnection,
)
from app.schemas.meta_groups import (
    MetaGroupCapabilityConfigure,
    MetaGroupCreate,
    MetaGroupLifecycleIngest,
    MetaGroupMessagePrepare,
    MetaGroupOfficialCreationPrepare,
)
from app.services.meta_groups_service import (
    META_GROUP_REQUIRED_WEBHOOK_FIELDS,
    MetaGroupNotFoundError,
    MetaGroupService,
    MetaGroupValidationError,
)
from app.services.whatsapp.meta_cloud_client import MetaCloudClient


async def _connection(
    db_session,
    workspace_id,
    *,
    key: str = "meta-groups-primary",
    transport_mode: str = "cloud_api",
) -> OmnichannelChannelConnection:
    connection = OmnichannelChannelConnection(
        workspace_id=workspace_id,
        channel="whatsapp",
        provider="meta_cloud",
        connection_key=key,
        display_name=f"WhatsApp {key}",
        external_account_id=f"phone-{key}",
        transport_mode=transport_mode,
        status="configured_local",
        configuration={"phoneNumberId": f"phone-{key}", "wabaId": f"waba-{key}"},
    )
    db_session.add(connection)
    await db_session.flush()
    return connection


async def _configure_capability(
    service: MetaGroupService,
    *,
    workspace_id,
    connection: OmnichannelChannelConnection,
    actor_user_id,
    eligible: bool,
    expected_version: int = 0,
    key: str,
):
    return await service.configure_capability(
        workspace_id=workspace_id,
        connection_id=connection.id,
        actor_user_id=actor_user_id,
        idempotency_key=key,
        payload=MetaGroupCapabilityConfigure(
            expected_version=expected_version,
            phone_number_id=connection.external_account_id,
            waba_id=f"waba-{connection.connection_key}",
            developer_app_configured=True,
            webhook_callback_verified=True,
            subscribed_webhook_fields=list(META_GROUP_REQUIRED_WEBHOOK_FIELDS),
            oba_eligibility_state="eligible" if eligible else "unknown",
            oba_evidence_source="meta_api" if eligible else "unverified",
            oba_evidence_reference="meta-evidence-fixture" if eligible else None,
            oba_evidence_recorded_at=datetime.now(UTC) if eligible else None,
        ),
    )


@pytest.mark.asyncio
async def test_capability_gate_separates_verified_capability_from_execution_runtime(
    db_session,
    auth_context,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        meta_groups_service_module,
        "external_delivery_allowed",
        lambda: False,
    )
    connection = await _connection(db_session, auth_context.workspace_id)
    service = MetaGroupService(db_session)

    with pytest.raises(MetaGroupNotFoundError):
        await service.capability_gate(uuid4(), connection.id)

    initial = await service.capability_gate(
        auth_context.workspace_id,
        connection.id,
    )
    assert initial.developer_app_ready is False
    assert initial.oba_gate_open is False
    assert initial.external_capability_gate_open is False

    app_ready = await _configure_capability(
        service,
        workspace_id=auth_context.workspace_id,
        connection=connection,
        actor_user_id=auth_context.user_id,
        eligible=False,
        key="capability-app-ready-001",
    )
    assert app_ready.gate.developer_app_ready is True
    assert app_ready.gate.oba_gate_open is False
    assert app_ready.gate.external_capability_gate_open is False
    assert "oba_eligibility_unknown" in app_ready.gate.capability_denial_reasons

    verified = await _configure_capability(
        service,
        workspace_id=auth_context.workspace_id,
        connection=connection,
        actor_user_id=auth_context.user_id,
        eligible=True,
        expected_version=1,
        key="capability-oba-ready-002",
    )
    gate = await service.capability_gate(auth_context.workspace_id, connection.id)

    assert verified.gate.external_capability_gate_open is True
    assert gate.developer_app_ready is True
    assert gate.oba_gate_open is True
    assert gate.product_mode_supported is True
    assert gate.external_capability_gate_open is True
    assert gate.capability_denial_reasons == []
    assert gate.runtime_external_actions_enabled is False
    assert gate.external_execution_gate_open is False
    assert gate.external_dispatch_implemented is False
    assert "runtime_external_actions_disabled" in gate.execution_denial_reasons
    assert "external_dispatch_not_implemented" in gate.execution_denial_reasons
    assert gate.required_webhook_fields == list(META_GROUP_REQUIRED_WEBHOOK_FIELDS)
    assert gate.limits.invite_only is True
    assert gate.limits.max_participants == 8
    assert gate.limits.max_groups_per_business_number == 10_000


@pytest.mark.asyncio
async def test_coexistence_is_an_explicit_capability_denial(
    db_session,
    auth_context,
) -> None:
    connection = await _connection(
        db_session,
        auth_context.workspace_id,
        key="coexistence",
        transport_mode="coexistence",
    )
    service = MetaGroupService(db_session)
    configured = await _configure_capability(
        service,
        workspace_id=auth_context.workspace_id,
        connection=connection,
        actor_user_id=auth_context.user_id,
        eligible=True,
        key="capability-coexistence-001",
    )

    gate = configured.gate
    assert gate.developer_app_ready is True
    assert gate.oba_gate_open is True
    assert gate.product_mode == "coexistence"
    assert gate.product_mode_supported is False
    assert gate.coexistence_denied is True
    assert gate.external_capability_gate_open is False
    assert "coexistence_unsupported" in gate.capability_denial_reasons


@pytest.mark.asyncio
async def test_official_creation_is_prepared_without_dispatch_then_projected_by_webhook(
    db_session,
    auth_context,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        meta_groups_service_module,
        "external_delivery_allowed",
        lambda: False,
    )
    create_group = AsyncMock(side_effect=AssertionError("live Meta call is forbidden"))
    send_group = AsyncMock(side_effect=AssertionError("live Meta call is forbidden"))
    monkeypatch.setattr(MetaCloudClient, "create_group", create_group)
    monkeypatch.setattr(MetaCloudClient, "send_group_message_payload", send_group)

    connection = await _connection(db_session, auth_context.workspace_id)
    service = MetaGroupService(db_session)
    await _configure_capability(
        service,
        workspace_id=auth_context.workspace_id,
        connection=connection,
        actor_user_id=auth_context.user_id,
        eligible=True,
        key="capability-official-001",
    )
    local = await service.create_local_group(
        workspace_id=auth_context.workspace_id,
        connection_id=connection.id,
        actor_user_id=auth_context.user_id,
        idempotency_key="local-group-separate-001",
        payload=MetaGroupCreate(subject="Equipe interna"),
    )

    request = MetaGroupOfficialCreationPrepare(
        subject="Plantão clientes",
        description="Grupo oficial por convite.",
        join_approval_mode="approval_required",
    )
    prepared = await service.prepare_official_group_creation(
        workspace_id=auth_context.workspace_id,
        connection_id=connection.id,
        actor_user_id=auth_context.user_id,
        idempotency_key="official-create-prepare-001",
        payload=request,
    )
    replay = await service.prepare_official_group_creation(
        workspace_id=auth_context.workspace_id,
        connection_id=connection.id,
        actor_user_id=auth_context.user_id,
        idempotency_key="official-create-prepare-001",
        payload=request,
    )

    assert prepared.provider_request_payload_ready is True
    assert prepared.provider_request_path == f"/{connection.external_account_id}/groups"
    assert prepared.provider_request_payload == {
        "messaging_product": "whatsapp",
        "subject": "Plantão clientes",
        "description": "Grupo oficial por convite.",
        "join_approval_mode": "approval_required",
    }
    assert prepared.group_projection_created is False
    assert prepared.operation.group_id is None
    assert prepared.external_dispatch_executed is False
    assert prepared.gate.external_capability_gate_open is True
    assert prepared.gate.external_execution_gate_open is False
    assert replay.deduplicated is True
    assert replay.operation.id == prepared.operation.id
    create_group.assert_not_awaited()

    lifecycle = MetaGroupLifecycleIngest(
        webhook_field="group_lifecycle_update",
        external_event_id="meta-group-created-event-001",
        provider_group_id="GROUP_ID_OFFICIAL_001",
        prepared_operation_id=prepared.operation.id,
        event_type="group_created",
        provider_request_id="meta-request-001",
        status="active",
        subject="Plantão clientes",
        description="Grupo oficial por convite.",
        join_approval_mode="approval_required",
        occurred_at=datetime.now(UTC),
        ingress_evidence_source="verified_provider_adapter",
        ingress_evidence_reference="signed-envelope-fixture-001",
    )
    projected = await service.ingest_lifecycle_event(
        workspace_id=auth_context.workspace_id,
        connection_id=connection.id,
        actor_user_id=auth_context.user_id,
        payload=lifecycle,
    )
    projected_replay = await service.ingest_lifecycle_event(
        workspace_id=auth_context.workspace_id,
        connection_id=connection.id,
        actor_user_id=auth_context.user_id,
        payload=lifecycle,
    )

    assert projected.group.group_kind == "meta_cloud_group"
    assert projected.group.is_official_meta_group is True
    assert projected.group.is_local_internal is False
    assert projected.group.provider_group_id == "GROUP_ID_OFFICIAL_001"
    assert projected.event.operation_id == prepared.operation.id
    assert projected_replay.deduplicated is True
    assert projected_replay.group.id == projected.group.id
    assert projected.group.id != local.group.id

    local_after = await service.get_group(auth_context.workspace_id, local.group.id)
    assert local_after.group_kind == "local_internal"
    assert local_after.provider_group_id is None
    linked_operation = await db_session.get(MetaGroupOperation, prepared.operation.id)
    assert linked_operation is not None
    assert linked_operation.group_id == projected.group.id

    message = await service.prepare_group_message(
        workspace_id=auth_context.workspace_id,
        group_id=projected.group.id,
        actor_user_id=auth_context.user_id,
        idempotency_key="official-group-message-001",
        payload=MetaGroupMessagePrepare(
            message={"type": "text", "text": {"body": "Aviso oficial"}},
            require_external_capability=True,
        ),
    )
    assert message.provider_payload_ready is True
    assert message.provider_payload == {
        "messaging_product": "whatsapp",
        "recipient_type": "group",
        "to": "GROUP_ID_OFFICIAL_001",
        "type": "text",
        "text": {"preview_url": False, "body": "Aviso oficial"},
    }
    assert message.external_dispatch_executed is False
    assert "external_dispatch_not_implemented" in message.blocked_reasons
    send_group.assert_not_awaited()
    assert await db_session.scalar(select(func.count()).select_from(MetaGroup)) == 2

    provider_event = await db_session.scalar(
        select(MetaGroupLifecycleEvent).where(
            MetaGroupLifecycleEvent.external_event_id == "meta-group-created-event-001"
        )
    )
    assert provider_event is not None
    provider_event.event_type = "tampered"
    with pytest.raises(ImmutableRecordError):
        await db_session.flush()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_group_participant_projection_enforces_eight_including_business(
    db_session,
    auth_context,
) -> None:
    connection = await _connection(db_session, auth_context.workspace_id)
    service = MetaGroupService(db_session)
    created = await service.ingest_lifecycle_event(
        workspace_id=auth_context.workspace_id,
        connection_id=connection.id,
        actor_user_id=auth_context.user_id,
        payload=MetaGroupLifecycleIngest(
            webhook_field="group_lifecycle_update",
            external_event_id="group-create-for-participants",
            provider_group_id="GROUP_ID_PARTICIPANTS",
            event_type="group_created",
            status="active",
            subject="Grupo limitado",
            occurred_at=datetime.now(UTC),
            ingress_evidence_source="verified_provider_adapter",
            ingress_evidence_reference="signed-envelope-participants-create",
        ),
    )
    participants = [{"user_id": f"bsuid-{index}", "role": "member"} for index in range(7)]
    joined = await service.ingest_lifecycle_event(
        workspace_id=auth_context.workspace_id,
        connection_id=connection.id,
        actor_user_id=auth_context.user_id,
        payload=MetaGroupLifecycleIngest(
            webhook_field="group_participants_update",
            external_event_id="group-seven-participants-joined",
            provider_group_id="GROUP_ID_PARTICIPANTS",
            event_type="participants_joined",
            participants=participants,
            occurred_at=datetime.now(UTC),
            ingress_evidence_source="verified_provider_adapter",
            ingress_evidence_reference="signed-envelope-participants-seven",
        ),
    )
    assert joined.group.id == created.group.id
    assert joined.group.participant_count == 8
    assert await db_session.scalar(select(func.count()).select_from(MetaGroupParticipant)) == 7

    with pytest.raises(MetaGroupValidationError, match="maximum of 8"):
        await service.ingest_lifecycle_event(
            workspace_id=auth_context.workspace_id,
            connection_id=connection.id,
            actor_user_id=auth_context.user_id,
            payload=MetaGroupLifecycleIngest(
                webhook_field="group_participants_update",
                external_event_id="group-eighth-external-participant",
                provider_group_id="GROUP_ID_PARTICIPANTS",
                event_type="participant_joined",
                participants=[{"user_id": "bsuid-over-limit", "role": "member"}],
                occurred_at=datetime.now(UTC),
                ingress_evidence_source="verified_provider_adapter",
                ingress_evidence_reference="signed-envelope-participants-over-limit",
            ),
        )
    await db_session.rollback()


@pytest.mark.asyncio
async def test_local_group_gets_creator_participant_and_message_preview(
    db_session,
    auth_context,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        meta_groups_service_module,
        "external_delivery_allowed",
        lambda: False,
    )
    connection = await _connection(db_session, auth_context.workspace_id, key="local-thread")
    service = MetaGroupService(db_session)

    created = await service.create_local_group(
        workspace_id=auth_context.workspace_id,
        connection_id=connection.id,
        actor_user_id=auth_context.user_id,
        idempotency_key="local-group-thread-001",
        payload=MetaGroupCreate(subject="Time interno"),
    )
    assert created.group.participant_count == 1

    participants = await service.list_participants(
        workspace_id=auth_context.workspace_id,
        group_id=created.group.id,
    )
    assert participants.total == 1
    assert participants.items[0].role == "admin"
    assert participants.items[0].state == "joined"

    with pytest.raises(MetaGroupValidationError, match="At least one participant"):
        await service.remove_participant(
            workspace_id=auth_context.workspace_id,
            group_id=created.group.id,
            participant_id=participants.items[0].id,
            actor_user_id=auth_context.user_id,
            idempotency_key="remove-last-participant-001",
        )

    message_op = await service.prepare_group_message(
        workspace_id=auth_context.workspace_id,
        group_id=created.group.id,
        actor_user_id=auth_context.user_id,
        idempotency_key="local-group-message-001",
        payload=MetaGroupMessagePrepare(message={"type": "text", "text": {"body": "Bom dia, equipe"}}),
    )
    assert message_op.provider_payload_ready is False

    refreshed = await service.get_group(auth_context.workspace_id, created.group.id)
    assert refreshed.last_message_preview == "Bom dia, equipe"
    assert refreshed.last_message_at is not None

    thread = await service.list_group_messages(
        workspace_id=auth_context.workspace_id,
        group_id=created.group.id,
    )
    assert thread.total == 1
    assert thread.items[0].text == "Bom dia, equipe"
    assert thread.items[0].external_dispatch_executed is False


@pytest.mark.asyncio
async def test_invite_link_rotation_is_local_simulated_and_gated_for_official(
    db_session,
    auth_context,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        meta_groups_service_module,
        "external_delivery_allowed",
        lambda: False,
    )
    connection = await _connection(db_session, auth_context.workspace_id, key="invite-link")
    service = MetaGroupService(db_session)

    created = await service.create_local_group(
        workspace_id=auth_context.workspace_id,
        connection_id=connection.id,
        actor_user_id=auth_context.user_id,
        idempotency_key="local-group-invite-001",
        payload=MetaGroupCreate(subject="Grupo com convite"),
    )

    rotated = await service.rotate_invite_link(
        workspace_id=auth_context.workspace_id,
        group_id=created.group.id,
        actor_user_id=auth_context.user_id,
        idempotency_key="rotate-invite-001",
    )
    assert rotated.rotated is True
    assert rotated.simulated is True
    assert rotated.group.invite_link is not None
    assert rotated.group.invite_link_simulated is True

    replay = await service.rotate_invite_link(
        workspace_id=auth_context.workspace_id,
        group_id=created.group.id,
        actor_user_id=auth_context.user_id,
        idempotency_key="rotate-invite-001",
    )
    assert replay.deduplicated is True
    assert replay.group.invite_link == rotated.group.invite_link

    official = await service.ingest_lifecycle_event(
        workspace_id=auth_context.workspace_id,
        connection_id=connection.id,
        actor_user_id=auth_context.user_id,
        payload=MetaGroupLifecycleIngest(
            webhook_field="group_lifecycle_update",
            external_event_id="official-invite-group-created",
            provider_group_id="GROUP_ID_INVITE_OFFICIAL",
            event_type="group_created",
            subject="Grupo oficial com convite",
            occurred_at=datetime.now(UTC),
            ingress_evidence_source="verified_provider_adapter",
            ingress_evidence_reference="signed-envelope-invite-official",
        ),
    )
    official_rotation = await service.rotate_invite_link(
        workspace_id=auth_context.workspace_id,
        group_id=official.group.id,
        actor_user_id=auth_context.user_id,
        idempotency_key="rotate-invite-official-001",
    )
    assert official_rotation.rotated is False
    assert official_rotation.simulated is False
    assert "external_dispatch_not_implemented" in official_rotation.blocked_reasons


@pytest.mark.asyncio
async def test_pinned_message_set_and_clear_is_local_and_idempotent(
    db_session,
    auth_context,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        meta_groups_service_module,
        "external_delivery_allowed",
        lambda: False,
    )
    connection = await _connection(db_session, auth_context.workspace_id, key="pin-message")
    service = MetaGroupService(db_session)

    created = await service.create_local_group(
        workspace_id=auth_context.workspace_id,
        connection_id=connection.id,
        actor_user_id=auth_context.user_id,
        idempotency_key="local-group-pin-001",
        payload=MetaGroupCreate(subject="Grupo com fixação"),
    )

    from app.schemas.meta_groups import MetaGroupPinnedMessageSet

    pinned = await service.set_pinned_message(
        workspace_id=auth_context.workspace_id,
        group_id=created.group.id,
        actor_user_id=auth_context.user_id,
        idempotency_key="pin-001",
        payload=MetaGroupPinnedMessageSet(text="Leiam o comunicado do caso"),
    )
    assert pinned.pinned_message is not None
    assert pinned.pinned_message.text == "Leiam o comunicado do caso"

    replay = await service.set_pinned_message(
        workspace_id=auth_context.workspace_id,
        group_id=created.group.id,
        actor_user_id=auth_context.user_id,
        idempotency_key="pin-001",
        payload=MetaGroupPinnedMessageSet(text="Leiam o comunicado do caso"),
    )
    assert replay.pinned_message is not None

    unpinned = await service.clear_pinned_message(
        workspace_id=auth_context.workspace_id,
        group_id=created.group.id,
        actor_user_id=auth_context.user_id,
        idempotency_key="unpin-001",
    )
    assert unpinned.pinned_message is None
