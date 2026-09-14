"""Tenant-safe, local-only Meta Cloud API Groups domain."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.klm_runtime import external_delivery_allowed
from app.models.omnichannel_v6 import (
    MetaGroup,
    MetaGroupCapability,
    MetaGroupLifecycleEvent,
    MetaGroupOperation,
    MetaGroupParticipant,
    OmnichannelChannelConnection,
)
from app.schemas.meta_groups import (
    MetaGroupCapabilityConfigure,
    MetaGroupCapabilityGateRead,
    MetaGroupCapabilityRead,
    MetaGroupCollectionRead,
    MetaGroupCreate,
    MetaGroupCreateRead,
    MetaGroupInviteLinkRotateRead,
    MetaGroupLifecycleEventRead,
    MetaGroupLifecycleIngest,
    MetaGroupLifecycleIngestRead,
    MetaGroupMessageCollectionRead,
    MetaGroupMessagePreparationRead,
    MetaGroupMessagePrepare,
    MetaGroupMessageRead,
    MetaGroupOfficialCreationPreparationRead,
    MetaGroupOfficialCreationPrepare,
    MetaGroupOfficialLimitsRead,
    MetaGroupOperationRead,
    MetaGroupParticipantCollectionRead,
    MetaGroupParticipantRead,
    MetaGroupParticipantRemoveRead,
    MetaGroupPinnedMessageRead,
    MetaGroupPinnedMessageSet,
    MetaGroupProductMode,
    MetaGroupRead,
)
from app.services.whatsapp.errors import WhatsAppProviderError
from app.services.whatsapp.meta_cloud_client import MetaCloudClient

META_GROUP_REQUIRED_WEBHOOK_FIELDS = (
    "group_lifecycle_update",
    "group_participants_update",
    "group_settings_update",
    "group_status_update",
)
META_GROUP_MAX_PARTICIPANTS = 8
META_GROUP_MAX_GROUPS_PER_BUSINESS_NUMBER = 10_000


class MetaGroupDomainError(ValueError):
    """Base error for the local Meta Groups domain."""


class MetaGroupNotFoundError(MetaGroupDomainError):
    pass


class MetaGroupConflictError(MetaGroupDomainError):
    pass


class MetaGroupValidationError(MetaGroupDomainError):
    pass


def _now() -> datetime:
    return datetime.now(UTC)


def _payload_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _deduplicate(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


class MetaGroupService:
    """Prepare and audit Groups API work without ever dispatching to Meta."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def _connection(
        self,
        workspace_id: UUID,
        connection_id: UUID,
    ) -> OmnichannelChannelConnection:
        connection = await self.db.scalar(
            select(OmnichannelChannelConnection).where(
                OmnichannelChannelConnection.workspace_id == workspace_id,
                OmnichannelChannelConnection.id == connection_id,
            )
        )
        if connection is None:
            raise MetaGroupNotFoundError("Omnichannel connection was not found in this workspace.")
        if connection.channel != "whatsapp":
            raise MetaGroupValidationError("Meta Groups requires a WhatsApp connection.")
        return connection

    async def _capability(
        self,
        workspace_id: UUID,
        connection_id: UUID,
    ) -> MetaGroupCapability | None:
        return await self.db.scalar(
            select(MetaGroupCapability).where(
                MetaGroupCapability.workspace_id == workspace_id,
                MetaGroupCapability.connection_id == connection_id,
            )
        )

    @staticmethod
    def _product_mode(connection: OmnichannelChannelConnection) -> MetaGroupProductMode:
        raw_mode = str(connection.transport_mode or "").strip().casefold().replace("-", "_")
        if raw_mode in {"official_api", "cloud_api"}:
            return raw_mode  # type: ignore[return-value]
        if raw_mode == "coexistence":
            return "coexistence"
        if raw_mode in {
            "multi_solution",
            "multi_solution_conversations",
            "multisolution",
        }:
            return "multi_solution"
        if raw_mode in {"business_app", "whatsapp_business_app", "qr_gateway"}:
            return "business_app"
        if connection.provider != "meta_cloud" and raw_mode.startswith("qr"):
            return "business_app"
        return "unsupported"

    @classmethod
    def evaluate_capability(
        cls,
        *,
        workspace_id: UUID,
        connection: OmnichannelChannelConnection,
        capability: MetaGroupCapability | None,
    ) -> MetaGroupCapabilityGateRead:
        """Return a typed gate decision suitable for other backend callers."""
        subscribed = set(capability.subscribed_webhook_fields or ()) if capability else set()
        missing_requirements: list[str] = []
        if capability is None:
            missing_requirements.append("capability_evidence_not_configured")
        else:
            if not capability.developer_app_configured:
                missing_requirements.append("developer_app_not_configured")
            if not capability.webhook_callback_verified:
                missing_requirements.append("webhook_callback_not_verified")
            missing_requirements.extend(
                f"webhook_subscription_missing:{field}"
                for field in META_GROUP_REQUIRED_WEBHOOK_FIELDS
                if field not in subscribed
            )
        developer_app_ready = not missing_requirements

        oba_state = capability.oba_eligibility_state if capability else "unknown"
        oba_source = capability.oba_evidence_source if capability else "unverified"
        oba_evidence_complete = bool(
            capability
            and capability.oba_evidence_reference
            and capability.oba_evidence_recorded_at
            and oba_source in {"meta_api", "meta_business_manager"}
        )
        oba_gate_open = bool(oba_state == "eligible" and oba_evidence_complete)
        mode = cls._product_mode(connection)
        product_mode_supported = bool(
            connection.provider == "meta_cloud" and mode in {"official_api", "cloud_api"}
        )
        runtime_gate = external_delivery_allowed()
        external_capability_gate = bool(
            developer_app_ready and oba_gate_open and product_mode_supported
        )

        capability_denial_reasons = list(missing_requirements)
        if oba_state == "unknown":
            capability_denial_reasons.append("oba_eligibility_unknown")
        elif oba_state == "ineligible":
            capability_denial_reasons.append("oba_ineligible")
        elif not oba_evidence_complete:
            capability_denial_reasons.append("oba_external_evidence_missing")
        if connection.provider != "meta_cloud":
            capability_denial_reasons.append("provider_not_meta_cloud")
        if mode == "coexistence":
            capability_denial_reasons.append("coexistence_unsupported")
        elif mode == "business_app":
            capability_denial_reasons.append("business_app_unsupported")
        elif mode == "multi_solution":
            capability_denial_reasons.append("multi_solution_unsupported")
        elif mode == "unsupported":
            capability_denial_reasons.append("transport_mode_unsupported")
        capability_denial_reasons = _deduplicate(capability_denial_reasons)
        execution_denial_reasons = list(capability_denial_reasons)
        if not runtime_gate:
            execution_denial_reasons.append("runtime_external_actions_disabled")
        execution_denial_reasons.append("external_dispatch_not_implemented")

        return MetaGroupCapabilityGateRead(
            workspace_id=workspace_id,
            connection_id=connection.id,
            provider=connection.provider,
            product_mode=mode,
            developer_app_state="ready" if developer_app_ready else "not_ready",
            developer_app_ready=developer_app_ready,
            developer_app_missing_requirements=missing_requirements,
            required_webhook_fields=list(META_GROUP_REQUIRED_WEBHOOK_FIELDS),
            subscribed_webhook_fields=sorted(subscribed),
            oba_eligibility_state=oba_state,
            oba_evidence_source=oba_source,
            oba_gate_open=oba_gate_open,
            product_mode_supported=product_mode_supported,
            coexistence_denied=mode == "coexistence",
            business_app_denied=mode == "business_app",
            multi_solution_denied=mode == "multi_solution",
            runtime_external_actions_enabled=runtime_gate,
            external_capability_gate_open=external_capability_gate,
            external_execution_gate_open=False,
            external_dispatch_implemented=False,
            local_preparation_available=True,
            capability_denial_reasons=capability_denial_reasons,
            execution_denial_reasons=_deduplicate(execution_denial_reasons),
            limits=MetaGroupOfficialLimitsRead(),
            observed_at=_now(),
        )

    async def capability_gate(
        self,
        workspace_id: UUID,
        connection_id: UUID,
    ) -> MetaGroupCapabilityGateRead:
        """Public service-level gate for workers, adapters, and API callers."""
        connection = await self._connection(workspace_id, connection_id)
        capability = await self._capability(workspace_id, connection_id)
        return self.evaluate_capability(
            workspace_id=workspace_id,
            connection=connection,
            capability=capability,
        )

    def _capability_read(
        self,
        *,
        workspace_id: UUID,
        connection: OmnichannelChannelConnection,
        capability: MetaGroupCapability | None,
        deduplicated: bool = False,
    ) -> MetaGroupCapabilityRead:
        gate = self.evaluate_capability(
            workspace_id=workspace_id,
            connection=connection,
            capability=capability,
        )
        return MetaGroupCapabilityRead(
            id=capability.id if capability else None,
            workspace_id=workspace_id,
            connection_id=connection.id,
            configured=capability is not None,
            phone_number_id=capability.phone_number_id if capability else None,
            waba_id=capability.waba_id if capability else None,
            developer_app_configured=(capability.developer_app_configured if capability else False),
            webhook_callback_verified=(
                capability.webhook_callback_verified if capability else False
            ),
            oba_evidence_reference=(capability.oba_evidence_reference if capability else None),
            oba_evidence_recorded_at=(capability.oba_evidence_recorded_at if capability else None),
            evidence_metadata=dict(capability.evidence_metadata or {}) if capability else {},
            version=capability.version if capability else 0,
            created_at=capability.created_at if capability else None,
            updated_at=capability.updated_at if capability else None,
            gate=gate,
            deduplicated=deduplicated,
        )

    async def get_capability(
        self,
        workspace_id: UUID,
        connection_id: UUID,
    ) -> MetaGroupCapabilityRead:
        connection = await self._connection(workspace_id, connection_id)
        capability = await self._capability(workspace_id, connection_id)
        return self._capability_read(
            workspace_id=workspace_id,
            connection=connection,
            capability=capability,
        )

    async def configure_capability(
        self,
        *,
        workspace_id: UUID,
        connection_id: UUID,
        actor_user_id: UUID | None,
        idempotency_key: str,
        payload: MetaGroupCapabilityConfigure,
    ) -> MetaGroupCapabilityRead:
        connection = await self._connection(workspace_id, connection_id)
        request_payload = payload.model_dump(mode="json", by_alias=False)
        request_hash = _payload_sha256(request_payload)
        event_key = f"meta-group-capability:{idempotency_key}"
        existing_event = await self.db.scalar(
            select(MetaGroupLifecycleEvent).where(
                MetaGroupLifecycleEvent.workspace_id == workspace_id,
                MetaGroupLifecycleEvent.idempotency_key == event_key,
            )
        )
        if existing_event is not None:
            if existing_event.payload_sha256 != request_hash:
                raise MetaGroupConflictError(
                    "The capability idempotency key was reused with different evidence."
                )
            capability = await self._capability(workspace_id, connection_id)
            if capability is None:
                raise MetaGroupConflictError("Capability evidence replay has no projection.")
            return self._capability_read(
                workspace_id=workspace_id,
                connection=connection,
                capability=capability,
                deduplicated=True,
            )

        configured_phone = str(
            (connection.configuration or {}).get("phoneNumberId")
            or connection.external_account_id
            or ""
        ).strip()
        if configured_phone and configured_phone != payload.phone_number_id:
            raise MetaGroupValidationError(
                "Capability evidence does not match the connection phone number ID."
            )

        capability = await self._capability(workspace_id, connection_id)
        current_version = capability.version if capability else 0
        if payload.expected_version != current_version:
            raise MetaGroupConflictError(
                f"Capability version conflict; current version is {current_version}."
            )
        if capability is None:
            capability = MetaGroupCapability(
                workspace_id=workspace_id,
                connection_id=connection.id,
                phone_number_id=payload.phone_number_id,
            )
            self.db.add(capability)

        subscribed = sorted(set(payload.subscribed_webhook_fields))
        developer_ready = bool(
            payload.developer_app_configured
            and payload.webhook_callback_verified
            and set(META_GROUP_REQUIRED_WEBHOOK_FIELDS).issubset(subscribed)
        )
        capability.phone_number_id = payload.phone_number_id
        capability.waba_id = payload.waba_id
        capability.developer_app_configured = payload.developer_app_configured
        capability.webhook_callback_verified = payload.webhook_callback_verified
        capability.developer_app_state = "ready" if developer_ready else "not_ready"
        capability.required_webhook_fields = list(META_GROUP_REQUIRED_WEBHOOK_FIELDS)
        capability.subscribed_webhook_fields = subscribed
        capability.oba_eligibility_state = payload.oba_eligibility_state
        capability.oba_evidence_source = payload.oba_evidence_source
        capability.oba_evidence_reference = payload.oba_evidence_reference
        capability.oba_evidence_recorded_at = payload.oba_evidence_recorded_at
        capability.product_mode = self._product_mode(connection)
        capability.max_participants = META_GROUP_MAX_PARTICIPANTS
        capability.max_groups_per_business_number = META_GROUP_MAX_GROUPS_PER_BUSINESS_NUMBER
        capability.invite_only = True
        capability.evidence_metadata = dict(payload.evidence_metadata)
        capability.version = current_version + 1
        await self.db.flush()

        gate = self.evaluate_capability(
            workspace_id=workspace_id,
            connection=connection,
            capability=capability,
        )
        self.db.add(
            MetaGroupLifecycleEvent(
                workspace_id=workspace_id,
                group_id=None,
                operation_id=None,
                connection_id=connection.id,
                actor_user_id=actor_user_id,
                source="local_control",
                webhook_field=None,
                event_type="capability_evidence_configured",
                external_event_id=None,
                idempotency_key=event_key,
                payload_sha256=request_hash,
                event_payload=request_payload,
                state_snapshot=gate.model_dump(mode="json", by_alias=False),
                occurred_at=_now(),
            )
        )
        await self.db.commit()
        await self.db.refresh(capability)
        return self._capability_read(
            workspace_id=workspace_id,
            connection=connection,
            capability=capability,
        )

    async def _group(
        self,
        workspace_id: UUID,
        group_id: UUID,
    ) -> MetaGroup:
        group = await self.db.scalar(
            select(MetaGroup).where(
                MetaGroup.workspace_id == workspace_id,
                MetaGroup.id == group_id,
            )
        )
        if group is None:
            raise MetaGroupNotFoundError("Meta group was not found in this workspace.")
        return group

    @staticmethod
    def _extract_message_text(message: dict[str, Any] | None) -> str | None:
        if not isinstance(message, dict):
            return None
        text_field = message.get("text")
        if isinstance(text_field, dict):
            body = text_field.get("body")
            if isinstance(body, str) and body.strip():
                return body.strip()
        if isinstance(text_field, str) and text_field.strip():
            return text_field.strip()
        image_field = message.get("image")
        if isinstance(image_field, dict):
            caption = image_field.get("caption")
            if isinstance(caption, str) and caption.strip():
                return caption.strip()
        template_field = message.get("template")
        if isinstance(template_field, dict):
            name = template_field.get("name")
            if isinstance(name, str) and name.strip():
                return f"Template: {name.strip()}"
        message_type = message.get("type")
        if isinstance(message_type, str) and message_type.strip():
            return f"Mensagem ({message_type.strip()})"
        return None

    async def _last_prepared_message_operation(
        self,
        *,
        workspace_id: UUID,
        group_id: UUID,
    ) -> MetaGroupOperation | None:
        return await self.db.scalar(
            select(MetaGroupOperation)
            .where(
                MetaGroupOperation.workspace_id == workspace_id,
                MetaGroupOperation.group_id == group_id,
                MetaGroupOperation.operation_type == "prepare_group_message",
            )
            .order_by(MetaGroupOperation.created_at.desc(), MetaGroupOperation.id.desc())
            .limit(1)
        )

    async def _group_read(
        self,
        *,
        workspace_id: UUID,
        group: MetaGroup,
    ) -> MetaGroupRead:
        connection = await self._connection(workspace_id, group.connection_id)
        capability = await self._capability(workspace_id, group.connection_id)
        gate = self.evaluate_capability(
            workspace_id=workspace_id,
            connection=connection,
            capability=capability,
        )
        metadata = dict(group.group_metadata or {})
        pinned_message = metadata.get("pinnedMessage")
        last_operation = await self._last_prepared_message_operation(
            workspace_id=workspace_id, group_id=group.id
        )
        last_message_preview: str | None = None
        last_message_at: datetime | None = None
        if last_operation is not None:
            request_payload = last_operation.request_payload or {}
            last_message_preview = self._extract_message_text(request_payload.get("message"))
            last_message_at = last_operation.created_at
        return MetaGroupRead.model_validate(
            {
                **group.__dict__,
                "is_local_internal": group.group_kind == "local_internal",
                "is_official_meta_group": group.group_kind == "meta_cloud_group",
                "invite_link_simulated": bool(metadata.get("inviteLinkSource") == "local_simulated"),
                "last_message_preview": last_message_preview,
                "last_message_at": last_message_at,
                "pinned_message": pinned_message,
                "gate": gate,
            }
        )

    @staticmethod
    def _operation_read(operation: MetaGroupOperation) -> MetaGroupOperationRead:
        return MetaGroupOperationRead.model_validate(operation)

    async def create_local_group(
        self,
        *,
        workspace_id: UUID,
        connection_id: UUID,
        actor_user_id: UUID | None,
        idempotency_key: str,
        payload: MetaGroupCreate,
    ) -> MetaGroupCreateRead:
        connection = await self._connection(workspace_id, connection_id)
        capability = await self._capability(workspace_id, connection_id)
        request_payload = {
            "connection_id": str(connection_id),
            **payload.model_dump(mode="json", by_alias=False),
        }
        request_hash = _payload_sha256(request_payload)
        existing = await self.db.scalar(
            select(MetaGroup).where(
                MetaGroup.workspace_id == workspace_id,
                MetaGroup.create_idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            if existing.request_payload_sha256 != request_hash:
                raise MetaGroupConflictError(
                    "The group idempotency key was reused with a different request."
                )
            operation = await self.db.scalar(
                select(MetaGroupOperation).where(
                    MetaGroupOperation.workspace_id == workspace_id,
                    MetaGroupOperation.idempotency_key == idempotency_key,
                    MetaGroupOperation.operation_type == "create_local_group",
                )
            )
            if operation is None:
                raise MetaGroupConflictError("Group replay has no audited operation.")
            return MetaGroupCreateRead(
                group=await self._group_read(workspace_id=workspace_id, group=existing),
                operation=self._operation_read(operation),
                deduplicated=True,
            )

        gate = self.evaluate_capability(
            workspace_id=workspace_id,
            connection=connection,
            capability=capability,
        )
        group = MetaGroup(
            workspace_id=workspace_id,
            connection_id=connection_id,
            capability_id=capability.id if capability else None,
            created_by_user_id=actor_user_id,
            group_kind="local_internal",
            provider_group_id=None,
            provider_request_id=None,
            subject=payload.subject,
            description=payload.description,
            join_approval_mode=payload.join_approval_mode,
            state="local_ready",
            participant_count=1,
            create_idempotency_key=idempotency_key,
            request_payload_sha256=request_hash,
            group_metadata={"inviteOnly": True, "externalDispatchExecuted": False},
        )
        self.db.add(group)
        await self.db.flush()
        self.db.add(
            MetaGroupParticipant(
                workspace_id=workspace_id,
                group_id=group.id,
                provider_user_id=(
                    str(actor_user_id) if actor_user_id is not None else f"local-creator:{group.id}"
                ),
                role="admin",
                state="joined",
                source="invite_link",
                joined_at=_now(),
                participant_metadata={"joinedAs": "group_creator"},
            )
        )
        operation = MetaGroupOperation(
            workspace_id=workspace_id,
            group_id=group.id,
            connection_id=connection_id,
            requested_by_user_id=actor_user_id,
            operation_type="create_local_group",
            state="local_ready",
            idempotency_key=idempotency_key,
            request_payload_sha256=request_hash,
            request_payload=request_payload,
            capability_snapshot=gate.model_dump(mode="json", by_alias=False),
            external_dispatch_executed=False,
        )
        self.db.add(operation)
        await self.db.flush()
        self.db.add(
            MetaGroupLifecycleEvent(
                workspace_id=workspace_id,
                group_id=group.id,
                operation_id=operation.id,
                connection_id=connection_id,
                actor_user_id=actor_user_id,
                source="local_control",
                webhook_field=None,
                event_type="local_internal_group_created",
                external_event_id=None,
                idempotency_key=f"meta-group-create:{idempotency_key}",
                payload_sha256=request_hash,
                event_payload=request_payload,
                state_snapshot={
                    "groupKind": "local_internal",
                    "state": "local_ready",
                    "externalDispatchExecuted": False,
                },
                occurred_at=_now(),
            )
        )
        await self.db.commit()
        await self.db.refresh(group)
        await self.db.refresh(operation)
        return MetaGroupCreateRead(
            group=await self._group_read(workspace_id=workspace_id, group=group),
            operation=self._operation_read(operation),
            deduplicated=False,
        )

    async def get_group(self, workspace_id: UUID, group_id: UUID) -> MetaGroupRead:
        group = await self._group(workspace_id, group_id)
        return await self._group_read(workspace_id=workspace_id, group=group)

    async def prepare_official_group_creation(
        self,
        *,
        workspace_id: UUID,
        connection_id: UUID,
        actor_user_id: UUID | None,
        idempotency_key: str,
        payload: MetaGroupOfficialCreationPrepare,
    ) -> MetaGroupOfficialCreationPreparationRead:
        """Persist a gated official-create intent without creating a projection or dispatching."""
        connection = await self._connection(workspace_id, connection_id)
        capability = await self._capability(workspace_id, connection_id)
        gate = self.evaluate_capability(
            workspace_id=workspace_id,
            connection=connection,
            capability=capability,
        )
        try:
            provider_request = MetaCloudClient.build_group_creation_payload(
                subject=payload.subject,
                description=payload.description,
                join_approval_mode=payload.join_approval_mode,
            )
        except WhatsAppProviderError as error:  # pragma: no cover - schema mirrors builder
            raise MetaGroupValidationError(error.message) from error

        request_payload = {
            "connection_id": str(connection_id),
            **payload.model_dump(mode="json", by_alias=False),
            "provider_request": provider_request,
        }
        request_hash = _payload_sha256(request_payload)
        existing = await self.db.scalar(
            select(MetaGroupOperation).where(
                MetaGroupOperation.workspace_id == workspace_id,
                MetaGroupOperation.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            if (
                existing.operation_type != "prepare_official_group_creation"
                or existing.connection_id != connection_id
                or existing.request_payload_sha256 != request_hash
            ):
                raise MetaGroupConflictError(
                    "The official-create idempotency key was reused with a different request."
                )
            ready = gate.external_capability_gate_open
            return MetaGroupOfficialCreationPreparationRead(
                operation=self._operation_read(existing),
                gate=gate,
                provider_request_path=(
                    f"/{capability.phone_number_id}/groups"
                    if ready and capability is not None
                    else None
                ),
                provider_request_payload_ready=ready,
                provider_request_payload=provider_request if ready else None,
                blocked_reasons=([] if ready else gate.capability_denial_reasons),
                deduplicated=True,
            )

        projected_count = int(
            await self.db.scalar(
                select(func.count(MetaGroup.id)).where(
                    MetaGroup.workspace_id == workspace_id,
                    MetaGroup.connection_id == connection_id,
                    MetaGroup.group_kind == "meta_cloud_group",
                    MetaGroup.state != "deleted",
                )
            )
            or 0
        )
        pending_count = int(
            await self.db.scalar(
                select(func.count(MetaGroupOperation.id)).where(
                    MetaGroupOperation.workspace_id == workspace_id,
                    MetaGroupOperation.connection_id == connection_id,
                    MetaGroupOperation.operation_type == "prepare_official_group_creation",
                    MetaGroupOperation.group_id.is_(None),
                    MetaGroupOperation.state == "local_ready",
                )
            )
            or 0
        )
        if projected_count + pending_count >= META_GROUP_MAX_GROUPS_PER_BUSINESS_NUMBER:
            raise MetaGroupValidationError(
                "The official limit of 10,000 groups per business number was reached."
            )

        ready = gate.external_capability_gate_open
        operation = MetaGroupOperation(
            workspace_id=workspace_id,
            group_id=None,
            connection_id=connection_id,
            requested_by_user_id=actor_user_id,
            operation_type="prepare_official_group_creation",
            state="local_ready" if ready else "blocked_by_capability",
            idempotency_key=idempotency_key,
            request_payload_sha256=request_hash,
            request_payload=request_payload,
            capability_snapshot=gate.model_dump(mode="json", by_alias=False),
            external_dispatch_executed=False,
        )
        self.db.add(operation)
        await self.db.flush()
        self.db.add(
            MetaGroupLifecycleEvent(
                workspace_id=workspace_id,
                group_id=None,
                operation_id=operation.id,
                connection_id=connection_id,
                actor_user_id=actor_user_id,
                source="local_control",
                webhook_field=None,
                event_type=(
                    "official_group_creation_prepared"
                    if ready
                    else "official_group_creation_blocked"
                ),
                external_event_id=None,
                idempotency_key=f"meta-group-official-create:{idempotency_key}",
                payload_sha256=request_hash,
                event_payload=request_payload,
                state_snapshot={
                    "providerRequestPayloadReady": ready,
                    "groupProjectionCreated": False,
                    "externalDispatchExecuted": False,
                    "blockedReasons": ([] if ready else gate.capability_denial_reasons),
                },
                occurred_at=_now(),
            )
        )
        await self.db.commit()
        await self.db.refresh(operation)
        return MetaGroupOfficialCreationPreparationRead(
            operation=self._operation_read(operation),
            gate=gate,
            provider_request_path=(
                f"/{capability.phone_number_id}/groups"
                if ready and capability is not None
                else None
            ),
            provider_request_payload_ready=ready,
            provider_request_payload=provider_request if ready else None,
            blocked_reasons=[] if ready else gate.capability_denial_reasons,
            deduplicated=False,
        )

    async def list_groups(
        self,
        *,
        workspace_id: UUID,
        connection_id: UUID | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> MetaGroupCollectionRead:
        filters = [MetaGroup.workspace_id == workspace_id]
        if connection_id is not None:
            await self._connection(workspace_id, connection_id)
            filters.append(MetaGroup.connection_id == connection_id)
        total = int(await self.db.scalar(select(func.count(MetaGroup.id)).where(*filters)) or 0)
        groups = list(
            (
                await self.db.scalars(
                    select(MetaGroup)
                    .where(*filters)
                    .order_by(MetaGroup.created_at.desc(), MetaGroup.id.desc())
                    .limit(limit)
                    .offset(offset)
                )
            ).all()
        )
        return MetaGroupCollectionRead(
            items=[
                await self._group_read(workspace_id=workspace_id, group=group) for group in groups
            ],
            total=total,
        )

    async def prepare_group_message(
        self,
        *,
        workspace_id: UUID,
        group_id: UUID,
        actor_user_id: UUID | None,
        idempotency_key: str,
        payload: MetaGroupMessagePrepare,
    ) -> MetaGroupMessagePreparationRead:
        group = await self._group(workspace_id, group_id)
        connection = await self._connection(workspace_id, group.connection_id)
        capability = await self._capability(workspace_id, group.connection_id)
        gate = self.evaluate_capability(
            workspace_id=workspace_id,
            connection=connection,
            capability=capability,
        )

        try:
            validated_payload = MetaCloudClient.build_group_message_payload(
                group_id=group.provider_group_id or "local-group-validation",
                message=payload.message,
            )
        except WhatsAppProviderError as error:
            raise MetaGroupValidationError(error.message) from error

        provider_payload = validated_payload if group.provider_group_id else None
        provider_payload_ready = bool(
            group.group_kind == "meta_cloud_group"
            and group.provider_group_id
            and gate.external_capability_gate_open
        )
        blocked_reasons = list(gate.execution_denial_reasons)
        if group.group_kind == "local_internal" or not group.provider_group_id:
            blocked_reasons.insert(0, "local_internal_group_has_no_meta_group_id")
        blocked_reasons = _deduplicate(blocked_reasons)

        request_payload = {
            "group_id": str(group.id),
            "message": payload.message,
            "require_external_capability": payload.require_external_capability,
            "provider_payload": provider_payload,
        }
        request_hash = _payload_sha256(request_payload)
        existing = await self.db.scalar(
            select(MetaGroupOperation).where(
                MetaGroupOperation.workspace_id == workspace_id,
                MetaGroupOperation.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            if (
                existing.operation_type != "prepare_group_message"
                or existing.group_id != group.id
                or existing.request_payload_sha256 != request_hash
            ):
                raise MetaGroupConflictError(
                    "The message idempotency key was reused with a different request."
                )
            return MetaGroupMessagePreparationRead(
                group=await self._group_read(workspace_id=workspace_id, group=group),
                operation=self._operation_read(existing),
                to_group_id=group.provider_group_id,
                provider_payload_ready=provider_payload_ready,
                provider_payload=provider_payload,
                external_dispatch_executed=False,
                blocked_reasons=blocked_reasons,
                deduplicated=True,
            )

        state = (
            "blocked_by_capability"
            if payload.require_external_capability and not provider_payload_ready
            else "local_ready"
        )
        operation = MetaGroupOperation(
            workspace_id=workspace_id,
            group_id=group.id,
            connection_id=group.connection_id,
            requested_by_user_id=actor_user_id,
            operation_type="prepare_group_message",
            state=state,
            idempotency_key=idempotency_key,
            request_payload_sha256=request_hash,
            request_payload=request_payload,
            capability_snapshot=gate.model_dump(mode="json", by_alias=False),
            external_dispatch_executed=False,
        )
        self.db.add(operation)
        await self.db.flush()
        self.db.add(
            MetaGroupLifecycleEvent(
                workspace_id=workspace_id,
                group_id=group.id,
                operation_id=operation.id,
                connection_id=group.connection_id,
                actor_user_id=actor_user_id,
                source="local_control",
                webhook_field=None,
                event_type="group_message_prepared_locally",
                external_event_id=None,
                idempotency_key=f"meta-group-message:{idempotency_key}",
                payload_sha256=request_hash,
                event_payload=request_payload,
                state_snapshot={
                    "state": state,
                    "providerPayloadReady": provider_payload_ready,
                    "externalDispatchExecuted": False,
                    "blockedReasons": blocked_reasons,
                },
                occurred_at=_now(),
            )
        )
        await self.db.commit()
        await self.db.refresh(operation)
        return MetaGroupMessagePreparationRead(
            group=await self._group_read(workspace_id=workspace_id, group=group),
            operation=self._operation_read(operation),
            to_group_id=group.provider_group_id,
            provider_payload_ready=provider_payload_ready,
            provider_payload=provider_payload,
            external_dispatch_executed=False,
            blocked_reasons=blocked_reasons,
            deduplicated=False,
        )

    @staticmethod
    def _participant_state(event_type: str) -> str:
        normalized = event_type.strip().casefold().replace("-", "_")
        if "join_request" in normalized and "reject" in normalized:
            return "rejected"
        if "join_request" in normalized and any(
            token in normalized for token in ("approve", "accept")
        ):
            return "joined"
        if "join_request" in normalized:
            return "join_requested"
        if any(token in normalized for token in ("remove", "removed")):
            return "removed"
        if any(token in normalized for token in ("leave", "left")):
            return "left"
        if any(token in normalized for token in ("reject", "rejected")):
            return "rejected"
        if any(token in normalized for token in ("join", "joined", "add", "added")):
            return "joined"
        return "invited"

    async def ingest_lifecycle_event(
        self,
        *,
        workspace_id: UUID,
        connection_id: UUID,
        actor_user_id: UUID | None,
        payload: MetaGroupLifecycleIngest,
    ) -> MetaGroupLifecycleIngestRead:
        await self._connection(workspace_id, connection_id)
        event_payload = payload.model_dump(mode="json", by_alias=False)
        event_hash = _payload_sha256(event_payload)
        external_event_digest = hashlib.sha256(
            payload.external_event_id.encode("utf-8")
        ).hexdigest()
        event_key = f"meta-group-webhook:{connection_id}:{external_event_digest}"
        existing_event = await self.db.scalar(
            select(MetaGroupLifecycleEvent).where(
                MetaGroupLifecycleEvent.workspace_id == workspace_id,
                MetaGroupLifecycleEvent.idempotency_key == event_key,
            )
        )
        if existing_event is not None:
            if existing_event.payload_sha256 != event_hash:
                raise MetaGroupConflictError(
                    "The provider event ID was replayed with different normalized content."
                )
            if existing_event.group_id is None:
                raise MetaGroupConflictError("Provider event replay has no group projection.")
            group = await self._group(workspace_id, existing_event.group_id)
            return MetaGroupLifecycleIngestRead(
                event=MetaGroupLifecycleEventRead.model_validate(existing_event),
                group=await self._group_read(workspace_id=workspace_id, group=group),
                deduplicated=True,
            )

        prepared_operation: MetaGroupOperation | None = None
        group: MetaGroup | None = None
        if payload.prepared_operation_id is not None:
            prepared_operation = await self.db.scalar(
                select(MetaGroupOperation).where(
                    MetaGroupOperation.workspace_id == workspace_id,
                    MetaGroupOperation.id == payload.prepared_operation_id,
                )
            )
            if prepared_operation is None:
                raise MetaGroupNotFoundError(
                    "The prepared official-create operation was not found in this workspace."
                )
            if (
                prepared_operation.operation_type != "prepare_official_group_creation"
                or prepared_operation.connection_id != connection_id
            ):
                raise MetaGroupValidationError(
                    "The prepared operation is not an official create for this connection."
                )
            prepared_subject = str(
                (prepared_operation.request_payload or {}).get("subject") or ""
            ).strip()
            if prepared_subject and prepared_subject != str(payload.subject or "").strip():
                raise MetaGroupConflictError(
                    "The lifecycle subject does not match the prepared official create."
                )
            if prepared_operation.group_id is not None:
                group = await self._group(workspace_id, prepared_operation.group_id)
                if (
                    group.group_kind != "meta_cloud_group"
                    or group.provider_group_id != payload.provider_group_id
                ):
                    raise MetaGroupConflictError(
                        "The prepared operation is already linked to another official group."
                    )

        provider_group = await self.db.scalar(
            select(MetaGroup).where(
                MetaGroup.workspace_id == workspace_id,
                MetaGroup.connection_id == connection_id,
                MetaGroup.provider_group_id == payload.provider_group_id,
            )
        )
        if group is not None and provider_group is not None and group.id != provider_group.id:
            raise MetaGroupConflictError(
                "The provider GROUP_ID is already bound to another tenant projection."
            )
        group = group or provider_group
        group_was_created = False
        if group is None:
            is_create_event = bool(
                payload.webhook_field == "group_lifecycle_update"
                and "create" in payload.event_type.casefold()
            )
            if not is_create_event or not (payload.subject or "").strip():
                raise MetaGroupNotFoundError(
                    "Provider lifecycle event cannot be bound to a tenant group."
                )
            group_count = int(
                await self.db.scalar(
                    select(func.count(MetaGroup.id)).where(
                        MetaGroup.workspace_id == workspace_id,
                        MetaGroup.connection_id == connection_id,
                        MetaGroup.group_kind == "meta_cloud_group",
                        MetaGroup.state != "deleted",
                    )
                )
                or 0
            )
            if group_count >= META_GROUP_MAX_GROUPS_PER_BUSINESS_NUMBER:
                raise MetaGroupValidationError(
                    "The official limit of 10,000 groups per business number was reached."
                )
            capability = await self._capability(workspace_id, connection_id)
            provider_key = hashlib.sha256(payload.external_event_id.encode("utf-8")).hexdigest()
            group = MetaGroup(
                workspace_id=workspace_id,
                connection_id=connection_id,
                capability_id=capability.id if capability else None,
                created_by_user_id=(
                    prepared_operation.requested_by_user_id
                    if prepared_operation is not None
                    else actor_user_id
                ),
                group_kind="meta_cloud_group",
                provider_group_id=payload.provider_group_id,
                provider_request_id=payload.provider_request_id,
                subject=(payload.subject or "").strip(),
                description=payload.description,
                join_approval_mode=payload.join_approval_mode or "auto_approve",
                state="active",
                invite_link=payload.invite_link,
                participant_count=1,
                create_idempotency_key=(
                    f"official-preparation:{prepared_operation.id}"
                    if prepared_operation is not None
                    else f"provider-event:{provider_key}"
                ),
                request_payload_sha256=event_hash,
                group_metadata={
                    "discoveredFromVerifiedLifecycle": True,
                    "preparedOperationId": (
                        str(prepared_operation.id) if prepared_operation is not None else None
                    ),
                },
            )
            self.db.add(group)
            await self.db.flush()
            group_was_created = True

        if group.group_kind != "meta_cloud_group":
            raise MetaGroupConflictError(
                "Internal local groups cannot be converted into official Meta groups."
            )
        if prepared_operation is not None:
            prepared_operation.group_id = group.id

        group.provider_group_id = payload.provider_group_id
        group.provider_request_id = payload.provider_request_id or group.provider_request_id
        group.last_lifecycle_at = payload.occurred_at
        if payload.subject is not None:
            group.subject = payload.subject.strip()
        if payload.description is not None:
            group.description = payload.description
        if payload.invite_link is not None:
            group.invite_link = payload.invite_link
        if payload.join_approval_mode is not None:
            group.join_approval_mode = payload.join_approval_mode

        normalized_status = str(payload.status or payload.event_type).casefold()
        if payload.webhook_field == "group_lifecycle_update":
            if "delete" in normalized_status:
                group.state = "deleted"
            elif any(token in normalized_status for token in ("fail", "error")):
                group.state = "failed"
            elif "create" in normalized_status or "active" in normalized_status:
                group.state = "active"
        elif payload.webhook_field == "group_status_update":
            if "delete" in normalized_status:
                group.state = "deleted"
            elif any(token in normalized_status for token in ("fail", "error", "disable")):
                group.state = "failed"
            elif "active" in normalized_status or "enable" in normalized_status:
                group.state = "active"

        if payload.webhook_field == "group_participants_update":
            next_state = self._participant_state(payload.event_type)
            rows = list(
                (
                    await self.db.scalars(
                        select(MetaGroupParticipant).where(
                            MetaGroupParticipant.workspace_id == workspace_id,
                            MetaGroupParticipant.group_id == group.id,
                        )
                    )
                ).all()
            )
            by_identifier = {row.provider_user_id: row for row in rows}
            joined_identifiers = {row.provider_user_id for row in rows if row.state == "joined"}
            incoming_identifiers = {
                participant.user_id or participant.wa_id or participant.username or ""
                for participant in payload.participants
            }
            if next_state == "joined":
                joined_identifiers.update(incoming_identifiers)
            elif next_state in {"left", "removed", "rejected"}:
                joined_identifiers.difference_update(incoming_identifiers)
            if 1 + len(joined_identifiers) > META_GROUP_MAX_PARTICIPANTS:
                raise MetaGroupValidationError(
                    "Participant update exceeds the official maximum of 8 including the business."
                )

            for participant in payload.participants:
                provider_user_id = (
                    participant.user_id or participant.wa_id or participant.username or ""
                )
                row = by_identifier.get(provider_user_id)
                if row is None:
                    row = MetaGroupParticipant(
                        workspace_id=workspace_id,
                        group_id=group.id,
                        provider_user_id=provider_user_id,
                        source=(
                            "join_request" if next_state == "join_requested" else "provider_webhook"
                        ),
                    )
                    self.db.add(row)
                    by_identifier[provider_user_id] = row
                row.wa_id = participant.wa_id
                row.business_scoped_user_id = participant.user_id
                row.username = participant.username
                row.join_request_id = participant.join_request_id
                row.role = participant.role
                row.state = next_state
                row.source = (
                    "join_request" if next_state == "join_requested" else "provider_webhook"
                )
                if next_state == "joined":
                    row.joined_at = payload.occurred_at
                    row.left_at = None
                elif next_state in {"left", "removed"}:
                    row.left_at = payload.occurred_at
                row.participant_metadata = {
                    "lastExternalEventId": payload.external_event_id,
                    "ingressEvidenceSource": payload.ingress_evidence_source,
                }
            group.participant_count = 1 + len(joined_identifiers)

        metadata = dict(group.group_metadata or {})
        metadata["lastProviderStatus"] = payload.status
        metadata["lastWebhookField"] = payload.webhook_field
        group.group_metadata = metadata
        if not group_was_created:
            group.version += 1
        await self.db.flush()
        event = MetaGroupLifecycleEvent(
            workspace_id=workspace_id,
            group_id=group.id,
            operation_id=(prepared_operation.id if prepared_operation is not None else None),
            connection_id=connection_id,
            actor_user_id=actor_user_id,
            source="provider_webhook",
            webhook_field=payload.webhook_field,
            event_type=payload.event_type,
            external_event_id=payload.external_event_id,
            idempotency_key=event_key,
            payload_sha256=event_hash,
            event_payload=event_payload,
            state_snapshot={
                "groupKind": group.group_kind,
                "state": group.state,
                "participantCount": group.participant_count,
                "providerGroupId": group.provider_group_id,
            },
            occurred_at=payload.occurred_at,
        )
        self.db.add(event)
        await self.db.commit()
        await self.db.refresh(group)
        await self.db.refresh(event)
        return MetaGroupLifecycleIngestRead(
            event=MetaGroupLifecycleEventRead.model_validate(event),
            group=await self._group_read(workspace_id=workspace_id, group=group),
            deduplicated=False,
        )

    async def list_lifecycle_events(
        self,
        *,
        workspace_id: UUID,
        group_id: UUID,
        limit: int = 200,
    ) -> list[MetaGroupLifecycleEventRead]:
        await self._group(workspace_id, group_id)
        events = list(
            (
                await self.db.scalars(
                    select(MetaGroupLifecycleEvent)
                    .where(
                        MetaGroupLifecycleEvent.workspace_id == workspace_id,
                        MetaGroupLifecycleEvent.group_id == group_id,
                    )
                    .order_by(
                        MetaGroupLifecycleEvent.occurred_at,
                        MetaGroupLifecycleEvent.id,
                    )
                    .limit(limit)
                )
            ).all()
        )
        return [MetaGroupLifecycleEventRead.model_validate(event) for event in events]

    async def list_group_messages(
        self,
        *,
        workspace_id: UUID,
        group_id: UUID,
        limit: int = 100,
    ) -> MetaGroupMessageCollectionRead:
        await self._group(workspace_id, group_id)
        total = int(
            await self.db.scalar(
                select(func.count(MetaGroupOperation.id)).where(
                    MetaGroupOperation.workspace_id == workspace_id,
                    MetaGroupOperation.group_id == group_id,
                    MetaGroupOperation.operation_type == "prepare_group_message",
                )
            )
            or 0
        )
        operations = list(
            (
                await self.db.scalars(
                    select(MetaGroupOperation)
                    .where(
                        MetaGroupOperation.workspace_id == workspace_id,
                        MetaGroupOperation.group_id == group_id,
                        MetaGroupOperation.operation_type == "prepare_group_message",
                    )
                    .order_by(MetaGroupOperation.created_at.desc(), MetaGroupOperation.id.desc())
                    .limit(limit)
                )
            ).all()
        )
        items = [
            MetaGroupMessageRead(
                id=operation.id,
                group_id=group_id,
                requested_by_user_id=operation.requested_by_user_id,
                text=self._extract_message_text((operation.request_payload or {}).get("message")),
                message=(operation.request_payload or {}).get("message") or {},
                state=operation.state,
                external_dispatch_executed=False,
                created_at=operation.created_at,
            )
            for operation in operations
        ]
        return MetaGroupMessageCollectionRead(items=items, total=total)

    async def rotate_invite_link(
        self,
        *,
        workspace_id: UUID,
        group_id: UUID,
        actor_user_id: UUID | None,
        idempotency_key: str,
    ) -> MetaGroupInviteLinkRotateRead:
        group = await self._group(workspace_id, group_id)
        event_key = f"meta-group-invite-rotate:{idempotency_key}"
        existing_event = await self.db.scalar(
            select(MetaGroupLifecycleEvent).where(
                MetaGroupLifecycleEvent.workspace_id == workspace_id,
                MetaGroupLifecycleEvent.idempotency_key == event_key,
            )
        )
        if existing_event is not None:
            snapshot = existing_event.state_snapshot or {}
            return MetaGroupInviteLinkRotateRead(
                group=await self._group_read(workspace_id=workspace_id, group=group),
                rotated=bool(snapshot.get("rotated", False)),
                simulated=bool(snapshot.get("simulated", False)),
                blocked_reasons=list(snapshot.get("blockedReasons", [])),
                deduplicated=True,
            )

        connection = await self._connection(workspace_id, group.connection_id)
        capability = await self._capability(workspace_id, group.connection_id)
        gate = self.evaluate_capability(
            workspace_id=workspace_id,
            connection=connection,
            capability=capability,
        )

        if group.group_kind == "local_internal":
            new_link = f"https://wa.me/group-invite-local/{uuid4().hex}"
            group.invite_link = new_link
            metadata = dict(group.group_metadata or {})
            metadata["inviteLinkSource"] = "local_simulated"
            metadata["inviteLinkRotatedAt"] = _now().isoformat()
            group.group_metadata = metadata
            group.version += 1
            rotated = True
            simulated = True
            blocked_reasons: list[str] = []
            event_type = "invite_link_rotated_locally"
        else:
            rotated = False
            simulated = False
            blocked_reasons = _deduplicate(
                [
                    *gate.execution_denial_reasons,
                    "official_invite_link_requires_provider_webhook_ingestion",
                ]
            )
            event_type = "invite_link_rotation_prepared"

        await self.db.flush()
        event_payload = {
            "group_id": str(group_id),
            "rotated": rotated,
            "simulated": simulated,
        }
        self.db.add(
            MetaGroupLifecycleEvent(
                workspace_id=workspace_id,
                group_id=group.id,
                operation_id=None,
                connection_id=group.connection_id,
                actor_user_id=actor_user_id,
                source="local_control",
                webhook_field=None,
                event_type=event_type,
                external_event_id=None,
                idempotency_key=event_key,
                payload_sha256=_payload_sha256(event_payload),
                event_payload=event_payload,
                state_snapshot={
                    "rotated": rotated,
                    "simulated": simulated,
                    "blockedReasons": blocked_reasons,
                },
                occurred_at=_now(),
            )
        )
        await self.db.commit()
        await self.db.refresh(group)
        return MetaGroupInviteLinkRotateRead(
            group=await self._group_read(workspace_id=workspace_id, group=group),
            rotated=rotated,
            simulated=simulated,
            blocked_reasons=blocked_reasons,
            deduplicated=False,
        )

    async def list_participants(
        self,
        *,
        workspace_id: UUID,
        group_id: UUID,
    ) -> MetaGroupParticipantCollectionRead:
        await self._group(workspace_id, group_id)
        total = int(
            await self.db.scalar(
                select(func.count(MetaGroupParticipant.id)).where(
                    MetaGroupParticipant.workspace_id == workspace_id,
                    MetaGroupParticipant.group_id == group_id,
                )
            )
            or 0
        )
        rows = list(
            (
                await self.db.scalars(
                    select(MetaGroupParticipant)
                    .where(
                        MetaGroupParticipant.workspace_id == workspace_id,
                        MetaGroupParticipant.group_id == group_id,
                    )
                    .order_by(MetaGroupParticipant.created_at)
                )
            ).all()
        )
        return MetaGroupParticipantCollectionRead(
            items=[MetaGroupParticipantRead.model_validate(row) for row in rows],
            total=total,
        )

    async def remove_participant(
        self,
        *,
        workspace_id: UUID,
        group_id: UUID,
        participant_id: UUID,
        actor_user_id: UUID | None,
        idempotency_key: str,
    ) -> MetaGroupParticipantRemoveRead:
        group = await self._group(workspace_id, group_id)
        participant = await self.db.scalar(
            select(MetaGroupParticipant).where(
                MetaGroupParticipant.workspace_id == workspace_id,
                MetaGroupParticipant.group_id == group_id,
                MetaGroupParticipant.id == participant_id,
            )
        )
        if participant is None:
            raise MetaGroupNotFoundError("Group participant was not found in this workspace.")

        event_key = f"meta-group-participant-remove:{idempotency_key}"
        existing_event = await self.db.scalar(
            select(MetaGroupLifecycleEvent).where(
                MetaGroupLifecycleEvent.workspace_id == workspace_id,
                MetaGroupLifecycleEvent.idempotency_key == event_key,
            )
        )
        if existing_event is not None:
            snapshot = existing_event.state_snapshot or {}
            return MetaGroupParticipantRemoveRead(
                participant=MetaGroupParticipantRead.model_validate(participant),
                group=await self._group_read(workspace_id=workspace_id, group=group),
                executed=bool(snapshot.get("executed", False)),
                prepared=bool(snapshot.get("prepared", False)),
                blocked_reasons=list(snapshot.get("blockedReasons", [])),
                deduplicated=True,
            )

        if participant.state in {"removed", "left", "rejected"}:
            raise MetaGroupConflictError("The participant is already outside the group.")

        connection = await self._connection(workspace_id, group.connection_id)
        capability = await self._capability(workspace_id, group.connection_id)
        gate = self.evaluate_capability(
            workspace_id=workspace_id,
            connection=connection,
            capability=capability,
        )

        if group.group_kind == "local_internal":
            if group.participant_count <= 1:
                raise MetaGroupValidationError(
                    "At least one participant is required in the group."
                )
            participant.state = "removed"
            participant.left_at = _now()
            group.participant_count = max(1, group.participant_count - 1)
            group.version += 1
            executed = True
            prepared = True
            blocked_reasons: list[str] = []
            event_type = "participant_removed_locally"
        else:
            executed = False
            prepared = True
            blocked_reasons = _deduplicate(
                [
                    *gate.execution_denial_reasons,
                    "official_participant_removal_requires_provider_webhook_confirmation",
                ]
            )
            event_type = "participant_removal_prepared"

        await self.db.flush()
        event_payload = {
            "group_id": str(group_id),
            "participant_id": str(participant_id),
            "executed": executed,
        }
        self.db.add(
            MetaGroupLifecycleEvent(
                workspace_id=workspace_id,
                group_id=group.id,
                operation_id=None,
                connection_id=group.connection_id,
                actor_user_id=actor_user_id,
                source="local_control",
                webhook_field=None,
                event_type=event_type,
                external_event_id=None,
                idempotency_key=event_key,
                payload_sha256=_payload_sha256(event_payload),
                event_payload=event_payload,
                state_snapshot={
                    "executed": executed,
                    "prepared": prepared,
                    "blockedReasons": blocked_reasons,
                },
                occurred_at=_now(),
            )
        )
        await self.db.commit()
        await self.db.refresh(participant)
        await self.db.refresh(group)
        return MetaGroupParticipantRemoveRead(
            participant=MetaGroupParticipantRead.model_validate(participant),
            group=await self._group_read(workspace_id=workspace_id, group=group),
            executed=executed,
            prepared=prepared,
            blocked_reasons=blocked_reasons,
            deduplicated=False,
        )

    async def set_pinned_message(
        self,
        *,
        workspace_id: UUID,
        group_id: UUID,
        actor_user_id: UUID | None,
        idempotency_key: str,
        payload: MetaGroupPinnedMessageSet,
    ) -> MetaGroupRead:
        group = await self._group(workspace_id, group_id)
        event_key = f"meta-group-pin:{idempotency_key}"
        existing_event = await self.db.scalar(
            select(MetaGroupLifecycleEvent).where(
                MetaGroupLifecycleEvent.workspace_id == workspace_id,
                MetaGroupLifecycleEvent.idempotency_key == event_key,
            )
        )
        if existing_event is not None:
            return await self._group_read(workspace_id=workspace_id, group=group)

        pinned_at = _now()
        metadata = dict(group.group_metadata or {})
        metadata["pinnedMessage"] = {
            "text": payload.text,
            "pinnedByUserId": str(actor_user_id) if actor_user_id is not None else None,
            "pinnedAt": pinned_at.isoformat(),
        }
        group.group_metadata = metadata
        group.version += 1
        await self.db.flush()
        event_payload = {"group_id": str(group_id), "text": payload.text}
        self.db.add(
            MetaGroupLifecycleEvent(
                workspace_id=workspace_id,
                group_id=group.id,
                operation_id=None,
                connection_id=group.connection_id,
                actor_user_id=actor_user_id,
                source="local_control",
                webhook_field=None,
                event_type="message_pinned",
                external_event_id=None,
                idempotency_key=event_key,
                payload_sha256=_payload_sha256(event_payload),
                event_payload=event_payload,
                state_snapshot={"pinned": True},
                occurred_at=pinned_at,
            )
        )
        await self.db.commit()
        await self.db.refresh(group)
        return await self._group_read(workspace_id=workspace_id, group=group)

    async def clear_pinned_message(
        self,
        *,
        workspace_id: UUID,
        group_id: UUID,
        actor_user_id: UUID | None,
        idempotency_key: str,
    ) -> MetaGroupRead:
        group = await self._group(workspace_id, group_id)
        event_key = f"meta-group-unpin:{idempotency_key}"
        existing_event = await self.db.scalar(
            select(MetaGroupLifecycleEvent).where(
                MetaGroupLifecycleEvent.workspace_id == workspace_id,
                MetaGroupLifecycleEvent.idempotency_key == event_key,
            )
        )
        if existing_event is not None:
            return await self._group_read(workspace_id=workspace_id, group=group)

        metadata = dict(group.group_metadata or {})
        metadata.pop("pinnedMessage", None)
        group.group_metadata = metadata
        group.version += 1
        await self.db.flush()
        event_payload = {"group_id": str(group_id)}
        self.db.add(
            MetaGroupLifecycleEvent(
                workspace_id=workspace_id,
                group_id=group.id,
                operation_id=None,
                connection_id=group.connection_id,
                actor_user_id=actor_user_id,
                source="local_control",
                webhook_field=None,
                event_type="message_unpinned",
                external_event_id=None,
                idempotency_key=event_key,
                payload_sha256=_payload_sha256(event_payload),
                event_payload=event_payload,
                state_snapshot={"pinned": False},
                occurred_at=_now(),
            )
        )
        await self.db.commit()
        await self.db.refresh(group)
        return await self._group_read(workspace_id=workspace_id, group=group)


__all__ = [
    "META_GROUP_MAX_GROUPS_PER_BUSINESS_NUMBER",
    "META_GROUP_MAX_PARTICIPANTS",
    "META_GROUP_REQUIRED_WEBHOOK_FIELDS",
    "MetaGroupConflictError",
    "MetaGroupDomainError",
    "MetaGroupNotFoundError",
    "MetaGroupService",
    "MetaGroupValidationError",
]
