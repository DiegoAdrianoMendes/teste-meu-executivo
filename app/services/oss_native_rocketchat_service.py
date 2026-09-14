"""Tenant-scoped native read and command adapter for the local Rocket.Chat runtime.

The adapter deliberately treats KLM links and audit records as canonical. Rocket.Chat
is a bound local projection, and the audited 8.6.1 source is not presented as proof of
compatibility with the configured 6.13.1 runtime.
"""

from __future__ import annotations

import hashlib
import importlib
import inspect
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.klm_operations import (
    MessageAuthorType,
    MessageDirection,
    OmnichannelChannel,
)
from app.models.oss_bridge import (
    ExternalEntityLink,
    ExternalEntitySyncState,
    IntegrationEventReceipt,
    IntegrationEventReceiptStatus,
    OSSBridgeProvider,
    sanitize_oss_json,
)
from app.models.workspace_membership import (
    MembershipStatus,
    WorkspaceMembership,
    WorkspaceRole,
)
from app.schemas.klm_operations import OmnichannelMessageCreate
from app.schemas.oss_bridge import IntegrationEventReceiptCreate
from app.services.klm_operations_service import KlmOperationsService
from app.services.oss_bridge_service import OSSBridgeService
from app.services.rocketchat_bridge_service import (
    RocketChatBridgeService,
    RocketChatWorkspaceBindingError,
    _account_id,
)
from app.services.rocketchat_client import RocketChatClient, RocketChatClientError
from app.services.rocketchat_operations_service import RocketChatOperationsService

RUNTIME_VERSION = "6.13.1"
AUDITED_SOURCE_VERSION = "8.6.1"

_RUNTIME_RISK = {
    "runtimeVersion": RUNTIME_VERSION,
    "auditedSourceVersion": AUDITED_SOURCE_VERSION,
    "compatibilityStatus": "unverified",
    "compatible": None,
    "warning": (
        "O runtime 6.13.1 e a fonte auditada 8.6.1 divergem; cada endpoint precisa "
        "de smoke test no runtime antes de uma alegacao de compatibilidade."
    ),
}

_GATED_CAPABILITIES = {
    "e2ee",
    "webdav",
    "enterprise-controls",
    "external-push-conference",
}

_ROOM_CAPABILITIES = {
    "channels-case-rooms",
    "threads-discussions",
    "mentions-agent-bots",
    "reactions-pins-stars",
    "files-media",
    "search-history",
    "room-threads",
    "room-discussions",
    "room-mentions",
    "room-pinned-messages",
    "room-starred-messages",
    "room-files",
    "room-search",
}


@dataclass(frozen=True)
class _ActionSpec:
    required: tuple[str, ...] = ()
    mutating: bool = True
    admin: bool = False


_ACTION_SPECS: dict[str, _ActionSpec] = {
    "sync-thread": _ActionSpec(("threadId",), admin=True),
    "send-message": _ActionSpec(("threadId", "text")),
    "open-dm": _ActionSpec(("username",)),
    "send-direct-message": _ActionSpec(("username|roomId", "text")),
    "reply-thread": _ActionSpec(("roomId", "threadMessageId", "text")),
    "create-discussion": _ActionSpec(("roomId", "title")),
    "send-mention": _ActionSpec(("roomId", "username", "text")),
    "dispatch-agent-command": _ActionSpec(("roomId", "commandName", "confirmed"), admin=True),
    "react": _ActionSpec(("roomId", "messageId", "emoji")),
    "pin-message": _ActionSpec(("roomId", "messageId")),
    "unpin-message": _ActionSpec(("roomId", "messageId")),
    "star-message": _ActionSpec(("roomId", "messageId")),
    "unstar-message": _ActionSpec(("roomId", "messageId")),
    "prepare-upload": _ActionSpec(("roomId", "fileName", "contentType", "size"), mutating=False),
    "delete-file": _ActionSpec(("roomId", "fileId")),
    "search": _ActionSpec(("roomId", "searchText"), mutating=False),
    "export-room": _ActionSpec(("roomId",), mutating=False),
    "set-status": _ActionSpec(("status",)),
    "mark-room-read": _ActionSpec(("roomId",)),
    "assign-role": _ActionSpec(("username", "roleId"), admin=True),
    "remove-role": _ActionSpec(("username", "roleId"), admin=True),
    "invite-room-member": _ActionSpec(("roomId", "roomType", "userId"), admin=True),
    "kick-room-member": _ActionSpec(("roomId", "roomType", "userId"), admin=True),
    "sync-workspace": _ActionSpec(admin=True),
    "ingest-webhook": _ActionSpec(("event",), admin=True),
    "create-team": _ActionSpec(("name", "type"), admin=True),
    "add-team-member": _ActionSpec(("teamId", "userId"), admin=True),
    "remove-team-member": _ActionSpec(("teamId", "userId"), admin=True),
    "create-webhook": _ActionSpec(("type", "name"), admin=True),
    "update-webhook": _ActionSpec(("integrationId", "type", "name"), admin=True),
    "remove-webhook": _ActionSpec(("integrationId", "type"), admin=True),
    "validate-app-manifest": _ActionSpec(("manifest",), mutating=False, admin=True),
    "set-app-status": _ActionSpec(("appId", "status", "confirmed"), admin=True),
    "edit-message": _ActionSpec(("roomId", "messageId", "text")),
    "delete-message": _ActionSpec(("roomId", "messageId")),
    "create-user": _ActionSpec(("email", "name", "username", "password"), admin=True),
    "update-user": _ActionSpec(("userId", "data"), admin=True),
    "deactivate-user": _ActionSpec(("userId",), admin=True),
    "update-permissions": _ActionSpec(("permissions",), admin=True),
    "create-room": _ActionSpec(("name", "roomType"), admin=True),
    "archive-room": _ActionSpec(("roomId", "roomType"), admin=True),
    "delete-emoji": _ActionSpec(("emojiId",), admin=True),
    "prepare-emoji-upload": _ActionSpec(
        ("name", "aliases", "fileName"), mutating=False, admin=True
    ),
    "validate-oauth-provider": _ActionSpec(("provider",), mutating=False, admin=True),
    "create-oauth-app": _ActionSpec(("name", "redirectUri"), admin=True),
    "update-oauth-app": _ActionSpec(("applicationId", "name", "redirectUri"), admin=True),
    "delete-oauth-app": _ActionSpec(("applicationId",), admin=True),
    "generate-personal-access-token": _ActionSpec(("tokenName",), admin=True),
    "revoke-personal-access-token": _ActionSpec(("tokenName",), admin=True),
    "dry-run": _ActionSpec(mutating=False, admin=True),
    "save-local-policy": _ActionSpec(("confirmLocalOnly",), admin=True),
    "save-local-config": _ActionSpec(("confirmLocalOnly",), admin=True),
}

_CAPABILITY_ACTIONS: dict[str, tuple[str, ...]] = {
    "channels-case-rooms": ("sync-thread", "send-message"),
    "direct-messages": ("open-dm", "send-direct-message"),
    "threads-discussions": ("reply-thread", "create-discussion"),
    "mentions-agent-bots": ("send-mention", "dispatch-agent-command"),
    "reactions-pins-stars": (
        "react",
        "pin-message",
        "unpin-message",
        "star-message",
        "unstar-message",
    ),
    "files-media": ("prepare-upload", "delete-file"),
    "search-history": ("search", "export-room"),
    "presence-notifications": ("set-status", "mark-room-read"),
    "roles-permissions-admin": (
        "assign-role",
        "remove-role",
        "invite-room-member",
        "kick-room-member",
        "archive-room",
    ),
    "rest-webhooks-bridge": ("sync-workspace", "ingest-webhook"),
    "teams-membership": ("create-team", "add-team-member", "remove-team-member"),
    "native-integrations-webhook-history": (
        "create-webhook",
        "update-webhook",
        "remove-webhook",
    ),
    "e2ee": ("dry-run", "save-local-policy"),
    "apps-engine-custom-apps": ("validate-app-manifest", "set-app-status"),
    "message-lifecycle-export-emoji": (
        "edit-message",
        "delete-message",
        "export-room",
        "delete-emoji",
    ),
    "oauth-tokens": (
        "validate-oauth-provider",
        "create-oauth-app",
        "generate-personal-access-token",
        "revoke-personal-access-token",
    ),
    "room-threads": ("reply-thread",),
    "room-discussions": ("create-discussion",),
    "room-mentions": ("send-mention",),
    "room-pinned-messages": ("pin-message", "unpin-message"),
    "room-starred-messages": ("star-message", "unstar-message"),
    "room-files": ("prepare-upload", "delete-file"),
    "room-search": ("search", "export-room"),
    "admin-users": ("create-user", "update-user", "deactivate-user"),
    "admin-permissions": ("update-permissions", "assign-role", "remove-role"),
    "admin-rooms": ("create-room", "archive-room"),
    "emoji-administration": ("prepare-emoji-upload", "delete-emoji"),
    "oauth-providers": ("validate-oauth-provider",),
    "third-party-login": ("create-oauth-app", "update-oauth-app", "delete-oauth-app"),
    "personal-access-tokens": (
        "generate-personal-access-token",
        "revoke-personal-access-token",
    ),
    "webdav": ("dry-run", "save-local-config"),
    "enterprise-controls": ("dry-run", "save-local-policy"),
    "external-push-conference": ("dry-run", "save-local-config"),
}

if len(_CAPABILITY_ACTIONS) != 33 or any(not actions for actions in _CAPABILITY_ACTIONS.values()):
    raise RuntimeError("Rocket.Chat native capability contract must contain 33 actionable keys.")

ROCKETCHAT_NATIVE_CAPABILITY_KEYS = frozenset(_CAPABILITY_ACTIONS)

_WRITE_ROLES = {
    WorkspaceRole.workspace_owner,
    WorkspaceRole.workspace_admin,
    WorkspaceRole.legal_manager,
    WorkspaceRole.operator,
}
_ADMIN_ROLES = {WorkspaceRole.workspace_owner, WorkspaceRole.workspace_admin}
_IDEMPOTENCY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,119}$")
_COMMAND_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")

_ROOM_FIELDS = (
    "_id",
    "name",
    "fname",
    "t",
    "topic",
    "description",
    "usersCount",
    "teamId",
    "prid",
    "ts",
    "_updatedAt",
    "lm",
    "archived",
    "readOnly",
)
_USER_FIELDS = (
    "_id",
    "username",
    "name",
    "status",
    "statusText",
    "roles",
    "active",
    "type",
    "createdAt",
    "lastLogin",
)


def _bounded_json(value: Any, *, depth: int = 0) -> Any:
    """Create a bounded JSON value before applying credential redaction."""

    if depth > 8:
        return "[TRUNCATED]"
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return _bounded_json(model_dump(mode="json", by_alias=True), depth=depth + 1)
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 100:
                result["_truncated"] = True
                break
            result[str(key)[:160]] = _bounded_json(item, depth=depth + 1)
        return result
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_bounded_json(item, depth=depth + 1) for item in value[:200]]
    if isinstance(value, str):
        return value[:100_000]
    if isinstance(value, bytes | bytearray):
        return "[BINARY OMITTED]"
    if isinstance(value, UUID | datetime | date):
        return str(value)
    if isinstance(value, Enum):
        return _bounded_json(value.value, depth=depth + 1)
    if value is None or isinstance(value, bool | int | float):
        return value
    return str(value)[:500]


def _sanitize(value: Any) -> Any:
    return sanitize_oss_json(_bounded_json(value))


def _pick(row: Mapping[str, Any], *fields: str) -> dict[str, Any]:
    return {field: row[field] for field in fields if row.get(field) is not None}


def _message(row: Mapping[str, Any]) -> dict[str, Any]:
    projected = _pick(
        row,
        "_id",
        "rid",
        "msg",
        "ts",
        "_updatedAt",
        "tmid",
        "tcount",
        "tlm",
        "pinned",
        "starred",
        "editedAt",
    )
    user = row.get("u")
    projected["user"] = (
        _pick(user, "_id", "username", "name") if isinstance(user, Mapping) else None
    )
    reactions = row.get("reactions")
    projected["reactions"] = (
        [
            {
                "emoji": str(emoji)[:80],
                "count": len(details.get("usernames", []))
                if isinstance(details, Mapping) and isinstance(details.get("usernames"), list)
                else 0,
            }
            for emoji, details in list(reactions.items())[:50]
        ]
        if isinstance(reactions, Mapping)
        else []
    )
    attachments = row.get("attachments")
    projected["attachments"] = (
        [
            _pick(item, "title", "description", "type", "format", "size", "author_name")
            for item in attachments[:50]
            if isinstance(item, Mapping)
        ]
        if isinstance(attachments, list)
        else []
    )
    file_data = row.get("file")
    projected["file"] = (
        _pick(file_data, "_id", "name", "type", "format", "size")
        if isinstance(file_data, Mapping)
        else None
    )
    return projected


def _rows(payload: Mapping[str, Any], *keys: str) -> list[Mapping[str, Any]]:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, Mapping)]
    return []


class RocketChatNativeService:
    """Native, bounded adapter over one exclusively bound local Rocket.Chat server."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.client = RocketChatClient()
        self.links = OSSBridgeService(db)
        self.klm = KlmOperationsService(db)
        self.operations = RocketChatOperationsService(db, self.client)
        self.bridge = RocketChatBridgeService(db)
        self.bridge.client = self.client
        self.bridge.operations = self.klm

    async def snapshot(
        self,
        workspace_id: UUID,
        capability_key: str,
        query: dict[str, Any],
    ) -> dict[str, Any]:
        """Return a sanitized read model and explicit actions for one capability."""

        workspace_id = self._uuid(workspace_id, "workspace_id")
        self._capability(capability_key)
        if not isinstance(query, dict):
            raise TypeError("query deve ser um objeto.")

        if capability_key in _GATED_CAPABILITIES:
            content = await self._gated_snapshot(workspace_id, capability_key, query)
            account_id = None
        else:
            try:
                account_id = await self._bound_account(workspace_id)
                content = await self._snapshot_content(
                    workspace_id, account_id, capability_key, query
                )
            except RocketChatWorkspaceBindingError:
                raise
            except RocketChatClientError as error:
                account_id = None
                content = {
                    "status": "runtime-unavailable",
                    "error": str(error)[:500],
                    "items": [],
                }

        response = {
            "provider": "rocketchat",
            "workspaceId": workspace_id,
            "capabilityKey": capability_key,
            "accountId": account_id,
            "sourceOfTruth": "klm",
            "content": content or {"status": "empty", "items": []},
            "actions": self._actions(capability_key),
            "runtimeRisk": _RUNTIME_RISK,
            "sanitized": True,
        }
        return _sanitize(response)

    @classmethod
    async def validate_native_configuration(
        cls,
        workspace_id: UUID,
        actor_user_id: UUID,
        capability_key: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        workspace_uuid = cls._uuid(workspace_id, "workspace_id")
        cls._uuid(actor_user_id, "actor_user_id")
        cls._capability(capability_key)
        configuration = payload.get("configuration") if isinstance(payload, dict) else None
        if configuration is not None and not isinstance(configuration, dict):
            raise TypeError("configuration deve ser um objeto.")
        return _sanitize(
            {
                "provider": "rocketchat",
                "workspaceId": workspace_uuid,
                "capabilityKey": capability_key,
                "command": "__native_config__",
                "mode": "configure",
                "status": "validated",
                "configurationFieldCount": len(configuration or {}),
                "externalNetworkExecuted": False,
            }
        )

    async def validate(
        self,
        workspace_id: UUID,
        actor_user_id: UUID,
        capability_key: str,
        command: str,
        payload: dict[str, Any],
        *,
        mode: str,
    ) -> dict[str, Any]:
        """Validate a Rocket.Chat command and actor without contacting the server."""

        workspace_id = self._uuid(workspace_id, "workspace_id")
        actor_user_id = self._uuid(actor_user_id, "actor_user_id")
        self._capability(capability_key)
        if mode not in {"execute", "dry-run", "configure"}:
            raise ValueError("Modo de validação Rocket.Chat inválido.")
        if not isinstance(payload, dict):
            raise TypeError("payload deve ser um objeto.")
        if command == "__native_config__":
            return await self.validate_native_configuration(
                workspace_id, actor_user_id, capability_key, payload
            )
        if command not in _CAPABILITY_ACTIONS[capability_key]:
            raise ValueError("Comando não permitido para esta capacidade Rocket.Chat.")
        spec = _ACTION_SPECS[command]
        await self._authorize_actor(workspace_id, actor_user_id, spec)
        missing: list[str] = []
        for required in spec.required:
            alternatives = [name for name in required.split("|") if name]
            if not any(
                payload.get(name) is not None and payload.get(name) != ""
                for name in alternatives
            ):
                missing.append(" ou ".join(alternatives))
        if missing:
            raise ValueError("Campos obrigatórios ausentes: " + ", ".join(missing) + ".")
        return _sanitize(
            {
                "provider": "rocketchat",
                "workspaceId": workspace_id,
                "capabilityKey": capability_key,
                "command": command,
                "mode": mode,
                "status": "validated",
                "requiredFields": list(spec.required),
                "externalNetworkExecuted": False,
            }
        )

    async def execute(
        self,
        workspace_id: UUID,
        actor_user_id: UUID,
        capability_key: str,
        command: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute one allow-listed command with KLM authorization and replay fencing."""

        workspace_id = self._uuid(workspace_id, "workspace_id")
        actor_user_id = self._uuid(actor_user_id, "actor_user_id")
        self._capability(capability_key)
        if not isinstance(payload, dict):
            raise TypeError("payload deve ser um objeto.")
        if command not in _CAPABILITY_ACTIONS[capability_key]:
            raise ValueError("Comando nao permitido para esta capability Rocket.Chat.")
        spec = _ACTION_SPECS[command]
        await self._authorize_actor(workspace_id, actor_user_id, spec)

        account_id: str | None = None
        if capability_key not in _GATED_CAPABILITIES:
            account_id = await self._bound_account(workspace_id)

        receipt: IntegrationEventReceipt | None = None
        lease_started_at: datetime | None = None
        if spec.mutating:
            receipt, lease_started_at = await self._claim_command(
                workspace_id,
                actor_user_id,
                capability_key,
                command,
                payload,
            )
            if lease_started_at is None:
                return _sanitize(
                    {
                        "provider": "rocketchat",
                        "workspaceId": workspace_id,
                        "capabilityKey": capability_key,
                        "command": command,
                        "status": "replayed",
                        "result": {
                            "receiptId": receipt.id,
                            "receiptStatus": receipt.status,
                        },
                        "runtimeRisk": _RUNTIME_RISK,
                        "sanitized": True,
                    }
                )

        try:
            if capability_key in _GATED_CAPABILITIES:
                result = await self._execute_gated(
                    workspace_id,
                    actor_user_id,
                    capability_key,
                    command,
                    payload,
                )
            else:
                assert account_id is not None
                result = await self._execute_command(
                    workspace_id,
                    actor_user_id,
                    account_id,
                    capability_key,
                    command,
                    payload,
                )

            if spec.mutating:
                await self.klm.record_audit(
                    workspace_id=workspace_id,
                    actor_user_id=actor_user_id,
                    actor_type="human",
                    actor_label="Usuario KLM",
                    event_type=f"rocketchat.native.{command}",
                    entity_type="rocketchat_capability",
                    entity_id=None,
                    summary=f"Comando nativo Rocket.Chat executado: {command}.",
                    correlation_id=self._idempotency_key(payload),
                    after_data=_sanitize(
                        {
                            "capabilityKey": capability_key,
                            "command": command,
                            "result": result,
                        }
                    ),
                )
                assert receipt is not None and lease_started_at is not None
                await self.links.finish_receipt(
                    workspace_id,
                    OSSBridgeProvider.rocketchat,
                    receipt.id,
                    lease_started_at,
                    IntegrationEventReceiptStatus.processed,
                )
                await self.db.commit()

            status = "dry_run" if command == "dry-run" else "executed"
            return _sanitize(
                {
                    "provider": "rocketchat",
                    "workspaceId": workspace_id,
                    "capabilityKey": capability_key,
                    "command": command,
                    "status": status,
                    "result": result or {"success": True},
                    "runtimeRisk": _RUNTIME_RISK,
                    "sanitized": True,
                }
            )
        except Exception:
            await self.db.rollback()
            if receipt is not None and lease_started_at is not None:
                await self.links.finish_receipt(
                    workspace_id,
                    OSSBridgeProvider.rocketchat,
                    receipt.id,
                    lease_started_at,
                    IntegrationEventReceiptStatus.failed,
                )
                await self.db.commit()
            raise

    @staticmethod
    def _uuid(value: Any, field: str) -> UUID:
        if isinstance(value, UUID):
            return value
        try:
            return UUID(str(value))
        except (TypeError, ValueError) as error:
            raise ValueError(f"{field} invalido.") from error

    @staticmethod
    def _capability(capability_key: str) -> None:
        if capability_key not in _CAPABILITY_ACTIONS:
            raise LookupError("Capability Rocket.Chat nao classificada.")

    @staticmethod
    def _actions(capability_key: str) -> list[dict[str, Any]]:
        actions = []
        for command in _CAPABILITY_ACTIONS[capability_key]:
            spec = _ACTION_SPECS[command]
            actions.append(
                {
                    "command": command,
                    "required": list(spec.required),
                    "mutating": spec.mutating,
                    "authority": "workspace_admin" if spec.admin else "workspace_operator",
                    "idempotencyRequired": spec.mutating,
                    "networkAllowed": capability_key not in _GATED_CAPABILITIES,
                }
            )
        return actions

    async def _authorize_actor(
        self,
        workspace_id: UUID,
        actor_user_id: UUID,
        spec: _ActionSpec,
    ) -> WorkspaceMembership:
        membership = await self.db.scalar(
            select(WorkspaceMembership).where(
                WorkspaceMembership.workspace_id == workspace_id,
                WorkspaceMembership.user_id == actor_user_id,
                WorkspaceMembership.status == MembershipStatus.active,
            )
        )
        if membership is None:
            raise PermissionError("Ator nao pertence ao workspace informado.")
        grants = set(membership.permission_grants or [])
        denials = set(membership.permission_denials or [])
        if "*" in denials or "oss.rocketchat.execute" in denials:
            raise PermissionError("Comandos Rocket.Chat negados para este ator.")
        if spec.admin:
            if membership.role not in _ADMIN_ROLES and "oss.rocketchat.admin" not in grants:
                raise PermissionError("Comando Rocket.Chat exige administrador do workspace.")
        elif spec.mutating and membership.role not in _WRITE_ROLES:
            if "oss.rocketchat.execute" not in grants:
                raise PermissionError("Ator nao pode executar comandos Rocket.Chat.")
        return membership

    async def _bound_account(self, workspace_id: UUID) -> str:
        info = await self.client.info()
        account_id = _account_id(info)
        bindings = list(
            (
                await self.db.execute(
                    select(ExternalEntityLink).where(
                        ExternalEntityLink.provider == OSSBridgeProvider.rocketchat,
                        ExternalEntityLink.external_account_id == account_id,
                        ExternalEntityLink.klm_entity_type == "workspace",
                        ExternalEntityLink.external_entity_type == "account",
                        ExternalEntityLink.sync_state == ExternalEntitySyncState.synced,
                    )
                )
            ).scalars()
        )
        if any(binding.workspace_id != workspace_id for binding in bindings):
            raise RocketChatWorkspaceBindingError(
                "Este servidor Rocket.Chat local ja esta vinculado a outro workspace."
            )
        if not any(binding.workspace_id == workspace_id for binding in bindings):
            await self.bridge._bind_workspace(workspace_id, account_id)
            await self.db.flush()
        return account_id

    async def _linked_rooms(self, workspace_id: UUID, account_id: str) -> list[dict[str, Any]]:
        links = list(
            (
                await self.db.execute(
                    select(ExternalEntityLink).where(
                        ExternalEntityLink.workspace_id == workspace_id,
                        ExternalEntityLink.provider == OSSBridgeProvider.rocketchat,
                        ExternalEntityLink.external_account_id == account_id,
                        ExternalEntityLink.external_entity_type == "room",
                        ExternalEntityLink.sync_state == ExternalEntitySyncState.synced,
                    )
                )
            ).scalars()
        )
        return [
            {
                "roomId": link.external_entity_id,
                "threadId": link.klm_entity_id,
                "roomName": (link.link_metadata or {}).get("roomName"),
                "roomType": (link.link_metadata or {}).get("roomType"),
                "scope": (link.link_metadata or {}).get("scope"),
                "lastSyncedAt": link.last_synced_at,
            }
            for link in links[:200]
        ]

    async def _room_target(
        self,
        workspace_id: UUID,
        account_id: str,
        values: Mapping[str, Any],
        *,
        allow_server_scope: bool = False,
    ) -> dict[str, str] | None:
        thread_id = values.get("threadId")
        if thread_id:
            target = await self.operations.launch_target(
                workspace_id, self._uuid(thread_id, "threadId")
            )
            if target is None:
                raise LookupError("Thread KLM sem sala Rocket.Chat vinculada.")
            return target

        room_id = str(values.get("roomId") or "").strip()
        if not room_id:
            return None
        link = await self.db.scalar(
            select(ExternalEntityLink).where(
                ExternalEntityLink.workspace_id == workspace_id,
                ExternalEntityLink.provider == OSSBridgeProvider.rocketchat,
                ExternalEntityLink.external_account_id == account_id,
                ExternalEntityLink.external_entity_type == "room",
                ExternalEntityLink.external_entity_id == room_id,
                ExternalEntityLink.sync_state == ExternalEntitySyncState.synced,
            )
        )
        if link is None and not allow_server_scope:
            raise PermissionError("Sala Rocket.Chat nao vinculada a este workspace KLM.")
        metadata = link.link_metadata if link is not None else {}
        room_type = str(values.get("roomType") or metadata.get("roomType") or "p")
        if room_type not in {"c", "p", "d"}:
            raise ValueError("roomType deve ser c, p ou d.")
        return {
            "roomId": room_id,
            "roomName": str(metadata.get("roomName") or ""),
            "roomType": room_type,
        }

    async def _api(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        auth: bool = True,
    ) -> dict[str, Any]:
        return await self.client._request(method, path, params=params, json=body, auth=auth)

    @staticmethod
    def _limit(values: Mapping[str, Any]) -> int:
        try:
            return max(1, min(int(values.get("limit") or 50), 100))
        except (TypeError, ValueError) as error:
            raise ValueError("limit deve ser numerico.") from error

    async def _snapshot_content(
        self,
        workspace_id: UUID,
        account_id: str,
        capability_key: str,
        query: dict[str, Any],
    ) -> dict[str, Any]:
        limit = self._limit(query)
        linked_rooms = await self._linked_rooms(workspace_id, account_id)

        if capability_key == "channels-case-rooms":
            if query.get("threadId"):
                context = await self.operations.context(
                    workspace_id, self._uuid(query["threadId"], "threadId")
                )
                return {"kind": "case-room", "context": context}
            return {"kind": "linked-rooms", "items": linked_rooms, "count": len(linked_rooms)}

        if capability_key in _ROOM_CAPABILITIES:
            target = await self._room_target(workspace_id, account_id, query)
            if target is None:
                return {
                    "kind": "room-context-required",
                    "requires": ["threadId", "roomId"],
                    "linkedRooms": linked_rooms,
                }
            return await self._room_snapshot(capability_key, target, query, limit)

        if capability_key == "direct-messages":
            payload = await self._api("GET", "/api/v1/im.list", params={"count": limit})
            return {
                "kind": "direct-messages",
                "items": [_pick(row, *_ROOM_FIELDS) for row in _rows(payload, "ims")],
            }

        if capability_key == "presence-notifications":
            payload = await self._api("GET", "/api/v1/users.list", params={"count": limit})
            identity = await self.client.identity()
            return {
                "kind": "presence",
                "currentUser": _pick(identity, *_USER_FIELDS),
                "items": [_pick(row, *_USER_FIELDS) for row in _rows(payload, "users")],
                "externalPush": "gated",
            }

        if capability_key in {"roles-permissions-admin", "admin-permissions"}:
            roles = await self._api("GET", "/api/v1/roles.list")
            permissions = await self._api("GET", "/api/v1/permissions.listAll")
            content = {
                "kind": "rbac-drift",
                "authority": "klm",
                "roles": [
                    _pick(row, "_id", "name", "scope", "description", "mandatory")
                    for row in _rows(roles, "roles")
                ],
                "permissions": [
                    _pick(row, "_id", "roles", "level", "scope")
                    for row in _rows(permissions, "permissions", "update")
                ],
            }
            if capability_key == "roles-permissions-admin":
                users = await self._api("GET", "/api/v1/users.list", params={"count": limit})
                rooms = await self._api("GET", "/api/v1/rooms.adminRooms", params={"count": limit})
                content["users"] = [_pick(row, *_USER_FIELDS) for row in _rows(users, "users")]
                content["rooms"] = [_pick(row, *_ROOM_FIELDS) for row in _rows(rooms, "rooms")]
            return content

        if capability_key == "admin-users":
            payload = await self._api("GET", "/api/v1/users.list", params={"count": limit})
            return {
                "kind": "users",
                "authority": "klm",
                "items": [_pick(row, *_USER_FIELDS) for row in _rows(payload, "users")],
            }

        if capability_key == "admin-rooms":
            payload = await self._api("GET", "/api/v1/rooms.adminRooms", params={"count": limit})
            return {
                "kind": "admin-rooms",
                "linkedRooms": linked_rooms,
                "items": [_pick(row, *_ROOM_FIELDS) for row in _rows(payload, "rooms")],
            }

        if capability_key == "rest-webhooks-bridge":
            payload = await self._api("GET", "/api/v1/integrations.list", params={"count": limit})
            return {
                "kind": "bridge-health",
                "binding": {"accountId": account_id, "exclusive": True},
                "linkedRooms": linked_rooms,
                "integrations": self._integrations(payload),
            }

        if capability_key == "teams-membership":
            payload = await self._api("GET", "/api/v1/teams.listAll", params={"count": limit})
            return {
                "kind": "teams",
                "items": [
                    _pick(row, "_id", "name", "type", "roomId", "createdAt", "updatedAt")
                    for row in _rows(payload, "teams")
                ],
            }

        if capability_key == "native-integrations-webhook-history":
            payload = await self._api("GET", "/api/v1/integrations.list", params={"count": limit})
            result: dict[str, Any] = {
                "kind": "integrations",
                "items": self._integrations(payload),
            }
            integration_id = str(query.get("integrationId") or "").strip()
            if integration_id:
                history = await self._api(
                    "GET",
                    "/api/v1/integrations.history",
                    params={"id": integration_id, "count": limit},
                )
                result["history"] = [
                    _pick(row, "_id", "integrationId", "status", "error", "createdAt")
                    for row in _rows(history, "history")
                ]
            return result

        if capability_key == "apps-engine-custom-apps":
            payload = await self._api("GET", "/api/apps/installed", params={"count": limit})
            return {
                "kind": "installed-apps",
                "items": [
                    _pick(
                        row,
                        "id",
                        "_id",
                        "name",
                        "version",
                        "status",
                        "author",
                        "description",
                    )
                    for row in _rows(payload, "apps", "data")
                ],
            }

        if capability_key in {"message-lifecycle-export-emoji", "emoji-administration"}:
            payload = await self._api("GET", "/api/v1/emoji-custom.list")
            return {
                "kind": "emoji-and-lifecycle",
                "emoji": [
                    _pick(row, "_id", "name", "aliases", "extension", "updatedAt")
                    for row in _rows(payload, "emojis", "emoji")
                ],
                "linkedRooms": linked_rooms,
            }

        if capability_key in {"oauth-tokens", "third-party-login"}:
            apps = await self._api("GET", "/api/v1/oauth-apps.list")
            content = {
                "kind": "oauth-apps",
                "items": [
                    _pick(row, "_id", "name", "active", "redirectUri", "createdAt", "updatedAt")
                    for row in _rows(apps, "oauthApps", "apps")
                ],
            }
            if capability_key == "oauth-tokens":
                tokens = await self._api("GET", "/api/v1/users.getPersonalAccessTokens")
                content["personalTokens"] = self._personal_tokens(tokens)
            return content

        if capability_key == "personal-access-tokens":
            payload = await self._api("GET", "/api/v1/users.getPersonalAccessTokens")
            return {"kind": "personal-access-tokens", "items": self._personal_tokens(payload)}

        if capability_key == "oauth-providers":
            payload = await self._api("GET", "/api/v1/settings.public", auth=False)
            settings_rows = _rows(payload, "settings")
            return {
                "kind": "oauth-providers",
                "items": [
                    {
                        **_pick(row, "_id", "group", "type", "public", "enterprise", "blocked"),
                        "configured": row.get("value") not in {None, "", False},
                    }
                    for row in settings_rows
                    if "oauth" in str(row.get("_id") or row.get("group") or "").lower()
                ],
                "secretsIncluded": False,
            }

        raise RuntimeError(f"Snapshot Rocket.Chat sem handler: {capability_key}")

    async def _room_snapshot(
        self,
        capability_key: str,
        target: dict[str, str],
        query: dict[str, Any],
        limit: int,
    ) -> dict[str, Any]:
        room_id = target["roomId"]
        room_type = target["roomType"]
        base = {"room": target, "kind": capability_key}

        if capability_key in {"threads-discussions", "room-threads"}:
            threads = await self._api(
                "GET", "/api/v1/chat.getThreadsList", params={"rid": room_id, "count": limit}
            )
            base["threads"] = [_message(row) for row in _rows(threads, "threads")]
            if capability_key == "threads-discussions":
                base["discussions"] = [
                    _pick(row, *_ROOM_FIELDS)
                    for row in await self.client.discussions(room_id, limit)
                ]
            return base

        if capability_key == "room-discussions":
            base["items"] = [
                _pick(row, *_ROOM_FIELDS) for row in await self.client.discussions(room_id, limit)
            ]
            return base

        if capability_key in {"mentions-agent-bots", "room-mentions"}:
            term = str(query.get("searchText") or "@").strip()[:500]
            if term != "@":
                payload = await self._api(
                    "GET",
                    "/api/v1/chat.search",
                    params={"roomId": room_id, "searchText": term, "count": limit},
                )
                messages = _rows(payload, "messages")
            else:
                messages = [
                    row
                    for row in await self.client.room_history(room_id, room_type, limit)
                    if "@" in str(row.get("msg") or "")
                ]
            base["items"] = [_message(row) for row in messages]
            base["agentDispatch"] = "requires-explicit-confirmation"
            return base

        if capability_key in {"reactions-pins-stars", "room-pinned-messages"}:
            pinned = await self._api(
                "GET",
                "/api/v1/chat.getPinnedMessages",
                params={"roomId": room_id, "count": limit},
            )
            base["pinned"] = [_message(row) for row in _rows(pinned, "messages")]
            if capability_key == "room-pinned-messages":
                return base

        if capability_key in {"reactions-pins-stars", "room-starred-messages"}:
            starred = await self._api(
                "GET",
                "/api/v1/chat.getStarredMessages",
                params={"roomId": room_id, "count": limit},
            )
            base["starred"] = [_message(row) for row in _rows(starred, "messages")]
            if capability_key == "room-starred-messages":
                return base

        if capability_key == "reactions-pins-stars":
            history = await self.client.room_history(room_id, room_type, limit)
            base["reactions"] = [
                _message(row) for row in history if isinstance(row.get("reactions"), Mapping)
            ]
            return base

        if capability_key in {"files-media", "room-files"}:
            endpoint = {"c": "channels.files", "p": "groups.files", "d": "im.files"}[room_type]
            payload = await self._api(
                "GET", f"/api/v1/{endpoint}", params={"roomId": room_id, "count": limit}
            )
            base["items"] = [
                _pick(
                    row,
                    "_id",
                    "name",
                    "type",
                    "size",
                    "extension",
                    "identify",
                    "uploadedAt",
                    "lastModified",
                )
                for row in _rows(payload, "files")
            ]
            base["uploadMode"] = "prepare-only-until-client-supports-multipart"
            return base

        if capability_key in {"search-history", "room-search"}:
            term = str(query.get("searchText") or "").strip()[:500]
            if term:
                payload = await self._api(
                    "GET",
                    "/api/v1/chat.search",
                    params={"roomId": room_id, "searchText": term, "count": limit},
                )
                messages = _rows(payload, "messages")
            else:
                messages = await self.client.room_history(room_id, room_type, limit)
            base["items"] = [_message(row) for row in messages]
            base["searchText"] = term
            return base

        raise RuntimeError(f"Room snapshot Rocket.Chat sem handler: {capability_key}")

    @staticmethod
    def _integrations(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
        return [
            _pick(
                row,
                "_id",
                "name",
                "type",
                "enabled",
                "channel",
                "username",
                "event",
                "scriptEnabled",
                "createdAt",
                "updatedAt",
            )
            for row in _rows(payload, "integrations")
        ]

    @staticmethod
    def _personal_tokens(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
        return [
            _pick(row, "name", "createdAt", "lastTokenPart", "lastUsedAt")
            for row in _rows(payload, "tokens", "personalAccessTokens")
        ]

    async def _claim_command(
        self,
        workspace_id: UUID,
        actor_user_id: UUID,
        capability_key: str,
        command: str,
        payload: dict[str, Any],
    ) -> tuple[IntegrationEventReceipt, datetime | None]:
        idempotency_key = self._idempotency_key(payload)
        canonical_payload = {
            "actorUserId": str(actor_user_id),
            "capabilityKey": capability_key,
            "command": command,
            # The command receipt is durable. Only the redacted projection may
            # cross this boundary; transient credentials remain in memory.
            "payload": sanitize_oss_json(payload),
        }
        receipt, _ = await self.links.register_receipt(
            workspace_id,
            IntegrationEventReceiptCreate(
                provider=OSSBridgeProvider.rocketchat,
                external_event_id=(f"native:{capability_key}:{idempotency_key}")[:255],
                payload=canonical_payload,
            ),
        )
        lease_started_at = await self.links.claim_receipt(
            workspace_id,
            OSSBridgeProvider.rocketchat,
            receipt.id,
        )
        return receipt, lease_started_at

    @staticmethod
    def _idempotency_key(payload: Mapping[str, Any]) -> str:
        value = str(payload.get("idempotencyKey") or "").strip()
        if not _IDEMPOTENCY_PATTERN.fullmatch(value):
            raise ValueError(
                "idempotencyKey deve ter 3-120 caracteres seguros para comandos mutantes."
            )
        return value

    @staticmethod
    def _text(
        values: Mapping[str, Any],
        key: str,
        *,
        maximum: int = 100_000,
    ) -> str:
        value = str(values.get(key) or "").strip()
        if not value:
            raise ValueError(f"{key} e obrigatorio.")
        if len(value) > maximum:
            raise ValueError(f"{key} excede o limite de {maximum} caracteres.")
        return value

    @staticmethod
    def _string_list(values: Mapping[str, Any], key: str, *, maximum: int = 100) -> list[str]:
        raw = values.get(key)
        if raw is None:
            return []
        if not isinstance(raw, list) or len(raw) > maximum:
            raise ValueError(f"{key} deve ser uma lista limitada.")
        return [str(item).strip()[:500] for item in raw if str(item).strip()]

    async def _execute_command(
        self,
        workspace_id: UUID,
        actor_user_id: UUID,
        account_id: str,
        capability_key: str,
        command: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if command == "sync-thread":
            return await self.bridge.sync_thread(
                workspace_id, self._uuid(payload.get("threadId"), "threadId")
            )

        if command == "send-message":
            thread_id = self._uuid(payload.get("threadId"), "threadId")
            message, replayed = await self.klm.create_message(
                workspace_id,
                thread_id,
                actor_user_id,
                OmnichannelMessageCreate(
                    channel=OmnichannelChannel.internal,
                    direction=MessageDirection.internal,
                    author_type=MessageAuthorType.human,
                    author_label="Usuario KLM",
                    body=self._text(payload, "text"),
                    idempotency_key=(f"rocket-native:{self._idempotency_key(payload)}")[:160],
                    message_metadata={"source": "rocketchat_native_adapter"},
                ),
            )
            projection = await self.bridge.sync_thread(workspace_id, thread_id)
            return {
                "klmMessageId": message.id,
                "messageReplayed": replayed,
                "projection": projection,
            }

        if command == "open-dm":
            result = await self._api(
                "POST",
                "/api/v1/im.create",
                body={"username": self._text(payload, "username", maximum=80)},
            )
            return self._command_result(result)

        if command == "send-direct-message":
            room_id = str(payload.get("roomId") or "").strip()
            if not room_id:
                dm = await self._api(
                    "POST",
                    "/api/v1/im.create",
                    body={"username": self._text(payload, "username", maximum=80)},
                )
                room = dm.get("room") if isinstance(dm.get("room"), Mapping) else dm.get("channel")
                room_id = str(room.get("_id") if isinstance(room, Mapping) else "").strip()
            if not room_id:
                raise RocketChatClientError("Rocket.Chat nao retornou a DM criada.")
            result = await self._api(
                "POST",
                "/api/v1/chat.sendMessage",
                body={
                    "message": {
                        "_id": self._message_id(command, payload),
                        "rid": room_id,
                        "msg": self._text(payload, "text"),
                    }
                },
            )
            return self._command_result(result)

        if command in {
            "reply-thread",
            "create-discussion",
            "send-mention",
            "dispatch-agent-command",
            "react",
            "pin-message",
            "unpin-message",
            "star-message",
            "unstar-message",
            "prepare-upload",
            "delete-file",
            "search",
            "export-room",
            "mark-room-read",
            "edit-message",
            "delete-message",
        }:
            target = await self._room_target(workspace_id, account_id, payload)
            if target is None:
                raise ValueError("roomId ou threadId e obrigatorio.")
            return await self._execute_room_command(target, command, payload)

        if command == "set-status":
            status = self._text(payload, "status", maximum=32)
            if status not in {"online", "away", "busy", "offline"}:
                raise ValueError("status Rocket.Chat invalido.")
            return self._command_result(
                await self._api(
                    "POST",
                    "/api/v1/users.setStatus",
                    body={"status": status, "message": str(payload.get("message") or "")[:280]},
                )
            )

        if command in {"assign-role", "remove-role"}:
            endpoint = (
                "roles.addUserToRole" if command == "assign-role" else "roles.removeUserFromRole"
            )
            body = {
                "username": self._text(payload, "username", maximum=80),
                "roleId": self._text(payload, "roleId", maximum=100),
            }
            if payload.get("roomId"):
                await self._room_target(workspace_id, account_id, payload, allow_server_scope=True)
                body["roomId"] = self._text(payload, "roomId", maximum=255)
            return self._command_result(await self._api("POST", f"/api/v1/{endpoint}", body=body))

        if command in {"invite-room-member", "kick-room-member", "archive-room"}:
            target = await self._room_target(
                workspace_id, account_id, payload, allow_server_scope=True
            )
            if target is None or target["roomType"] == "d":
                raise ValueError("Comando administrativo exige canal ou grupo.")
            prefix = "channels" if target["roomType"] == "c" else "groups"
            if command == "archive-room":
                endpoint = f"{prefix}.archive"
                body = {"roomId": target["roomId"]}
            else:
                endpoint = (
                    f"{prefix}.invite" if command == "invite-room-member" else f"{prefix}.kick"
                )
                body = {
                    "roomId": target["roomId"],
                    "userId": self._text(payload, "userId", maximum=255),
                }
            return self._command_result(await self._api("POST", f"/api/v1/{endpoint}", body=body))

        if command == "sync-workspace":
            return await self.bridge.sync_workspace(workspace_id)

        if command == "ingest-webhook":
            event = payload.get("event")
            if not isinstance(event, dict):
                raise ValueError("event deve ser um objeto Rocket.Chat.")
            return await self.bridge.ingest_webhook(workspace_id=workspace_id, payload=dict(event))

        if command in {"create-team", "add-team-member", "remove-team-member"}:
            return await self._execute_team_command(command, payload)

        if command in {"create-webhook", "update-webhook", "remove-webhook"}:
            return await self._execute_integration_command(command, payload)

        if command == "validate-app-manifest":
            return self._validate_app_manifest(payload)

        if command == "set-app-status":
            if payload.get("confirmed") is not True:
                raise PermissionError("Alteracao de app exige confirmed=true.")
            status = self._text(payload, "status", maximum=32)
            if status not in {"auto_enabled", "disabled", "manually_enabled"}:
                raise ValueError("Status de app nao permitido.")
            result = await self._api(
                "PUT",
                f"/api/apps/{self._text(payload, 'appId', maximum=255)}/status",
                body={"status": status},
            )
            return self._command_result(result)

        if command in {"create-user", "update-user", "deactivate-user"}:
            return await self._execute_user_command(command, payload)

        if command == "update-permissions":
            permissions = payload.get("permissions")
            if not isinstance(permissions, list) or not permissions or len(permissions) > 100:
                raise ValueError("permissions deve ser uma lista nao vazia e limitada.")
            normalized = []
            for row in permissions:
                if not isinstance(row, Mapping):
                    raise ValueError("Cada permissao deve ser um objeto.")
                normalized.append(
                    {
                        "_id": self._text(row, "_id", maximum=160),
                        "roles": self._string_list(row, "roles", maximum=50),
                    }
                )
            return self._command_result(
                await self._api(
                    "POST", "/api/v1/permissions.update", body={"permissions": normalized}
                )
            )

        if command == "create-room":
            room_type = self._text(payload, "roomType", maximum=1)
            if room_type not in {"c", "p"}:
                raise ValueError("roomType administrativo deve ser c ou p.")
            endpoint = "channels.create" if room_type == "c" else "groups.create"
            return self._command_result(
                await self._api(
                    "POST",
                    f"/api/v1/{endpoint}",
                    body={
                        "name": self._text(payload, "name", maximum=120),
                        "members": self._string_list(payload, "members"),
                        "readOnly": bool(payload.get("readOnly", False)),
                    },
                )
            )

        if command in {"delete-emoji", "prepare-emoji-upload"}:
            if command == "prepare-emoji-upload":
                return {
                    "prepared": True,
                    "networkExecuted": False,
                    "name": self._text(payload, "name", maximum=80),
                    "aliases": self._string_list(payload, "aliases", maximum=20),
                    "fileName": self._text(payload, "fileName", maximum=255),
                    "reason": "RocketChatClient atual nao possui transporte multipart.",
                }
            return self._command_result(
                await self._api(
                    "POST",
                    "/api/v1/emoji-custom.delete",
                    body={"emojiId": self._text(payload, "emojiId", maximum=255)},
                )
            )

        if command == "validate-oauth-provider":
            provider = self._text(payload, "provider", maximum=80)
            return {
                "valid": bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{1,79}", provider)),
                "provider": provider,
                "networkExecuted": False,
                "secretsAccepted": False,
            }

        if command in {"create-oauth-app", "update-oauth-app", "delete-oauth-app"}:
            return await self._execute_oauth_app_command(command, payload)

        if command in {"generate-personal-access-token", "revoke-personal-access-token"}:
            endpoint = (
                "users.generatePersonalAccessToken"
                if command == "generate-personal-access-token"
                else "users.removePersonalAccessToken"
            )
            result = await self._api(
                "POST",
                f"/api/v1/{endpoint}",
                body={"tokenName": self._text(payload, "tokenName", maximum=80)},
            )
            return self._command_result(result)

        raise RuntimeError(f"Comando Rocket.Chat sem handler: {capability_key}.{command}")

    async def _execute_room_command(
        self,
        target: dict[str, str],
        command: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        room_id = target["roomId"]
        if command == "reply-thread":
            result = await self._api(
                "POST",
                "/api/v1/chat.sendMessage",
                body={
                    "message": {
                        "_id": self._message_id(command, payload),
                        "rid": room_id,
                        "tmid": self._text(payload, "threadMessageId", maximum=255),
                        "msg": self._text(payload, "text"),
                    }
                },
            )
            return self._command_result(result)
        if command == "create-discussion":
            body: dict[str, Any] = {
                "prid": room_id,
                "t_name": self._text(payload, "title", maximum=160),
            }
            if payload.get("parentMessageId"):
                body["pmid"] = self._text(payload, "parentMessageId", maximum=255)
            if payload.get("text"):
                body["reply"] = self._text(payload, "text")
            return self._command_result(
                await self._api("POST", "/api/v1/rooms.createDiscussion", body=body)
            )
        if command == "send-mention":
            username = self._text(payload, "username", maximum=80).lstrip("@")
            text = self._text(payload, "text")
            result = await self._api(
                "POST",
                "/api/v1/chat.sendMessage",
                body={
                    "message": {
                        "_id": self._message_id(command, payload),
                        "rid": room_id,
                        "msg": text if f"@{username}" in text else f"@{username} {text}",
                    }
                },
            )
            return self._command_result(result)
        if command == "dispatch-agent-command":
            if payload.get("confirmed") is not True:
                raise PermissionError("Dispatch de agente exige confirmed=true.")
            name = self._text(payload, "commandName", maximum=64).lstrip("/")
            if not _COMMAND_PATTERN.fullmatch(name):
                raise ValueError("commandName invalido.")
            arguments = str(payload.get("arguments") or "").strip()[:2_000]
            result = await self._api(
                "POST",
                "/api/v1/chat.sendMessage",
                body={
                    "message": {
                        "_id": self._message_id(command, payload),
                        "rid": room_id,
                        "msg": f"/{name}{' ' + arguments if arguments else ''}",
                    }
                },
            )
            return self._command_result(result)
        if command in {
            "react",
            "pin-message",
            "unpin-message",
            "star-message",
            "unstar-message",
        }:
            endpoint = {
                "react": "chat.react",
                "pin-message": "chat.pinMessage",
                "unpin-message": "chat.unPinMessage",
                "star-message": "chat.starMessage",
                "unstar-message": "chat.unStarMessage",
            }[command]
            body = {"messageId": self._text(payload, "messageId", maximum=255)}
            if command == "react":
                body["emoji"] = self._text(payload, "emoji", maximum=80)
                body["shouldReact"] = bool(payload.get("shouldReact", True))
            return self._command_result(await self._api("POST", f"/api/v1/{endpoint}", body=body))
        if command == "prepare-upload":
            try:
                size = int(payload.get("size"))
            except (TypeError, ValueError) as error:
                raise ValueError("size deve ser numerico.") from error
            if size < 0 or size > 100 * 1024 * 1024:
                raise ValueError("Arquivo fora do limite local de 100 MiB.")
            return {
                "prepared": True,
                "networkExecuted": False,
                "roomId": room_id,
                "fileName": self._text(payload, "fileName", maximum=255),
                "contentType": self._text(payload, "contentType", maximum=160),
                "size": size,
                "reason": "RocketChatClient atual nao possui transporte multipart.",
            }
        if command == "delete-file":
            return self._command_result(
                await self._api(
                    "POST",
                    "/api/v1/chat.delete",
                    body={"fileId": self._text(payload, "fileId", maximum=255)},
                )
            )
        if command == "search":
            result = await self._api(
                "GET",
                "/api/v1/chat.search",
                params={
                    "roomId": room_id,
                    "searchText": self._text(payload, "searchText", maximum=500),
                    "count": self._limit(payload),
                },
            )
            return {"messages": [_message(row) for row in _rows(result, "messages")]}
        if command == "export-room":
            messages = await self.client.room_history(
                room_id, target["roomType"], self._limit(payload)
            )
            return {
                "format": "klm-sanitized-json",
                "networkExecuted": False,
                "messages": [_message(row) for row in messages],
            }
        if command == "mark-room-read":
            return self._command_result(
                await self._api("POST", "/api/v1/subscriptions.read", body={"rid": room_id})
            )
        if command == "edit-message":
            return self._command_result(
                await self._api(
                    "POST",
                    "/api/v1/chat.update",
                    body={
                        "roomId": room_id,
                        "msgId": self._text(payload, "messageId", maximum=255),
                        "text": self._text(payload, "text"),
                    },
                )
            )
        if command == "delete-message":
            return self._command_result(
                await self._api(
                    "POST",
                    "/api/v1/chat.delete",
                    body={
                        "roomId": room_id,
                        "msgId": self._text(payload, "messageId", maximum=255),
                    },
                )
            )
        raise RuntimeError(f"Comando de sala sem handler: {command}")

    async def _execute_team_command(self, command: str, payload: dict[str, Any]) -> dict[str, Any]:
        if command == "create-team":
            try:
                team_type = int(payload.get("type"))
            except (TypeError, ValueError) as error:
                raise ValueError("type de time deve ser 0 ou 1.") from error
            if team_type not in {0, 1}:
                raise ValueError("type de time deve ser 0 ou 1.")
            body: dict[str, Any] = {
                "name": self._text(payload, "name", maximum=120),
                "type": team_type,
                "members": self._string_list(payload, "members"),
            }
            return self._command_result(await self._api("POST", "/api/v1/teams.create", body=body))
        endpoint = "teams.addMembers" if command == "add-team-member" else "teams.removeMember"
        body = {
            "teamId": self._text(payload, "teamId", maximum=255),
            ("members" if command == "add-team-member" else "userId"): (
                [self._text(payload, "userId", maximum=255)]
                if command == "add-team-member"
                else self._text(payload, "userId", maximum=255)
            ),
        }
        return self._command_result(await self._api("POST", f"/api/v1/{endpoint}", body=body))

    async def _execute_integration_command(
        self, command: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        integration_type = self._text(payload, "type", maximum=40)
        if integration_type not in {"webhook-incoming", "webhook-outgoing"}:
            raise ValueError("Tipo de webhook nao permitido.")
        if (
            integration_type == "webhook-outgoing"
            and payload.get("confirmExternalEffect") is not True
        ):
            raise PermissionError("Webhook de saida exige confirmExternalEffect=true.")
        if command == "remove-webhook":
            body = {
                "type": integration_type,
                "integrationId": self._text(payload, "integrationId", maximum=255),
            }
            endpoint = "integrations.remove"
        else:
            body = self._integration_body(payload)
            endpoint = (
                "integrations.create" if command == "create-webhook" else "integrations.update"
            )
            if command == "update-webhook":
                body["_id"] = self._text(payload, "integrationId", maximum=255)
        return self._command_result(
            await self._api(
                "POST" if command != "update-webhook" else "PUT",
                f"/api/v1/{endpoint}",
                body=body,
            )
        )

    def _integration_body(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        body: dict[str, Any] = {
            "type": self._text(payload, "type", maximum=40),
            "name": self._text(payload, "name", maximum=160),
            "enabled": bool(payload.get("enabled", True)),
        }
        for key, maximum in (
            ("channel", 160),
            ("username", 80),
            ("event", 120),
            ("alias", 120),
            ("emoji", 80),
            ("script", 20_000),
        ):
            if payload.get(key) is not None:
                body[key] = str(payload[key])[:maximum]
        urls = self._string_list(payload, "urls", maximum=20)
        if urls:
            body["urls"] = urls
        body["scriptEnabled"] = bool(payload.get("scriptEnabled", False))
        return body

    async def _execute_user_command(self, command: str, payload: dict[str, Any]) -> dict[str, Any]:
        if command == "create-user":
            body = {
                "email": self._text(payload, "email", maximum=320),
                "name": self._text(payload, "name", maximum=160),
                "username": self._text(payload, "username", maximum=80),
                "password": self._text(payload, "password", maximum=500),
                "active": bool(payload.get("active", True)),
                "joinDefaultChannels": bool(payload.get("joinDefaultChannels", False)),
                "roles": self._string_list(payload, "roles", maximum=20),
            }
            endpoint = "users.create"
        elif command == "update-user":
            data = payload.get("data")
            if not isinstance(data, Mapping):
                raise ValueError("data deve ser um objeto de usuario.")
            allowed = {
                key: data[key]
                for key in ("email", "name", "username", "active", "roles")
                if key in data
            }
            if not allowed:
                raise ValueError("Nenhum campo de usuario permitido foi informado.")
            body = {
                "userId": self._text(payload, "userId", maximum=255),
                "data": allowed,
            }
            endpoint = "users.update"
        else:
            body = {
                "userId": self._text(payload, "userId", maximum=255),
                "activeStatus": False,
            }
            endpoint = "users.setActiveStatus"
        return self._command_result(await self._api("POST", f"/api/v1/{endpoint}", body=body))

    async def _execute_oauth_app_command(
        self, command: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        if command == "delete-oauth-app":
            endpoint = "oauth-apps.delete"
            body = {"applicationId": self._text(payload, "applicationId", maximum=255)}
        else:
            endpoint = "oauth-apps.create" if command == "create-oauth-app" else "oauth-apps.update"
            body = {
                "name": self._text(payload, "name", maximum=160),
                "redirectUri": self._text(payload, "redirectUri", maximum=2_000),
                "active": bool(payload.get("active", True)),
            }
            if command == "update-oauth-app":
                body["applicationId"] = self._text(payload, "applicationId", maximum=255)
        return self._command_result(await self._api("POST", f"/api/v1/{endpoint}", body=body))

    @staticmethod
    def _validate_app_manifest(payload: Mapping[str, Any]) -> dict[str, Any]:
        manifest = payload.get("manifest")
        if not isinstance(manifest, Mapping):
            raise ValueError("manifest deve ser um objeto.")
        required = ("id", "name", "version")
        missing = [key for key in required if not str(manifest.get(key) or "").strip()]
        permissions = manifest.get("permissions")
        permission_count = len(permissions) if isinstance(permissions, list) else 0
        return {
            "valid": not missing,
            "missing": missing,
            "manifest": _pick(manifest, "id", "name", "version", "description", "author"),
            "permissionCount": permission_count,
            "networkExecuted": False,
            "installationExecuted": False,
        }

    @staticmethod
    def _command_result(payload: Mapping[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {"success": payload.get("success", True)}
        projectors = {
            "message": _message,
            "user": lambda row: _pick(row, *_USER_FIELDS),
            "room": lambda row: _pick(row, *_ROOM_FIELDS),
            "channel": lambda row: _pick(row, *_ROOM_FIELDS),
            "group": lambda row: _pick(row, *_ROOM_FIELDS),
            "discussion": lambda row: _pick(row, *_ROOM_FIELDS),
            "team": lambda row: _pick(row, "_id", "name", "type", "roomId"),
            "role": lambda row: _pick(row, "_id", "name", "scope", "description"),
            "integration": lambda row: _pick(
                row, "_id", "name", "type", "enabled", "channel", "event"
            ),
            "oauthApp": lambda row: _pick(row, "_id", "name", "active", "redirectUri", "createdAt"),
        }
        for key, projector in projectors.items():
            row = payload.get(key)
            if isinstance(row, Mapping):
                result[key] = projector(row)
        for key in ("token", "authToken", "data"):
            if key in payload:
                result[key] = payload[key]
        if len(result) == 1:
            result["acknowledged"] = True
        return _sanitize(result)

    @staticmethod
    def _message_id(command: str, payload: Mapping[str, Any]) -> str:
        seed = f"{command}:{RocketChatNativeService._idempotency_key(payload)}"
        return f"klm_native_{hashlib.sha256(seed.encode()).hexdigest()[:32]}"

    async def _gated_snapshot(
        self,
        workspace_id: UUID,
        capability_key: str,
        query: dict[str, Any],
    ) -> dict[str, Any]:
        state = await self._native_state_call(
            "snapshot",
            workspace_id=workspace_id,
            actor_user_id=None,
            capability_key=capability_key,
            command=None,
            data=query,
        )
        return {
            "kind": "klm-local-gate",
            "gate": capability_key,
            "networkExecuted": False,
            "upstreamExecutionAllowed": False,
            "state": state if state is not None else {"configured": False},
            "mode": (
                "local-policy-equivalent"
                if capability_key in {"e2ee", "enterprise-controls"}
                else "local-config-or-dry-run"
            ),
        }

    async def _execute_gated(
        self,
        workspace_id: UUID,
        actor_user_id: UUID,
        capability_key: str,
        command: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if command == "dry-run":
            return {
                "gate": capability_key,
                "networkExecuted": False,
                "wouldExecuteUpstream": False,
                "plan": _sanitize(payload.get("plan") or {"status": "no-upstream-operation"}),
            }
        if payload.get("confirmLocalOnly") is not True:
            raise PermissionError("Configuracao gated exige confirmLocalOnly=true.")
        local_payload = {
            key: value
            for key, value in payload.items()
            if key not in {"idempotencyKey", "confirmLocalOnly"}
        }
        state = await self._native_state_call(
            "execute",
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            capability_key=capability_key,
            command=command,
            data=local_payload,
        )
        return {
            "gate": capability_key,
            "networkExecuted": False,
            "upstreamExecutionAllowed": False,
            "storedLocally": state is not None,
            "state": state if state is not None else {"serviceAvailable": False},
        }

    async def _native_state_call(
        self,
        method_name: str,
        *,
        workspace_id: UUID,
        actor_user_id: UUID | None,
        capability_key: str,
        command: str | None,
        data: dict[str, Any],
    ) -> Any:
        """Late-bind the optional local state service without importing enterprise code."""

        module_name = "app.services.oss_native_state_service"
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError as error:
            if error.name != module_name:
                raise
            return None
        service_type = next(
            (
                getattr(module, name)
                for name in (
                    "OSSNativeStateService",
                    "OssNativeStateService",
                    "NativeStateService",
                )
                if hasattr(module, name)
            ),
            None,
        )
        if service_type is None:
            return None
        service = service_type(self.db)
        method = getattr(service, method_name, None)
        if method is None and method_name == "snapshot":
            method = getattr(service, "get_snapshot", None)
        if method is None and method_name == "execute":
            method = getattr(service, "update_config", None)
        if method is None:
            return None

        if method_name == "snapshot" and getattr(method, "__name__", "") == "get_snapshot":
            result = method(workspace_id, "rocketchat", capability_key)
            return await result if inspect.isawaitable(result) else result

        if method_name == "execute" and getattr(method, "__name__", "") == "update_config":
            schemas = importlib.import_module("app.schemas.oss_native")
            models = importlib.import_module("app.models.oss_native")
            expected_version = int(data.get("expectedVersion", data.get("expected_version", 0)))
            configuration = {
                key: value
                for key, value in data.items()
                if key not in {"expectedVersion", "expected_version"}
            }
            execution_mode = {
                "e2ee": models.OSSNativeExecutionMode.local_native,
                "enterprise-controls": models.OSSNativeExecutionMode.enterprise_equivalent,
            }.get(capability_key, models.OSSNativeExecutionMode.external_equivalent)
            patch = schemas.OSSNativeConfigPatch(
                expected_version=expected_version,
                configuration=configuration,
                execution_mode=execution_mode,
            )
            result = method(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                provider="rocketchat",
                capability=capability_key,
                data=patch,
            )
            return await result if inspect.isawaitable(result) else result

        available = {
            "workspace_id": workspace_id,
            "workspaceId": workspace_id,
            "actor_user_id": actor_user_id,
            "actorUserId": actor_user_id,
            "provider": "rocketchat",
            "provider_id": "rocketchat",
            "capability_key": capability_key,
            "capabilityKey": capability_key,
            "command": command,
            "query": data,
            "payload": data,
            "data": data,
        }
        signature = inspect.signature(method)
        args: list[Any] = []
        kwargs: dict[str, Any] = {}
        for parameter in signature.parameters.values():
            if parameter.kind in {
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            }:
                continue
            if parameter.name not in available:
                if parameter.default is inspect.Parameter.empty:
                    return None
                continue
            if parameter.kind == inspect.Parameter.POSITIONAL_ONLY:
                args.append(available[parameter.name])
            else:
                kwargs[parameter.name] = available[parameter.name]
        result = method(*args, **kwargs)
        return await result if inspect.isawaitable(result) else result
