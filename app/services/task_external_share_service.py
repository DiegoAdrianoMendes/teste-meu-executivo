"""Secure external task sharing without internal collaboration leakage."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import secrets
from collections import defaultdict, deque
from datetime import UTC, datetime
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import get_redis
from app.core.config import settings
from app.core.logging_privacy import opaque_ref
from app.db.tenant import set_workspace_context
from app.models.klm_operations import KlmAuditEvent
from app.models.task_external_share import TaskExternalShare
from app.schemas.task_external_share import TaskExternalSharePreviewRequest
from app.services.task_service import TaskMutationContext, TaskService

_LOCAL_RATE_LOCK = asyncio.Lock()
_LOCAL_RATE_EVENTS: dict[str, deque[float]] = defaultdict(deque)
_RATE_WINDOW_SECONDS = 60
_RATE_LIMIT = 30


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class TaskExternalShareService:
    def __init__(self, db: AsyncSession):
        self.db = db

    @staticmethod
    def _token_hash(token: str) -> str:
        return hmac.new(
            settings.SECRET_KEY.encode("utf-8"),
            f"task-external-share:{token}".encode(),
            hashlib.sha256,
        ).hexdigest()

    @staticmethod
    def _idempotency_hash(workspace_id: UUID, key: str) -> str:
        return hmac.new(
            settings.SECRET_KEY.encode("utf-8"),
            f"task-external-share-idempotency:{workspace_id}:{key}".encode(),
            hashlib.sha256,
        ).hexdigest()

    @staticmethod
    def _idempotent_replay(share: TaskExternalShare) -> HTTPException:
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "TASK_SHARE_IDEMPOTENT_REPLAY",
                "message": (
                    "Este link já foi criado. O token é exibido apenas na primeira "
                    "resposta; consulte a lista ou revogue o registro existente."
                ),
                "shareId": str(share.id),
                "expiresAt": _as_utc(share.expires_at).isoformat(),
            },
        )

    @staticmethod
    def _idempotency_conflict() -> HTTPException:
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "TASK_SHARE_IDEMPOTENCY_CONFLICT",
                "message": "Idempotency-Key já utilizada para outra solicitação.",
            },
        )

    @staticmethod
    def _matches_replay(
        share: TaskExternalShare,
        *,
        task_id: UUID,
        actor_user_id: UUID,
        preview: dict,
    ) -> bool:
        return bool(
            share.task_id == task_id
            and share.created_by_user_id == actor_user_id
            and share.title_snapshot == preview["title"]
            and share.status_snapshot == preview["status"]
            and (
                _as_utc(share.due_at_snapshot).isoformat()
                if share.due_at_snapshot
                else None
            )
            == preview["dueAt"]
            and share.safe_context_snapshot == preview["safeContext"]
            and _as_utc(share.expires_at).isoformat() == preview["expiresAt"]
        )

    @staticmethod
    def _locator_cipher() -> Fernet:
        key = hashlib.sha256(f"task-external-share-locator:{settings.SECRET_KEY}".encode()).digest()
        return Fernet(base64.urlsafe_b64encode(key))

    @classmethod
    def _issue_token(cls, workspace_id: UUID) -> str:
        locator = json.dumps(
            {"workspaceId": str(workspace_id), "nonce": secrets.token_urlsafe(32)},
            separators=(",", ":"),
        ).encode("utf-8")
        return cls._locator_cipher().encrypt(locator).decode("ascii")

    @classmethod
    def _workspace_from_token(cls, token: str) -> UUID:
        try:
            payload = json.loads(
                cls._locator_cipher().decrypt(token.encode("ascii"), ttl=31 * 86400)
            )
            return UUID(payload["workspaceId"])
        except (
            InvalidToken,
            UnicodeError,
            ValueError,
            KeyError,
            TypeError,
            json.JSONDecodeError,
        ) as exc:
            raise HTTPException(status_code=404, detail="Link indisponível.") from exc

    @staticmethod
    def _preview_fingerprint(task_id: UUID, payload: dict) -> str:
        canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hmac.new(
            settings.SECRET_KEY.encode("utf-8"),
            f"task-share-preview:{task_id}:{canonical}".encode(),
            hashlib.sha256,
        ).hexdigest()

    async def preview(
        self,
        task_id: UUID,
        workspace_id: UUID,
        data: TaskExternalSharePreviewRequest,
        *,
        actor_user_id: UUID,
        allow_workspace: bool,
    ) -> dict:
        task = await TaskService(self.db).get_for_actor(
            task_id,
            workspace_id,
            actor_user_id=actor_user_id,
            allow_workspace=allow_workspace,
        )
        if task is None:
            raise HTTPException(status_code=404, detail="Tarefa não encontrada.")
        safe_context = " ".join(data.safe_context.split()) if data.safe_context else None
        payload = {
            "title": task.title,
            "status": "cancelled" if task.cancelled_at else task.status.value,
            "dueAt": _as_utc(task.due_at).isoformat() if task.due_at else None,
            "safeContext": safe_context,
            "expiresAt": data.expires_at.astimezone(UTC).isoformat(),
        }
        return {
            **payload,
            "previewFingerprint": self._preview_fingerprint(task.id, payload),
            "excludedContent": [
                "descrição interna",
                "comentários",
                "histórico",
                "participantes",
                "anexos",
            ],
        }

    async def create(
        self,
        task_id: UUID,
        workspace_id: UUID,
        data: TaskExternalSharePreviewRequest,
        preview_fingerprint: str,
        idempotency_key: str,
        *,
        context: TaskMutationContext,
        allow_workspace: bool,
    ) -> tuple[TaskExternalShare, str]:
        if context.actor_user_id is None:
            raise HTTPException(status_code=403, detail="Autoria humana obrigatória.")
        preview = await self.preview(
            task_id,
            workspace_id,
            data,
            actor_user_id=context.actor_user_id,
            allow_workspace=allow_workspace,
        )
        expected = preview.pop("previewFingerprint")
        preview.pop("excludedContent")
        if not hmac.compare_digest(expected, preview_fingerprint):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "TASK_SHARE_PREVIEW_STALE",
                    "message": "A prévia mudou. Revise antes de criar o link.",
                },
            )
        idempotency_hash = self._idempotency_hash(workspace_id, idempotency_key)
        existing = await self.db.scalar(
            select(TaskExternalShare).where(
                TaskExternalShare.workspace_id == workspace_id,
                TaskExternalShare.idempotency_key_hash == idempotency_hash,
            )
        )
        if existing is not None:
            if not self._matches_replay(
                existing,
                task_id=task_id,
                actor_user_id=context.actor_user_id,
                preview=preview,
            ):
                raise self._idempotency_conflict()
            raise self._idempotent_replay(existing)
        raw_token = self._issue_token(workspace_id)
        share = TaskExternalShare(
            workspace_id=workspace_id,
            task_id=task_id,
            token_hash=self._token_hash(raw_token),
            idempotency_key_hash=idempotency_hash,
            title_snapshot=preview["title"],
            status_snapshot=preview["status"],
            due_at_snapshot=datetime.fromisoformat(preview["dueAt"]) if preview["dueAt"] else None,
            safe_context_snapshot=preview["safeContext"],
            expires_at=datetime.fromisoformat(preview["expiresAt"]),
            created_by_user_id=context.actor_user_id,
        )
        try:
            async with self.db.begin_nested():
                self.db.add(share)
                await self.db.flush()
        except IntegrityError:
            existing = await self.db.scalar(
                select(TaskExternalShare).where(
                    TaskExternalShare.workspace_id == workspace_id,
                    TaskExternalShare.idempotency_key_hash == idempotency_hash,
                )
            )
            if existing is not None:
                if not self._matches_replay(
                    existing,
                    task_id=task_id,
                    actor_user_id=context.actor_user_id,
                    preview=preview,
                ):
                    raise self._idempotency_conflict()
                raise self._idempotent_replay(existing)
            raise
        self.db.add(
            KlmAuditEvent(
                workspace_id=workspace_id,
                actor_user_id=context.actor_user_id,
                actor_type="human",
                actor_label="Usuário do workspace",
                event_type="task_external_share_created",
                entity_type="task_external_share",
                entity_id=share.id,
                summary="Link externo somente leitura criado para tarefa jurídica.",
                correlation_id=context.correlation_id,
                after_data={
                    "taskId": str(task_id),
                    "expiresAt": preview["expiresAt"],
                    "scope": ["title", "status", "dueAt", "safeContext"],
                },
            )
        )
        await self.db.flush()
        return share, raw_token

    async def list_for_task(self, task_id: UUID, workspace_id: UUID) -> list[TaskExternalShare]:
        return list(
            (
                await self.db.scalars(
                    select(TaskExternalShare)
                    .where(
                        TaskExternalShare.workspace_id == workspace_id,
                        TaskExternalShare.task_id == task_id,
                    )
                    .order_by(TaskExternalShare.created_at.desc())
                )
            ).all()
        )

    async def revoke(
        self,
        share_id: UUID,
        task_id: UUID,
        workspace_id: UUID,
        *,
        context: TaskMutationContext,
    ) -> TaskExternalShare:
        share = await self.db.scalar(
            select(TaskExternalShare)
            .where(
                TaskExternalShare.id == share_id,
                TaskExternalShare.task_id == task_id,
                TaskExternalShare.workspace_id == workspace_id,
            )
            .with_for_update()
        )
        if share is None:
            raise HTTPException(status_code=404, detail="Link não encontrado.")
        if share.revoked_at is None:
            share.revoked_at = datetime.now(UTC)
            share.revoked_by_user_id = context.actor_user_id
            self.db.add(
                KlmAuditEvent(
                    workspace_id=workspace_id,
                    actor_user_id=context.actor_user_id,
                    actor_type="human",
                    actor_label="Usuário do workspace",
                    event_type="task_external_share_revoked",
                    entity_type="task_external_share",
                    entity_id=share.id,
                    summary="Link externo de tarefa jurídica revogado.",
                    correlation_id=context.correlation_id,
                )
            )
            await self.db.flush()
        return share

    async def redeem(self, token: str, network_key: str, correlation_id: str) -> dict:
        await self._enforce_rate_limit(network_key)
        workspace_id = self._workspace_from_token(token)
        await set_workspace_context(self.db, workspace_id)
        token_hash = self._token_hash(token)
        share = await self.db.scalar(
            select(TaskExternalShare).where(
                TaskExternalShare.workspace_id == workspace_id,
                TaskExternalShare.token_hash == token_hash,
            )
        )
        now = datetime.now(UTC)
        if share is None or share.revoked_at is not None or _as_utc(share.expires_at) <= now:
            raise HTTPException(status_code=404, detail="Link indisponível.")
        self.db.add(
            KlmAuditEvent(
                workspace_id=share.workspace_id,
                actor_user_id=None,
                actor_type="external_viewer",
                actor_label="Visitante externo",
                event_type="task_external_share_redeemed",
                entity_type="task_external_share",
                entity_id=share.id,
                summary="Link externo somente leitura consultado.",
                correlation_id=correlation_id,
                after_data={"networkRef": opaque_ref(network_key, kind="network")},
            )
        )
        await self.db.flush()
        return {
            "title": share.title_snapshot,
            "status": share.status_snapshot,
            "dueAt": share.due_at_snapshot,
            "safeContext": share.safe_context_snapshot,
            "expiresAt": _as_utc(share.expires_at),
        }

    async def _enforce_rate_limit(self, network_key: str) -> None:
        key = f"task-share-redeem:{opaque_ref(network_key, kind='network')}"
        if not settings.uses_in_memory_redis:
            try:
                redis = await get_redis()
                count = await redis.incr(key)
                if count == 1:
                    await redis.expire(key, _RATE_WINDOW_SECONDS)
                if count > _RATE_LIMIT:
                    raise HTTPException(
                        status_code=429, detail="Muitas consultas. Tente novamente em instantes."
                    )
                return
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(
                    status_code=503, detail="Proteção do link temporariamente indisponível."
                ) from exc
        now = asyncio.get_running_loop().time()
        async with _LOCAL_RATE_LOCK:
            events = _LOCAL_RATE_EVENTS[key]
            while events and events[0] <= now - _RATE_WINDOW_SECONDS:
                events.popleft()
            if len(events) >= _RATE_LIMIT:
                raise HTTPException(
                    status_code=429, detail="Muitas consultas. Tente novamente em instantes."
                )
            events.append(now)
