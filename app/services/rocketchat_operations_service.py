"""Read-only operational projection of the local Rocket.Chat workspace."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.oss_bridge import (
    ExternalEntityLink,
    ExternalEntitySyncState,
    OSSBridgeProvider,
)
from app.services.rocketchat_client import RocketChatClient


def _pick(row: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {key: row.get(key) for key in keys if row.get(key) is not None}


def _attachments(row: dict[str, Any]) -> list[dict[str, Any]]:
    attachments = row.get("attachments")
    normalized = (
        [
            _pick(
                attachment,
                "title",
                "description",
                "type",
                "format",
                "size",
                "author_name",
            )
            for attachment in attachments
            if isinstance(attachment, dict)
        ]
        if isinstance(attachments, list)
        else []
    )
    file_data = row.get("file")
    if isinstance(file_data, dict):
        file_attachment = _pick(file_data, "_id", "name", "type", "size", "format")
        if file_attachment and not any(
            item.get("_id") == file_attachment.get("_id")
            or item.get("title") == file_attachment.get("name")
            for item in normalized
        ):
            normalized.append(file_attachment)
    return normalized


def _reactions(row: dict[str, Any]) -> list[dict[str, Any]]:
    reactions = row.get("reactions")
    if not isinstance(reactions, dict):
        return []
    normalized: list[dict[str, Any]] = []
    for emoji, value in reactions.items():
        details = value if isinstance(value, dict) else {}
        usernames = details.get("usernames")
        users = (
            [str(username)[:80] for username in usernames[:50]]
            if isinstance(usernames, list)
            else []
        )
        normalized.append(
            {
                "emoji": str(emoji)[:80],
                "count": len(users),
                "usernames": users,
            }
        )
    return normalized


def _message(row: dict[str, Any]) -> dict[str, Any]:
    attachments = _attachments(row)
    return {
        **_pick(row, "_id", "msg", "ts", "_updatedAt", "tmid", "tcount", "tlm"),
        "user": _pick(row.get("u") or {}, "_id", "username", "name")
        if isinstance(row.get("u"), dict)
        else None,
        "attachments": attachments,
        "attachmentCount": len(attachments),
        "reactions": _reactions(row),
    }


class RocketChatOperationsService:
    def __init__(self, db: AsyncSession, client: RocketChatClient | None = None):
        self.db = db
        self.client = client or RocketChatClient()

    async def _room_link(self, workspace_id: UUID, thread_id: UUID) -> ExternalEntityLink | None:
        info = await self.client.info()
        account_id = str(
            info.get("hashedWorkspaceUrl")
            or info.get("cloudWorkspaceId")
            or info.get("uniqueID")
            or ""
        ).strip()
        if not account_id:
            raise ValueError("Rocket.Chat não informou sua identidade estável.")
        return await self.db.scalar(
            select(ExternalEntityLink).where(
                ExternalEntityLink.workspace_id == workspace_id,
                ExternalEntityLink.provider == OSSBridgeProvider.rocketchat,
                ExternalEntityLink.external_account_id == account_id,
                ExternalEntityLink.klm_entity_type == "omnichannel_thread",
                ExternalEntityLink.klm_entity_id == thread_id,
                ExternalEntityLink.external_entity_type == "room",
                ExternalEntityLink.sync_state == ExternalEntitySyncState.synced,
            )
        )

    async def launch_target(self, workspace_id: UUID, thread_id: UUID) -> dict[str, str] | None:
        """Resolve the stable room identity needed by a contextual deep link."""

        link = await self._room_link(workspace_id, thread_id)
        if link is None:
            return None
        metadata = link.link_metadata or {}
        room_type = str(
            metadata.get("roomType") or ("c" if metadata.get("scope") == "internal_team" else "p")
        )
        room_name = str(metadata.get("roomName") or "").strip()
        if not room_name:
            room = await self.client.room_info(link.external_entity_id, room_type)
            room_name = str(room.get("name") or room.get("fname") or "").strip()
        if not room_name and room_type != "d":
            return None
        return {
            "roomId": link.external_entity_id,
            "roomName": room_name,
            "roomType": room_type,
        }

    async def context(self, workspace_id: UUID, thread_id: UUID) -> dict[str, Any]:
        link = await self._room_link(workspace_id, thread_id)
        if link is None:
            return {
                "linked": False,
                "threadId": str(thread_id),
                "roomId": None,
                "room": None,
                "members": [],
                "messages": [],
                "discussions": [],
                "runtimeCompatibility": "6.13.1",
                "auditedReference": "8.6.1",
                "sourceOfTruth": "klm",
            }
        room_id = link.external_entity_id
        metadata = link.link_metadata or {}
        room_type = str(
            metadata.get("roomType") or ("c" if metadata.get("scope") == "internal_team" else "p")
        )
        room, members, messages, discussions = await asyncio.gather(
            self.client.room_info(room_id, room_type),
            self.client.room_members(room_id, room_type),
            self.client.room_history(room_id, room_type),
            self.client.discussions(room_id),
        )
        return {
            "linked": True,
            "threadId": str(thread_id),
            "roomId": room_id,
            "roomType": room_type,
            "room": _pick(room, "_id", "name", "fname", "t", "usersCount", "ts", "_updatedAt"),
            "members": [
                _pick(row, "_id", "username", "name", "status", "roles") for row in members
            ],
            "messages": [_message(row) for row in messages],
            "discussions": [
                _pick(row, "_id", "name", "fname", "msgs", "lm", "ts") for row in discussions
            ],
            "runtimeCompatibility": "6.13.1",
            "auditedReference": "8.6.1",
            "sourceOfTruth": "klm",
        }
