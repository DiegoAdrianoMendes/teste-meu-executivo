"""Durable credential epoch changes with retryable post-commit fanout."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import logger
from app.core.logging_privacy import safe_error_type
from app.core.token_store import revoke_user_tokens
from app.models.identity import (
    AuthRevocationIntent,
    AuthRevocationIntentStatus,
)
from app.models.user import User
from app.websockets.manager import ws_manager

_RETRY_BASE_SECONDS = 30


class AuthRevocationService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def advance_epoch(
        self,
        user: User,
        *,
        reason: str,
    ) -> AuthRevocationIntent:
        """Advance a row-locked user epoch in the password-change transaction."""
        user.auth_epoch = int(user.auth_epoch or 0) + 1
        intent = AuthRevocationIntent(
            id=uuid4(),
            user_id=user.id,
            auth_epoch=user.auth_epoch,
            reason=reason,
            status=AuthRevocationIntentStatus.pending,
            next_attempt_at=datetime.now(UTC),
        )
        self.db.add(intent)
        await self.db.flush()
        return intent

    async def fanout_after_commit(self, intent_id: UUID) -> bool:
        """Best-effort derived-state fanout; the durable epoch remains authoritative."""
        intent = await self.db.scalar(
            select(AuthRevocationIntent)
            .where(AuthRevocationIntent.id == intent_id)
            .with_for_update()
        )
        if intent is None:
            return False
        if intent.status == AuthRevocationIntentStatus.completed:
            return True
        try:
            await revoke_user_tokens(str(intent.user_id))
            await ws_manager.disconnect_user_sessions(intent.user_id, code=4401)
        except Exception as error:
            intent.attempt_count += 1
            intent.last_error_type = safe_error_type(error)
            delay = min(3600, _RETRY_BASE_SECONDS * (2 ** min(intent.attempt_count - 1, 7)))
            intent.next_attempt_at = datetime.now(UTC) + timedelta(seconds=delay)
            await self.db.commit()
            logger.warning(
                "auth_revocation_fanout_pending",
                intent_id=str(intent.id),
                user_id=str(intent.user_id),
                error_type=intent.last_error_type,
            )
            return False

        intent.attempt_count += 1
        intent.status = AuthRevocationIntentStatus.completed
        intent.completed_at = datetime.now(UTC)
        intent.next_attempt_at = intent.completed_at
        intent.last_error_type = None
        await self.db.commit()
        return True

    async def retry_pending(self, *, limit: int = 50) -> int:
        now = datetime.now(UTC)
        intent_ids = list(
            (
                await self.db.scalars(
                    select(AuthRevocationIntent.id)
                    .where(
                        AuthRevocationIntent.status == AuthRevocationIntentStatus.pending,
                        AuthRevocationIntent.next_attempt_at <= now,
                    )
                    .order_by(AuthRevocationIntent.next_attempt_at)
                    .limit(limit)
                )
            ).all()
        )
        completed = 0
        for intent_id in intent_ids:
            completed += int(await self.fanout_after_commit(intent_id))
        return completed
