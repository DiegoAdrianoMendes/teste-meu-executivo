"""
Serviço de rastreamento de erros.
Captura, deduplica e persiste erros com códigos únicos.
"""
import hashlib
import traceback as tb
from datetime import UTC, datetime
from pathlib import Path
from typing import Optional
from uuid import UUID

from sqlalchemy import select, update, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging_privacy import safe_error_type
from app.models.error_log import ErrorLog, ErrorSeverity, ErrorStatus


class ErrorTrackingService:

    @staticmethod
    def _compute_hash(exception_type: str, message: str, location: str) -> str:
        """Computa hash SHA-256 para agrupar erros idênticos."""
        payload = f"{exception_type}|{message[:200]}|{location}"
        return hashlib.sha256(payload.encode()).hexdigest()

    @staticmethod
    def _generate_code() -> str:
        """Gera código ERR-XXXXXX sequencial."""
        import random
        return f"ERR-{random.randint(100000, 999999)}"

    @staticmethod
    def _classify_severity(exception_type: str, status_code: Optional[int] = None) -> ErrorSeverity:
        if status_code and status_code >= 500:
            return ErrorSeverity.critical
        if "Auth" in exception_type or "Permission" in exception_type:
            return ErrorSeverity.high
        if "Timeout" in exception_type or "Connection" in exception_type:
            return ErrorSeverity.high
        if "Validation" in exception_type or "Value" in exception_type:
            return ErrorSeverity.medium
        return ErrorSeverity.medium

    @classmethod
    async def capture(
        cls,
        db: AsyncSession,
        exception: Exception,
        endpoint: Optional[str] = None,
        method: Optional[str] = None,
        user_id: Optional[UUID] = None,
        workspace_id: Optional[UUID] = None,
        request_id: Optional[str] = None,
        extra: Optional[dict] = None,
        status_code: Optional[int] = None,
    ) -> ErrorLog:
        """Captura um erro, deduplica por hash e persiste no banco."""

        exception_type = safe_error_type(exception)
        message = f"{exception_type}: details redacted"

        # Keep code location for triage without persisting exception text or source lines.
        location = ""
        frames = tb.extract_tb(exception.__traceback__) if exception.__traceback__ else []
        app_frames = [frame for frame in frames if "site-packages" not in frame.filename]
        if app_frames:
            frame = app_frames[-1]
            location = f"{Path(frame.filename).name}:{frame.lineno} in {frame.name}"[:500]

        error_hash = cls._compute_hash(exception_type, message, location)

        # Verificar se já existe (deduplicação)
        existing = await db.execute(
            select(ErrorLog).where(
                ErrorLog.error_hash == error_hash,
                ErrorLog.status != ErrorStatus.resolved,
            ).limit(1)
        )
        existing_log = existing.scalar_one_or_none()

        if existing_log:
            # Incrementar contador de ocorrências
            existing_log.occurrences += 1
            existing_log.last_seen_at = datetime.now(UTC)
            if endpoint:
                existing_log.endpoint = endpoint
            if user_id:
                existing_log.user_id = user_id
            await db.commit()
            return existing_log

        # Novo erro
        severity = cls._classify_severity(exception_type, status_code)
        error_log = ErrorLog(
            error_code=cls._generate_code(),
            error_hash=error_hash,
            severity=severity,
            exception_type=exception_type,
            message=message[:2000],
            traceback=None,
            location=location,
            endpoint=endpoint,
            method=method,
            user_id=user_id,
            workspace_id=workspace_id,
            request_id=request_id,
            extra={"provided": True} if extra else None,
        )
        db.add(error_log)
        await db.commit()
        await db.refresh(error_log)
        return error_log

    @classmethod
    async def list_errors(
        cls,
        db: AsyncSession,
        status: Optional[ErrorStatus] = None,
        severity: Optional[ErrorSeverity] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> tuple[list[ErrorLog], int]:
        query = select(ErrorLog)
        count_query = select(func.count()).select_from(ErrorLog)

        if status:
            query = query.where(ErrorLog.status == status)
            count_query = count_query.where(ErrorLog.status == status)
        if severity:
            query = query.where(ErrorLog.severity == severity)
            count_query = count_query.where(ErrorLog.severity == severity)

        query = query.order_by(
            ErrorLog.last_seen_at.desc(), ErrorLog.id.desc()
        ).offset(skip).limit(limit)

        result = await db.execute(query)
        total = await db.execute(count_query)
        return result.scalars().all(), total.scalar_one()

    @classmethod
    async def update_status(
        cls,
        db: AsyncSession,
        error_id: UUID,
        new_status: ErrorStatus,
        resolved_by: Optional[str] = None,
        resolution_notes: Optional[str] = None,
        *,
        commit: bool = True,
    ) -> Optional[ErrorLog]:
        result = await db.execute(
            select(ErrorLog).where(ErrorLog.id == error_id).with_for_update()
        )
        error = result.scalar_one_or_none()
        if not error:
            return None

        error.status = new_status
        if new_status == ErrorStatus.resolved:
            error.resolved_at = datetime.now(UTC)
            error.resolved_by = resolved_by
            error.resolution_notes = resolution_notes

        await (db.commit() if commit else db.flush())
        await db.refresh(error)
        return error
