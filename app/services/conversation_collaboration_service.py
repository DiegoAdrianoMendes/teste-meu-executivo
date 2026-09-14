"""Native, tenant-scoped collaboration for canonical conversations."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.klm_runtime import external_delivery_allowed
from app.db.advisory import acquire_transaction_advisory_lock
from app.models.conversation_collaboration import (
    ConversationCollaborationGroup,
    ConversationGroupParticipant,
    ConversationMention,
    ConversationMentionOperation,
    ConversationMentionType,
    ConversationMessageReference,
    ConversationParticipantType,
)
from app.models.klm_operations import OmnichannelMessage, OmnichannelThread
from app.models.omnichannel_v6 import WorkspaceTeam
from app.models.oss_bridge import (
    ExternalEntityLink,
    ExternalEntitySyncState,
    OSSBridgeProvider,
)
from app.models.user import User
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership
from app.schemas.conversation_collaboration import (
    ConversationMentionCreate,
    ConversationMessageMentionsCreate,
    ConversationParticipantCreate,
    ConversationReferenceCreate,
)
from app.services.klm_operations_service import KlmOperationsService
from app.services.oss_capability_registry import oss_capability_registry

_NATIVE_CAPABILITIES = {
    "structured_references": "Typed reply, quote and context links are stored locally.",
    "user_mentions": "Active workspace users are resolved before the mention is stored.",
    "team_mentions": "Active workspace teams are resolved before the mention is stored.",
    "group_participants": "Thread groups and participants are canonical KLM records.",
    "participant_roles": "Owner, admin, member and observer roles are enforced locally.",
}

_PROVIDER_CAPABILITY_IDS: dict[str, dict[str, str | None]] = {
    "chatwoot": {
        "structured_references": None,
        "user_mentions": None,
        "team_mentions": None,
        "group_participants": "chatwoot.teams",
        "participant_roles": None,
    },
    "rocketchat": {
        "structured_references": "rocketchat.threads-discussions",
        "user_mentions": "rocketchat.mentions-agent-bots",
        "team_mentions": "rocketchat.room-mentions",
        "group_participants": "rocketchat.teams-membership",
        "participant_roles": "rocketchat.roles-permissions-admin",
    },
}


class ConversationCollaborationConflictError(ValueError):
    """An idempotency key or immutable participant fact conflicts."""


@dataclass(frozen=True)
class ReferenceCreateResult:
    reference: ConversationMessageReference
    mentions: tuple[ConversationMention, ...]
    deduplicated: bool


@dataclass(frozen=True)
class MentionCreateResult:
    operation: ConversationMentionOperation
    mentions: tuple[ConversationMention, ...]
    deduplicated: bool


@dataclass(frozen=True)
class ParticipantCreateResult:
    group: ConversationCollaborationGroup
    participant: ConversationGroupParticipant
    deduplicated: bool


def _now() -> datetime:
    return datetime.now(UTC)


def _hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _reference_payload(thread_id: UUID, payload: ConversationReferenceCreate) -> dict[str, Any]:
    mentions = _mention_targets(payload.mentions)
    return {
        "threadId": str(thread_id),
        "sourceMessageId": str(payload.source_message_id),
        "targetMessageId": str(payload.target_message_id),
        "referenceType": payload.reference_type.value,
        "mentions": mentions,
    }


def _mention_targets(mentions: list[ConversationMentionCreate]) -> list[dict[str, str]]:
    return sorted(
        (
            {
                "mentionType": mention.mention_type.value,
                "targetId": str(mention.target_id),
            }
            for mention in mentions
        ),
        key=lambda item: (item["mentionType"], item["targetId"]),
    )


def _message_mentions_payload(
    thread_id: UUID, payload: ConversationMessageMentionsCreate
) -> dict[str, Any]:
    return {
        "threadId": str(thread_id),
        "sourceMessageId": str(payload.source_message_id),
        "mentions": _mention_targets(payload.mentions),
    }


def _participant_payload(thread_id: UUID, payload: ConversationParticipantCreate) -> dict[str, Any]:
    return {
        "threadId": str(thread_id),
        "participantType": payload.participant_type.value,
        "participantId": str(payload.participant_id),
        "role": payload.role.value,
    }


def provider_collaboration_gate_open() -> bool:
    """Use the same explicit gate that protects local OSS bridge launches."""

    return bool(settings.uses_local_infrastructure and settings.KLM_OSS_LOCAL_BRIDGES_ENABLED)


@lru_cache(maxsize=1)
def _official_provider_contracts() -> dict[str, dict[str, Any]]:
    wanted = {
        capability_id
        for capabilities in _PROVIDER_CAPABILITY_IDS.values()
        for capability_id in capabilities.values()
        if capability_id is not None
    }
    return {
        str(item["id"]): {
            "contractEligible": bool(item.get("contractEligible")),
            "currentExposure": str(item.get("currentExposure") or "none"),
            "disposition": str(item.get("disposition") or "discovered"),
            "externalEffectVerified": bool(item.get("externalEffectVerified")),
            "reason": str(item.get("reason") or ""),
        }
        for item in oss_capability_registry()["capabilities"]
        if item.get("id") in wanted
    }


def _provider_capability(
    *,
    provider: str,
    capability: str,
    capability_id: str | None,
    contract: dict[str, Any] | None,
    linked: bool,
    gate_open: bool,
) -> dict[str, Any]:
    base = {
        "capability": capability,
        "scope": "provider",
        "provider": provider,
        "official_capability_id": capability_id,
        "provider_linked": linked,
        "gate_open": gate_open,
        "external_effect_verified": bool(contract and contract.get("externalEffectVerified")),
    }
    if capability_id is None or contract is None:
        return {
            **base,
            "status": "unavailable",
            "reason": "The official provider contract does not declare this capability.",
        }
    if (
        not contract.get("contractEligible")
        or str(contract.get("disposition") or "").startswith("blocked_")
        or contract.get("disposition") == "not_applicable"
        or contract.get("currentExposure") == "none"
    ):
        return {
            **base,
            "status": "unavailable",
            "reason": contract.get("reason") or "The provider capability is blocked.",
        }
    if not linked:
        reason = "No synced provider binding exists for this workspace and thread."
    elif not gate_open:
        reason = "The official local provider bridge gate is closed."
    elif not contract.get("externalEffectVerified"):
        reason = "The provider effect is not independently verified for this runtime."
    else:
        return {
            **base,
            "status": "supported",
            "reason": contract.get("reason") or "Provider capability is verified and enabled.",
        }
    return {**base, "status": "pending_enablement", "reason": reason}


class ConversationCollaborationService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.operations = KlmOperationsService(db)

    async def _lock_operation(
        self,
        workspace_id: UUID,
        idempotency_key: str,
        *,
        namespace: str = "conversation-collaboration",
    ) -> None:
        await acquire_transaction_advisory_lock(
            self.db,
            namespace=namespace,
            value=f"{workspace_id}:{idempotency_key}",
        )

    async def _thread(self, workspace_id: UUID, thread_id: UUID) -> OmnichannelThread:
        thread = await self.db.scalar(
            select(OmnichannelThread).where(
                OmnichannelThread.workspace_id == workspace_id,
                OmnichannelThread.id == thread_id,
            )
        )
        if thread is None:
            raise LookupError("Conversation not found in this workspace.")
        return thread

    async def _messages_in_thread(
        self,
        workspace_id: UUID,
        thread_id: UUID,
        message_ids: set[UUID],
    ) -> dict[UUID, OmnichannelMessage]:
        result = await self.db.execute(
            select(OmnichannelMessage).where(
                OmnichannelMessage.workspace_id == workspace_id,
                OmnichannelMessage.thread_id == thread_id,
                OmnichannelMessage.id.in_(message_ids),
            )
        )
        messages = {message.id: message for message in result.scalars().all()}
        if set(messages) != message_ids:
            raise LookupError("Every referenced message must belong to this workspace and thread.")
        return messages

    async def _resolve_user(self, workspace_id: UUID, user_id: UUID) -> tuple[str, str]:
        row = (
            await self.db.execute(
                select(User.name)
                .join(
                    WorkspaceMembership,
                    WorkspaceMembership.user_id == User.id,
                )
                .where(
                    WorkspaceMembership.workspace_id == workspace_id,
                    WorkspaceMembership.user_id == user_id,
                    WorkspaceMembership.status == MembershipStatus.active,
                    User.id == user_id,
                    User.is_active.is_(True),
                    User.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if not row:
            raise LookupError("Mentioned user is not an active member of this workspace.")
        return row.strip(), "workspace_membership"

    async def _resolve_team(self, workspace_id: UUID, team_id: UUID) -> tuple[str, str]:
        name = await self.db.scalar(
            select(WorkspaceTeam.name).where(
                WorkspaceTeam.workspace_id == workspace_id,
                WorkspaceTeam.id == team_id,
                WorkspaceTeam.is_active.is_(True),
            )
        )
        if not name:
            raise LookupError("Mentioned team is not active in this workspace.")
        return name.strip(), "workspace_team"

    async def _resolve_mention(
        self, workspace_id: UUID, mention: ConversationMentionCreate
    ) -> tuple[str, str]:
        if mention.mention_type == ConversationMentionType.user:
            return await self._resolve_user(workspace_id, mention.target_id)
        return await self._resolve_team(workspace_id, mention.target_id)

    async def _mentions_for_reference(
        self,
        workspace_id: UUID,
        thread_id: UUID,
        reference_id: UUID,
    ) -> tuple[ConversationMention, ...]:
        result = await self.db.execute(
            select(ConversationMention)
            .where(
                ConversationMention.workspace_id == workspace_id,
                ConversationMention.thread_id == thread_id,
                ConversationMention.reference_id == reference_id,
            )
            .order_by(ConversationMention.created_at, ConversationMention.id)
        )
        return tuple(result.scalars().all())

    async def _mentions_for_operation(
        self,
        workspace_id: UUID,
        thread_id: UUID,
        operation_id: UUID,
    ) -> tuple[ConversationMention, ...]:
        result = await self.db.execute(
            select(ConversationMention)
            .where(
                ConversationMention.workspace_id == workspace_id,
                ConversationMention.thread_id == thread_id,
                ConversationMention.operation_id == operation_id,
            )
            .order_by(ConversationMention.created_at, ConversationMention.id)
        )
        return tuple(result.scalars().all())

    async def create_reference(
        self,
        workspace_id: UUID,
        thread_id: UUID,
        actor_user_id: UUID,
        payload: ConversationReferenceCreate,
    ) -> ReferenceCreateResult:
        await self._thread(workspace_id, thread_id)
        await self._lock_operation(workspace_id, payload.idempotency_key)
        payload_sha256 = _hash(_reference_payload(thread_id, payload))
        existing = await self.db.scalar(
            select(ConversationMessageReference).where(
                ConversationMessageReference.workspace_id == workspace_id,
                ConversationMessageReference.idempotency_key == payload.idempotency_key,
            )
        )
        if existing is not None:
            if existing.thread_id != thread_id or existing.payload_sha256 != payload_sha256:
                raise ConversationCollaborationConflictError(
                    "Idempotency key already used with a different collaboration reference."
                )
            mentions = await self._mentions_for_reference(workspace_id, thread_id, existing.id)
            if len(mentions) != len(payload.mentions):
                raise RuntimeError("Reference mention operation is incomplete and cannot replay.")
            return ReferenceCreateResult(existing, mentions, True)

        if payload.mentions:
            mention_operation = await self.db.scalar(
                select(ConversationMentionOperation).where(
                    ConversationMentionOperation.workspace_id == workspace_id,
                    ConversationMentionOperation.idempotency_key == payload.idempotency_key,
                )
            )
            if mention_operation is not None:
                raise ConversationCollaborationConflictError(
                    "Idempotency key already used by another mention operation."
                )

        await self._messages_in_thread(
            workspace_id,
            thread_id,
            {payload.source_message_id, payload.target_message_id},
        )
        resolved_mentions = [
            (mention, *(await self._resolve_mention(workspace_id, mention)))
            for mention in payload.mentions
        ]

        reference = ConversationMessageReference(
            workspace_id=workspace_id,
            thread_id=thread_id,
            source_message_id=payload.source_message_id,
            target_message_id=payload.target_message_id,
            reference_type=payload.reference_type,
            idempotency_key=payload.idempotency_key,
            payload_sha256=payload_sha256,
            created_by_user_id=actor_user_id,
        )
        self.db.add(reference)
        await self.db.flush()

        mentions: list[ConversationMention] = []
        mention_operation = None
        if resolved_mentions:
            mention_operation = ConversationMentionOperation(
                workspace_id=workspace_id,
                thread_id=thread_id,
                source_message_id=payload.source_message_id,
                reference_id=reference.id,
                idempotency_key=payload.idempotency_key,
                payload_sha256=payload_sha256,
                created_by_user_id=actor_user_id,
            )
            self.db.add(mention_operation)
            await self.db.flush()
        for mention_input, label, source in resolved_mentions:
            assert mention_operation is not None
            mention = ConversationMention(
                workspace_id=workspace_id,
                thread_id=thread_id,
                operation_id=mention_operation.id,
                reference_id=reference.id,
                source_message_id=payload.source_message_id,
                mention_type=mention_input.mention_type,
                mentioned_user_id=(
                    mention_input.target_id
                    if mention_input.mention_type == ConversationMentionType.user
                    else None
                ),
                mentioned_team_id=(
                    mention_input.target_id
                    if mention_input.mention_type == ConversationMentionType.team
                    else None
                ),
                resolved_label=label,
                resolution_source=source,
                created_by_user_id=actor_user_id,
            )
            self.db.add(mention)
            mentions.append(mention)
        await self.db.flush()

        for mention in mentions:
            await self.operations.record_audit(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                actor_type="human",
                actor_label="KLM user",
                event_type="conversation_mention_resolved",
                entity_type="conversation_mention",
                entity_id=mention.id,
                summary="Conversation mention resolved to a workspace-owned target.",
                correlation_id=payload.idempotency_key,
                after_data={
                    "threadId": str(thread_id),
                    "operationId": str(mention.operation_id),
                    "referenceId": str(reference.id),
                    "mentionType": mention.mention_type.value,
                    "targetId": str(mention.mentioned_user_id or mention.mentioned_team_id),
                    "resolutionSource": mention.resolution_source,
                },
            )
        await self.operations.record_audit(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            actor_type="human",
            actor_label="KLM user",
            event_type="conversation_reference_created",
            entity_type="conversation_message_reference",
            entity_id=reference.id,
            summary="Structured conversation message reference created.",
            correlation_id=payload.idempotency_key,
            after_data={
                "threadId": str(thread_id),
                "sourceMessageId": str(payload.source_message_id),
                "targetMessageId": str(payload.target_message_id),
                "referenceType": payload.reference_type.value,
                "mentionOperationId": (
                    str(mention_operation.id) if mention_operation is not None else None
                ),
                "mentionIds": [str(mention.id) for mention in mentions],
                "payloadSha256": payload_sha256,
            },
        )
        ordered_mentions = tuple(sorted(mentions, key=lambda item: (item.created_at, item.id)))
        return ReferenceCreateResult(reference, ordered_mentions, False)

    async def attach_mentions(
        self,
        workspace_id: UUID,
        thread_id: UUID,
        actor_user_id: UUID,
        payload: ConversationMessageMentionsCreate,
    ) -> MentionCreateResult:
        await self._thread(workspace_id, thread_id)
        await self._lock_operation(workspace_id, payload.idempotency_key)
        payload_sha256 = _hash(_message_mentions_payload(thread_id, payload))
        existing = await self.db.scalar(
            select(ConversationMentionOperation).where(
                ConversationMentionOperation.workspace_id == workspace_id,
                ConversationMentionOperation.idempotency_key == payload.idempotency_key,
            )
        )
        if existing is not None:
            if (
                existing.thread_id != thread_id
                or existing.source_message_id != payload.source_message_id
                or existing.reference_id is not None
                or existing.payload_sha256 != payload_sha256
            ):
                raise ConversationCollaborationConflictError(
                    "Idempotency key already used with a different mention operation."
                )
            mentions = await self._mentions_for_operation(
                workspace_id,
                thread_id,
                existing.id,
            )
            if len(mentions) != len(payload.mentions):
                raise RuntimeError("Mention operation is incomplete and cannot be replayed.")
            return MentionCreateResult(existing, mentions, True)

        await self._messages_in_thread(
            workspace_id,
            thread_id,
            {payload.source_message_id},
        )
        resolved_mentions = [
            (mention, *(await self._resolve_mention(workspace_id, mention)))
            for mention in payload.mentions
        ]
        operation = ConversationMentionOperation(
            workspace_id=workspace_id,
            thread_id=thread_id,
            source_message_id=payload.source_message_id,
            reference_id=None,
            idempotency_key=payload.idempotency_key,
            payload_sha256=payload_sha256,
            created_by_user_id=actor_user_id,
        )
        self.db.add(operation)
        await self.db.flush()

        mentions: list[ConversationMention] = []
        for mention_input, label, source in resolved_mentions:
            mention = ConversationMention(
                workspace_id=workspace_id,
                thread_id=thread_id,
                operation_id=operation.id,
                reference_id=None,
                source_message_id=payload.source_message_id,
                mention_type=mention_input.mention_type,
                mentioned_user_id=(
                    mention_input.target_id
                    if mention_input.mention_type == ConversationMentionType.user
                    else None
                ),
                mentioned_team_id=(
                    mention_input.target_id
                    if mention_input.mention_type == ConversationMentionType.team
                    else None
                ),
                resolved_label=label,
                resolution_source=source,
                created_by_user_id=actor_user_id,
            )
            self.db.add(mention)
            mentions.append(mention)
        await self.db.flush()

        for mention in mentions:
            await self.operations.record_audit(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                actor_type="human",
                actor_label="KLM user",
                event_type="conversation_mention_resolved",
                entity_type="conversation_mention",
                entity_id=mention.id,
                summary="Standalone message mention resolved to a workspace-owned target.",
                correlation_id=payload.idempotency_key,
                after_data={
                    "threadId": str(thread_id),
                    "sourceMessageId": str(payload.source_message_id),
                    "operationId": str(operation.id),
                    "referenceId": None,
                    "mentionType": mention.mention_type.value,
                    "targetId": str(mention.mentioned_user_id or mention.mentioned_team_id),
                    "resolutionSource": mention.resolution_source,
                },
            )
        await self.operations.record_audit(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            actor_type="human",
            actor_label="KLM user",
            event_type="conversation_mentions_attached",
            entity_type="omnichannel_message",
            entity_id=payload.source_message_id,
            summary="Standalone mentions attached to a canonical message.",
            correlation_id=payload.idempotency_key,
            after_data={
                "threadId": str(thread_id),
                "operationId": str(operation.id),
                "referenceId": None,
                "mentionIds": [str(mention.id) for mention in mentions],
                "payloadSha256": payload_sha256,
            },
        )
        ordered_mentions = tuple(sorted(mentions, key=lambda item: (item.created_at, item.id)))
        return MentionCreateResult(operation, ordered_mentions, False)

    async def list_message_mentions(
        self,
        workspace_id: UUID,
        thread_id: UUID,
        source_message_id: UUID,
        actor_user_id: UUID,
    ) -> list[ConversationMention]:
        await self._thread(workspace_id, thread_id)
        await self._messages_in_thread(workspace_id, thread_id, {source_message_id})
        result = await self.db.execute(
            select(ConversationMention)
            .where(
                ConversationMention.workspace_id == workspace_id,
                ConversationMention.thread_id == thread_id,
                ConversationMention.source_message_id == source_message_id,
            )
            .order_by(ConversationMention.created_at, ConversationMention.id)
        )
        mentions = list(result.scalars().all())
        await self.operations.record_audit(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            actor_type="human",
            actor_label="KLM user",
            event_type="conversation_message_mentions_listed",
            entity_type="omnichannel_message",
            entity_id=source_message_id,
            summary="Resolved mentions listed for a canonical message.",
            after_data={"threadId": str(thread_id), "count": len(mentions)},
        )
        return mentions

    async def list_references(
        self,
        workspace_id: UUID,
        thread_id: UUID,
        actor_user_id: UUID,
        *,
        limit: int = 200,
    ) -> list[tuple[ConversationMessageReference, tuple[ConversationMention, ...]]]:
        await self._thread(workspace_id, thread_id)
        result = await self.db.execute(
            select(ConversationMessageReference)
            .where(
                ConversationMessageReference.workspace_id == workspace_id,
                ConversationMessageReference.thread_id == thread_id,
            )
            .order_by(
                ConversationMessageReference.created_at.desc(),
                ConversationMessageReference.id.desc(),
            )
            .limit(limit)
        )
        references = list(result.scalars().all())
        mentions_by_reference: defaultdict[UUID, list[ConversationMention]] = defaultdict(list)
        if references:
            mention_result = await self.db.execute(
                select(ConversationMention)
                .where(
                    ConversationMention.workspace_id == workspace_id,
                    ConversationMention.thread_id == thread_id,
                    ConversationMention.reference_id.in_([item.id for item in references]),
                )
                .order_by(ConversationMention.created_at, ConversationMention.id)
            )
            for mention in mention_result.scalars().all():
                mentions_by_reference[mention.reference_id].append(mention)
        await self.operations.record_audit(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            actor_type="human",
            actor_label="KLM user",
            event_type="conversation_references_listed",
            entity_type="omnichannel_thread",
            entity_id=thread_id,
            summary="Structured conversation references listed.",
            after_data={"count": len(references), "limit": limit},
        )
        return [(reference, tuple(mentions_by_reference[reference.id])) for reference in references]

    async def _group(
        self, workspace_id: UUID, thread_id: UUID
    ) -> ConversationCollaborationGroup | None:
        return await self.db.scalar(
            select(ConversationCollaborationGroup).where(
                ConversationCollaborationGroup.workspace_id == workspace_id,
                ConversationCollaborationGroup.thread_id == thread_id,
            )
        )

    async def _ensure_group(
        self,
        workspace_id: UUID,
        thread: OmnichannelThread,
        actor_user_id: UUID,
    ) -> ConversationCollaborationGroup:
        await acquire_transaction_advisory_lock(
            self.db,
            namespace="conversation-collaboration-group",
            value=f"{workspace_id}:{thread.id}",
        )
        existing = await self._group(workspace_id, thread.id)
        if existing is not None:
            return existing
        subject = (thread.subject or "").strip()
        group = ConversationCollaborationGroup(
            workspace_id=workspace_id,
            thread_id=thread.id,
            name=(subject or "Conversation collaboration")[:220],
            created_by_user_id=actor_user_id,
        )
        self.db.add(group)
        await self.db.flush()
        await self.operations.record_audit(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            actor_type="human",
            actor_label="KLM user",
            event_type="conversation_group_created",
            entity_type="conversation_collaboration_group",
            entity_id=group.id,
            summary="Native collaboration group created for a canonical conversation.",
            after_data={"threadId": str(thread.id)},
        )
        return group

    async def create_participant(
        self,
        workspace_id: UUID,
        thread_id: UUID,
        actor_user_id: UUID,
        payload: ConversationParticipantCreate,
    ) -> ParticipantCreateResult:
        thread = await self._thread(workspace_id, thread_id)
        await self._lock_operation(
            workspace_id,
            payload.idempotency_key,
            namespace="conversation-collaboration-participant",
        )
        payload_sha256 = _hash(_participant_payload(thread_id, payload))
        existing = await self.db.scalar(
            select(ConversationGroupParticipant).where(
                ConversationGroupParticipant.workspace_id == workspace_id,
                ConversationGroupParticipant.idempotency_key == payload.idempotency_key,
            )
        )
        if existing is not None:
            if existing.thread_id != thread_id or existing.payload_sha256 != payload_sha256:
                raise ConversationCollaborationConflictError(
                    "Idempotency key already used with a different participant command."
                )
            group = await self._group(workspace_id, thread_id)
            if group is None or group.id != existing.group_id:
                raise RuntimeError("Participant references a missing collaboration group.")
            return ParticipantCreateResult(group, existing, True)

        if payload.participant_type == ConversationParticipantType.user:
            label, source = await self._resolve_user(workspace_id, payload.participant_id)
        else:
            label, source = await self._resolve_team(workspace_id, payload.participant_id)
        group = await self._ensure_group(workspace_id, thread, actor_user_id)
        target_filter = (
            ConversationGroupParticipant.user_id == payload.participant_id
            if payload.participant_type == ConversationParticipantType.user
            else ConversationGroupParticipant.team_id == payload.participant_id
        )
        duplicate_target = await self.db.scalar(
            select(ConversationGroupParticipant).where(
                ConversationGroupParticipant.workspace_id == workspace_id,
                ConversationGroupParticipant.group_id == group.id,
                target_filter,
            )
        )
        if duplicate_target is not None:
            raise ConversationCollaborationConflictError(
                "Participant already belongs to this collaboration group."
            )

        participant = ConversationGroupParticipant(
            workspace_id=workspace_id,
            thread_id=thread_id,
            group_id=group.id,
            participant_type=payload.participant_type,
            user_id=(
                payload.participant_id
                if payload.participant_type == ConversationParticipantType.user
                else None
            ),
            team_id=(
                payload.participant_id
                if payload.participant_type == ConversationParticipantType.team
                else None
            ),
            role=payload.role,
            resolved_label=label,
            resolution_source=source,
            idempotency_key=payload.idempotency_key,
            payload_sha256=payload_sha256,
            created_by_user_id=actor_user_id,
        )
        self.db.add(participant)
        await self.db.flush()
        await self.operations.record_audit(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            actor_type="human",
            actor_label="KLM user",
            event_type="conversation_participant_created",
            entity_type="conversation_group_participant",
            entity_id=participant.id,
            summary="Resolved participant added to a native conversation group.",
            correlation_id=payload.idempotency_key,
            after_data={
                "threadId": str(thread_id),
                "groupId": str(group.id),
                "participantType": payload.participant_type.value,
                "participantId": str(payload.participant_id),
                "role": payload.role.value,
                "resolutionSource": source,
                "payloadSha256": payload_sha256,
            },
        )
        return ParticipantCreateResult(group, participant, False)

    async def list_participants(
        self,
        workspace_id: UUID,
        thread_id: UUID,
        actor_user_id: UUID,
    ) -> tuple[ConversationCollaborationGroup | None, list[ConversationGroupParticipant]]:
        await self._thread(workspace_id, thread_id)
        group = await self._group(workspace_id, thread_id)
        participants: list[ConversationGroupParticipant] = []
        if group is not None:
            result = await self.db.execute(
                select(ConversationGroupParticipant)
                .where(
                    ConversationGroupParticipant.workspace_id == workspace_id,
                    ConversationGroupParticipant.thread_id == thread_id,
                    ConversationGroupParticipant.group_id == group.id,
                )
                .order_by(
                    ConversationGroupParticipant.created_at,
                    ConversationGroupParticipant.id,
                )
            )
            participants = list(result.scalars().all())
        await self.operations.record_audit(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            actor_type="human",
            actor_label="KLM user",
            event_type="conversation_participants_listed",
            entity_type="omnichannel_thread",
            entity_id=thread_id,
            summary="Native conversation participants listed.",
            after_data={"count": len(participants), "groupId": str(group.id) if group else None},
        )
        return group, participants

    async def capabilities(
        self,
        workspace_id: UUID,
        thread_id: UUID,
        actor_user_id: UUID,
    ) -> dict[str, Any]:
        await self._thread(workspace_id, thread_id)
        group = await self._group(workspace_id, thread_id)
        participant_count = 0
        if group is not None:
            participant_count = int(
                await self.db.scalar(
                    select(func.count(ConversationGroupParticipant.id)).where(
                        ConversationGroupParticipant.workspace_id == workspace_id,
                        ConversationGroupParticipant.thread_id == thread_id,
                        ConversationGroupParticipant.group_id == group.id,
                    )
                )
                or 0
            )

        link_result = await self.db.execute(
            select(ExternalEntityLink.provider).where(
                ExternalEntityLink.workspace_id == workspace_id,
                ExternalEntityLink.klm_entity_type == "omnichannel_thread",
                ExternalEntityLink.klm_entity_id == thread_id,
                ExternalEntityLink.external_entity_type.in_(["conversation", "room"]),
                ExternalEntityLink.sync_state == ExternalEntitySyncState.synced,
                ExternalEntityLink.provider.in_(
                    [OSSBridgeProvider.chatwoot, OSSBridgeProvider.rocketchat]
                ),
            )
        )
        linked_providers = {
            provider.value if isinstance(provider, OSSBridgeProvider) else str(provider)
            for provider in link_result.scalars().all()
        }
        local_bridge_gate = provider_collaboration_gate_open()
        contracts = _official_provider_contracts()
        capabilities: list[dict[str, Any]] = [
            {
                "capability": capability,
                "scope": "native",
                "status": "supported",
                "provider": "klm_native",
                "official_capability_id": None,
                "provider_linked": True,
                "gate_open": True,
                "external_effect_verified": False,
                "reason": reason,
            }
            for capability, reason in _NATIVE_CAPABILITIES.items()
        ]
        for provider, provider_capabilities in _PROVIDER_CAPABILITY_IDS.items():
            for capability, capability_id in provider_capabilities.items():
                capabilities.append(
                    _provider_capability(
                        provider=provider,
                        capability=capability,
                        capability_id=capability_id,
                        contract=contracts.get(capability_id) if capability_id else None,
                        linked=provider in linked_providers,
                        gate_open=local_bridge_gate,
                    )
                )

        provider_statuses = [item["status"] for item in capabilities if item["scope"] == "provider"]
        await self.operations.record_audit(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            actor_type="human",
            actor_label="KLM user",
            event_type="conversation_capabilities_viewed",
            entity_type="omnichannel_thread",
            entity_id=thread_id,
            summary="Gated collaboration capability matrix viewed.",
            after_data={
                "localBridgeGateOpen": local_bridge_gate,
                "externalDeliveryGateOpen": external_delivery_allowed(),
                "linkedProviders": sorted(linked_providers),
                "providerStatuses": {
                    status: provider_statuses.count(status)
                    for status in ("supported", "unavailable", "pending_enablement")
                },
            },
        )
        return {
            "workspace_id": workspace_id,
            "thread_id": thread_id,
            "group_id": group.id if group else None,
            "participant_count": participant_count,
            "local_bridge_gate_open": local_bridge_gate,
            "external_delivery_gate_open": external_delivery_allowed(),
            "capabilities": capabilities,
            "observed_at": _now(),
        }


__all__ = [
    "ConversationCollaborationConflictError",
    "ConversationCollaborationService",
    "MentionCreateResult",
    "ParticipantCreateResult",
    "ReferenceCreateResult",
    "provider_collaboration_gate_open",
]
