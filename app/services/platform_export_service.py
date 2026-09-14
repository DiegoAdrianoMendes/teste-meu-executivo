"""Private, permission-aware, asynchronous administrative exports."""

from __future__ import annotations

import asyncio
import csv
import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from html import escape
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.klm_runtime import aws_private_storage_allowed
from app.core.platform_rbac import has_platform_permission, is_platform_root
from app.models.billing import Plan, Subscription
from app.models.growth_crm import GrowthProspect
from app.models.platform_audit_event import PlatformAuditEvent
from app.models.platform_export import PlatformExportJob
from app.models.platform_operations import (
    PlatformBulkJob,
    PlatformBulkJobItem,
    PlatformSelectionItem,
    PlatformSelectionSet,
)
from app.models.support_ticket import SupportTicket, SupportTicketStatus
from app.models.user import User
from app.models.workspace import Workspace
from app.services.platform_audit_event_service import changed_paths, sanitize_audit_value
from app.services.platform_metrics_service import PlatformMetricsService
from app.services.s3_service import S3Service, StorageUnavailableError


class PlatformExportError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 422):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


DATASET_PERMISSIONS = {
    "accounts": (("customers", "export"),),
    "users": (("customers", "export"),),
    "kpis": (("reports", "export"),),
    "billing": (("billing", "export"), ("finance", "export")),
    "tickets": (("support", "export"),),
    "leads": (("sales", "export"),),
    "audit": (("audit", "export"),),
    "bulk_result": (("bulk_operations", "export"),),
}

DEFAULT_COLUMNS: dict[str, list[str]] = {
    "accounts": ["conta", "slug", "status", "ativa", "onboarding_concluido", "criada_em"],
    "users": ["nome", "email", "status", "criado_em"],
    "kpis": ["categoria", "chave", "nome", "valor", "unidade", "qualidade", "fonte", "atualizado_em"],
    "billing": ["conta", "plano", "status", "intervalo", "valor_centavos", "moeda", "trial_ate", "periodo_ate"],
    "tickets": ["numero", "assunto", "conta", "fila", "prioridade", "severidade", "status", "criado_em", "resolvido_em"],
    "leads": ["empresa", "contato", "email", "cidade", "uf", "origem", "estagio", "valor_estimado_centavos", "criado_em"],
    "audit": ["quando", "ator_papel", "root", "modulo", "acao", "recurso", "resultado", "motivo", "correlacao", "campos_alterados"],
    "bulk_result": ["ordem", "conta", "status", "tentativas", "codigo_erro", "processado_em"],
}

ALLOWED_FILTERS: dict[str, set[str]] = {
    "accounts": {"search", "status", "active", "onboardingCompleted"},
    "users": {"search", "status"},
    "kpis": {"periodDays"},
    "billing": {"search", "status", "planId"},
    "tickets": {
        "search", "status", "queue", "priority", "severity", "workspaceId",
        "statuses", "queues", "severities", "ticketTypes", "assignedToUserId", "overdue",
    },
    "leads": {"search", "stage", "source", "ownerUserId"},
    "audit": {"module", "action", "result", "actorId", "workspaceId", "correlationId"},
    "bulk_result": {"bulkJobId", "status"},
}

EXPORT_MEDIA_TYPES = {
    "csv": "text/csv; charset=utf-8",
    "ndjson": "application/x-ndjson",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}

_SAFE_EXPORT_FAILURE_MESSAGES = {
    "PLATFORM_EXPORT_BULK_JOB_INVALID": "O lote informado não pode ser exportado.",
    "PLATFORM_EXPORT_BULK_JOB_NOT_FOUND": "O lote informado não foi encontrado.",
    "PLATFORM_EXPORT_BULK_JOB_REQUIRED": "Informe o lote que será exportado.",
    "PLATFORM_EXPORT_COLUMN_INVALID": "A exportação contém colunas não suportadas.",
    "PLATFORM_EXPORT_DATASET_INVALID": "O conjunto de dados não é exportável.",
    "PLATFORM_EXPORT_FILTER_INVALID": "A exportação contém filtros não suportados.",
    "PLATFORM_EXPORT_FORMAT_INVALID": "O formato solicitado não é suportado.",
    "PLATFORM_EXPORT_SELECTION_EXPIRED": "A seleção da exportação expirou.",
    "PLATFORM_EXPORT_SELECTION_INCOMPATIBLE": "A seleção não pertence a este conjunto de dados.",
    "PLATFORM_EXPORT_SELECTION_NOT_FOUND": "A seleção da exportação não foi encontrada.",
    "PLATFORM_EXPORT_STORAGE_KEY_INVALID": "A chave privada da exportação é inválida.",
    "PLATFORM_EXPORT_STORAGE_UNAVAILABLE": "O armazenamento privado está indisponível.",
    "PLATFORM_EXPORT_STORAGE_UNSAFE": (
        "O armazenamento privado não passou na validação de segurança."
    ),
    "PLATFORM_EXPORT_TOO_LARGE": "O arquivo excede o limite; restrinja os filtros.",
    "PLATFORM_EXPORT_XLSX_LIMIT": "XLSX aceita no máximo 200 mil linhas.",
}


def _safe_export_failure(exc: Exception) -> tuple[str, str]:
    """Return an operator-safe code/message without persisting provider text."""
    if isinstance(exc, PlatformExportError):
        code = exc.code[:120]
        message = _SAFE_EXPORT_FAILURE_MESSAGES.get(code)
        if message is not None:
            return code, message
    return (
        "PLATFORM_EXPORT_INTERNAL_ERROR",
        "A exportação falhou internamente. Consulte o correlation ID.",
    )


@dataclass(frozen=True)
class PlatformExportDownload:
    local_path: Path | None = None
    signed_url: str | None = None


def _enum(value: Any) -> Any:
    return getattr(value, "value", value)


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


def _masked_email(value: str | None) -> str | None:
    if not value or "@" not in value:
        return None
    return f"***@{value.rsplit('@', 1)[1].lower()}"


def _csv_safe(value: Any) -> Any:
    if value is None:
        return ""
    text = str(_iso(value))
    if text.startswith(("=", "+", "-", "@", "\t", "\r")):
        return f"'{text}"
    return text


class PlatformExportService:
    TERMINAL = {"completed", "failed", "cancelled", "expired"}

    def __init__(self, db: AsyncSession, *, s3: S3Service | None = None):
        self.db = db
        self.s3 = s3 or S3Service()

    @staticmethod
    def _storage_root() -> Path:
        configured_root = Path(settings.PLATFORM_EXPORT_STORAGE_DIR).expanduser()
        if configured_root.is_symlink():
            raise PlatformExportError(
                "PLATFORM_EXPORT_STORAGE_UNSAFE",
                "A raiz local de exportações não pode ser um symlink.",
                status_code=503,
            )
        root = configured_root.resolve()
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(root, 0o700)
        return root

    @staticmethod
    def _validate_storage_key(storage_key: str) -> str:
        if (
            not isinstance(storage_key, str)
            or not storage_key
            or "\x00" in storage_key
            or "\\" in storage_key
            or "://" in storage_key
            or Path(storage_key).is_absolute()
        ):
            raise PlatformExportError(
                "PLATFORM_EXPORT_STORAGE_KEY_INVALID",
                "Chave lógica de exportação inválida.",
                status_code=500,
            )
        parts = PurePosixPath(storage_key).parts
        expected_prefix = tuple(
            settings.PLATFORM_EXPORT_STORAGE_PREFIX.strip().split("/")
        )
        if (
            not parts
            or parts[: len(expected_prefix)] != expected_prefix
            or any(part in {"", ".", ".."} for part in parts)
        ):
            raise PlatformExportError(
                "PLATFORM_EXPORT_STORAGE_KEY_INVALID",
                "Chave lógica de exportação inválida.",
                status_code=500,
            )
        return "/".join(parts)

    @classmethod
    def _storage_key(cls, job: PlatformExportJob) -> str:
        return cls._validate_storage_key(
            f"{settings.PLATFORM_EXPORT_STORAGE_PREFIX}/{job.id}/{job.id}.{job.format}"
        )

    @classmethod
    def local_artifact_path(
        cls,
        storage_reference: str,
        *,
        must_exist: bool = False,
    ) -> Path:
        """Resolve a logical key, with local-only legacy absolute-path compatibility."""
        root = cls._storage_root()
        raw_path = Path(storage_reference).expanduser()
        if raw_path.is_absolute():
            if settings.is_production or aws_private_storage_allowed():
                raise PlatformExportError(
                    "PLATFORM_EXPORT_LEGACY_STORAGE_UNAVAILABLE",
                    "Exportação legada local não está disponível neste ambiente.",
                    status_code=410,
                )
            candidate = raw_path.resolve()
        else:
            key = cls._validate_storage_key(storage_reference)
            candidate = root.joinpath(*PurePosixPath(key).parts).resolve()
        if root != candidate and root not in candidate.parents:
            raise PlatformExportError(
                "PLATFORM_EXPORT_STORAGE_KEY_INVALID",
                "Arquivo de exportação fora da raiz privada.",
                status_code=410,
            )
        if must_exist and (not candidate.is_file() or candidate.is_symlink()):
            raise PlatformExportError(
                "PLATFORM_EXPORT_FILE_MISSING",
                "Arquivo não está mais disponível.",
                status_code=410,
            )
        return candidate

    async def _persist_artifact(
        self,
        *,
        source: Path,
        storage_key: str,
        content_type: str,
        checksum_sha256: str,
        dataset: str,
        format: str,
    ) -> None:
        key = self._validate_storage_key(storage_key)
        if aws_private_storage_allowed():
            await asyncio.to_thread(
                self.s3.upload_authorized_private_file,
                source,
                key,
                content_type=content_type,
                state="clean",
                authorization_granted=True,
                checksum_sha256=checksum_sha256,
                metadata={"dataset": dataset, "format": format},
            )
            return
        if settings.is_production:
            raise PlatformExportError(
                "PLATFORM_EXPORT_STORAGE_UNAVAILABLE",
                "Storage privado compartilhado é obrigatório em produção.",
                status_code=503,
            )
        destination = self.local_artifact_path(key)
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if destination.parent.is_symlink():
            raise PlatformExportError(
                "PLATFORM_EXPORT_STORAGE_UNSAFE",
                "Diretório local de exportação inválido.",
                status_code=503,
            )
        os.replace(source, destination)
        os.chmod(destination, 0o600)

    async def delete_artifact(self, storage_reference: str) -> None:
        if aws_private_storage_allowed():
            key = self._validate_storage_key(storage_reference)
            try:
                await asyncio.to_thread(
                    self.s3.delete_authorized_private_object,
                    key,
                    authorization_granted=True,
                )
            except StorageUnavailableError as exc:
                raise PlatformExportError(
                    "PLATFORM_EXPORT_STORAGE_UNAVAILABLE",
                    "Não foi possível excluir o arquivo privado.",
                    status_code=503,
                ) from exc
            return
        if settings.is_production:
            raise PlatformExportError(
                "PLATFORM_EXPORT_STORAGE_UNAVAILABLE",
                "Storage privado compartilhado é obrigatório em produção.",
                status_code=503,
            )
        path = self.local_artifact_path(storage_reference)
        path.unlink(missing_ok=True)

    async def authorize_download(
        self,
        job: PlatformExportJob,
    ) -> PlatformExportDownload:
        if not job.storage_path:
            raise PlatformExportError(
                "PLATFORM_EXPORT_NOT_READY",
                "Arquivo ainda não está disponível.",
                status_code=409,
            )
        if aws_private_storage_allowed():
            key = self._validate_storage_key(job.storage_path)
            filename = f"blackbox-{job.dataset}-{job.id}.{job.format}"
            try:
                signed_url = await asyncio.to_thread(
                    self.s3.generate_authorized_private_download_url,
                    key,
                    expires_in=settings.PLATFORM_EXPORT_DOWNLOAD_URL_EXPIRY_SECONDS,
                    authorization_granted=True,
                    response_content_type=EXPORT_MEDIA_TYPES[job.format],
                    response_content_disposition=f'attachment; filename="{filename}"',
                )
            except StorageUnavailableError as exc:
                raise PlatformExportError(
                    "PLATFORM_EXPORT_STORAGE_UNAVAILABLE",
                    "Não foi possível autorizar o download privado.",
                    status_code=503,
                ) from exc
            return PlatformExportDownload(signed_url=signed_url)
        if settings.is_production:
            raise PlatformExportError(
                "PLATFORM_EXPORT_STORAGE_UNAVAILABLE",
                "Storage privado compartilhado é obrigatório em produção.",
                status_code=503,
            )
        return PlatformExportDownload(
            local_path=self.local_artifact_path(job.storage_path, must_exist=True)
        )

    @staticmethod
    def authorize(actor: User | None, dataset: str) -> None:
        permissions = DATASET_PERMISSIONS.get(dataset)
        if (
            actor is None
            or permissions is None
            or not any(has_platform_permission(actor, *permission) for permission in permissions)
        ):
            raise PlatformExportError(
                "PLATFORM_EXPORT_FORBIDDEN", "Exportação não autorizada.", status_code=403
            )

    async def create_job(
        self,
        *,
        actor: User,
        dataset: str,
        format: str,
        filters: dict,
        columns: list[str],
        selection_set_id: UUID | None,
        idempotency_key: str,
    ) -> PlatformExportJob:
        self.authorize(actor, dataset)
        existing = await self.db.scalar(
            select(PlatformExportJob).where(
                PlatformExportJob.actor_user_id == actor.id,
                PlatformExportJob.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            existing._created_for_request = False
            return existing
        if format not in {"csv", "xlsx", "ndjson"}:
            raise PlatformExportError("PLATFORM_EXPORT_FORMAT_INVALID", "Formato inválido.")
        unknown_filters = set(filters) - ALLOWED_FILTERS.get(dataset, set())
        if unknown_filters:
            raise PlatformExportError(
                "PLATFORM_EXPORT_FILTER_INVALID",
                f"Filtros não suportados: {', '.join(sorted(unknown_filters))}.",
            )
        unknown_columns = set(columns) - set(DEFAULT_COLUMNS.get(dataset, []))
        if unknown_columns:
            raise PlatformExportError(
                "PLATFORM_EXPORT_COLUMN_INVALID",
                f"Colunas não suportadas: {', '.join(sorted(unknown_columns))}.",
            )
        if dataset == "bulk_result" and selection_set_id is None and not filters.get("bulkJobId"):
            raise PlatformExportError(
                "PLATFORM_EXPORT_BULK_JOB_REQUIRED", "Informe o lote a exportar."
            )
        if selection_set_id is not None:
            selection_set = await self.db.get(PlatformSelectionSet, selection_set_id)
            if selection_set is None:
                raise PlatformExportError(
                    "PLATFORM_EXPORT_SELECTION_NOT_FOUND", "Seleção não encontrada.", status_code=404
                )
            expires_at = selection_set.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            if selection_set.status != "active" or expires_at <= datetime.now(UTC):
                raise PlatformExportError(
                    "PLATFORM_EXPORT_SELECTION_EXPIRED", "Seleção expirada.", status_code=410
                )
            if selection_set.actor_user_id != actor.id and not is_platform_root(actor):
                raise PlatformExportError(
                    "PLATFORM_EXPORT_FORBIDDEN", "Seleção pertence a outro operador.", status_code=403
                )
            if dataset != "accounts" or selection_set.resource_type != "workspace":
                raise PlatformExportError(
                    "PLATFORM_EXPORT_SELECTION_INCOMPATIBLE",
                    "A seleção não é compatível com este dataset.",
                )
        if dataset == "bulk_result":
            try:
                bulk_job_id = UUID(str(filters["bulkJobId"]))
            except (KeyError, TypeError, ValueError) as exc:
                raise PlatformExportError(
                    "PLATFORM_EXPORT_BULK_JOB_INVALID", "Lote inválido."
                ) from exc
            bulk_job = await self.db.get(PlatformBulkJob, bulk_job_id)
            if bulk_job is None:
                raise PlatformExportError(
                    "PLATFORM_EXPORT_BULK_JOB_NOT_FOUND", "Lote não encontrado.", status_code=404
                )
            if bulk_job.actor_user_id != actor.id and not is_platform_root(actor):
                raise PlatformExportError(
                    "PLATFORM_EXPORT_FORBIDDEN", "Lote pertence a outro operador.", status_code=403
                )
        job = PlatformExportJob(
            actor_user_id=actor.id,
            dataset=dataset,
            format=format,
            filters=filters,
            columns=list(dict.fromkeys(columns)) or DEFAULT_COLUMNS[dataset],
            selection_set_id=selection_set_id,
            status="queued",
            processed_rows=0,
            idempotency_key=idempotency_key,
            correlation_id=str(uuid4()),
        )
        self.db.add(job)
        await self.db.flush()
        job._created_for_request = True
        return job

    async def get_job(self, job_id: UUID) -> PlatformExportJob:
        job = await self.db.get(PlatformExportJob, job_id)
        if job is None:
            raise PlatformExportError("PLATFORM_EXPORT_NOT_FOUND", "Exportação não encontrada.", status_code=404)
        return job

    async def list_jobs(
        self, *, actor: User, limit: int, offset: int
    ) -> tuple[list[PlatformExportJob], int]:
        where = [] if is_platform_root(actor) else [PlatformExportJob.actor_user_id == actor.id]
        total = int((await self.db.scalar(select(func.count(PlatformExportJob.id)).where(*where))) or 0)
        rows = list((await self.db.scalars(
            select(PlatformExportJob)
            .where(*where)
            .order_by(PlatformExportJob.created_at.desc(), PlatformExportJob.id.desc())
            .limit(limit).offset(offset)
        )).all())
        return rows, total

    async def request_cancel(self, job_id: UUID) -> PlatformExportJob:
        job = await self.db.scalar(
            select(PlatformExportJob).where(PlatformExportJob.id == job_id).with_for_update()
        )
        if job is None:
            raise PlatformExportError("PLATFORM_EXPORT_NOT_FOUND", "Exportação não encontrada.", status_code=404)
        if job.status in self.TERMINAL:
            return job
        job.cancel_requested_at = job.cancel_requested_at or datetime.now(UTC)
        if job.status == "queued":
            job.status = "cancelled"
            job.completed_at = datetime.now(UTC)
        else:
            job.status = "cancelling"
        await self.db.flush()
        return job

    async def _rows(self, job: PlatformExportJob, *, actor: User) -> AsyncIterator[dict[str, Any]]:
        root = is_platform_root(actor)
        if job.dataset == "kpis":
            payload = await PlatformMetricsService(self.db).get_all_metrics(
                period_days=int(job.filters.get("periodDays", 30))
            )
            for category in payload.get("categories", []):
                for metric in category.get("kpis", []):
                    if not root and metric.get("category") in {"receita", "cobranca"}:
                        continue
                    yield {
                        "categoria": metric.get("category"), "chave": metric.get("key"),
                        "nome": metric.get("label"), "valor": metric.get("value"),
                        "unidade": metric.get("unit"), "qualidade": metric.get("quality"),
                        "fonte": metric.get("source"), "atualizado_em": metric.get("updated_at"),
                    }
            return

        if job.dataset == "accounts":
            statement = select(Workspace).where(Workspace.deleted_at.is_(None))
            if job.selection_set_id:
                statement = statement.join(
                    PlatformSelectionItem,
                    PlatformSelectionItem.resource_id == Workspace.id,
                ).where(PlatformSelectionItem.selection_set_id == job.selection_set_id)
            search = str(job.filters.get("search", "")).strip()
            if search:
                statement = statement.where(
                    or_(Workspace.name.ilike(f"%{search}%"), Workspace.slug.ilike(f"%{search}%"))
                )
            if job.filters.get("status"):
                statement = statement.where(Workspace.status == job.filters["status"])
            if "active" in job.filters:
                statement = statement.where(Workspace.is_active.is_(bool(job.filters["active"])))
            if "onboardingCompleted" in job.filters:
                statement = statement.where(
                    Workspace.onboarding_completed.is_(bool(job.filters["onboardingCompleted"]))
                )
            result = await self.db.stream(statement.order_by(Workspace.id))
            async for workspace in result.scalars():
                yield {"conta": workspace.name, "slug": workspace.slug, "status": _enum(workspace.status), "ativa": workspace.is_active, "onboarding_concluido": workspace.onboarding_completed, "criada_em": workspace.created_at}
            return
        if job.dataset == "users":
            statement = select(User).where(
                User.is_internal_staff.is_(False), User.deleted_at.is_(None)
            )
            search = str(job.filters.get("search", "")).strip()
            if search:
                statement = statement.where(
                    or_(User.name.ilike(f"%{search}%"), User.email.ilike(f"%{search}%"))
                )
            if job.filters.get("status") == "active":
                statement = statement.where(User.is_active.is_(True))
            elif job.filters.get("status") == "inactive":
                statement = statement.where(User.is_active.is_(False))
            result = await self.db.stream(statement.order_by(User.id))
            async for user in result.scalars():
                yield {"nome": user.name, "email": user.email if root else _masked_email(user.email), "status": "ativo" if user.is_active else "inativo", "criado_em": user.created_at}
            return
        if job.dataset == "billing":
            statement = (
                select(Subscription, Workspace, Plan)
                .join(Workspace, Workspace.id == Subscription.workspace_id)
                .join(Plan, Plan.id == Subscription.plan_id)
            )
            search = str(job.filters.get("search", "")).strip()
            if search:
                statement = statement.where(
                    or_(Workspace.name.ilike(f"%{search}%"), Plan.name.ilike(f"%{search}%"))
                )
            if job.filters.get("status"):
                statement = statement.where(Subscription.status == job.filters["status"])
            if job.filters.get("planId"):
                statement = statement.where(Subscription.plan_id == UUID(str(job.filters["planId"])))
            result = await self.db.stream(statement.order_by(Subscription.id))
            async for subscription, workspace, plan in result:
                yield {"conta": workspace.name, "plano": plan.name, "status": _enum(subscription.status), "intervalo": _enum(subscription.interval), "valor_centavos": subscription.amount_cents if root else None, "moeda": subscription.currency, "trial_ate": subscription.trial_end, "periodo_ate": subscription.current_period_end}
            return
        if job.dataset == "tickets":
            statement = select(SupportTicket, Workspace).join(
                Workspace, Workspace.id == SupportTicket.workspace_id
            )
            search = str(job.filters.get("search", "")).strip()
            if search:
                statement = statement.where(or_(
                    SupportTicket.public_number.ilike(f"%{search}%"),
                    SupportTicket.subject.ilike(f"%{search}%"),
                    Workspace.name.ilike(f"%{search}%"),
                ))
            for key, column in {
                "status": SupportTicket.status, "queue": SupportTicket.queue,
                "priority": SupportTicket.priority, "severity": SupportTicket.severity,
                "workspaceId": SupportTicket.workspace_id,
            }.items():
                if job.filters.get(key):
                    value = UUID(str(job.filters[key])) if key == "workspaceId" else job.filters[key]
                    statement = statement.where(column == value)
            for key, column in {
                "statuses": SupportTicket.status,
                "queues": SupportTicket.queue,
                "severities": SupportTicket.severity,
                "ticketTypes": SupportTicket.ticket_type,
            }.items():
                values = job.filters.get(key)
                if isinstance(values, list) and values:
                    statement = statement.where(column.in_(values))
            if job.filters.get("assignedToUserId"):
                statement = statement.where(
                    SupportTicket.assigned_to_user_id
                    == UUID(str(job.filters["assignedToUserId"]))
                )
            if "overdue" in job.filters and isinstance(job.filters["overdue"], bool):
                overdue_clause = and_(
                    SupportTicket.status.in_([
                        SupportTicketStatus.new,
                        SupportTicketStatus.triage,
                        SupportTicketStatus.in_progress,
                        SupportTicketStatus.waiting_customer,
                        SupportTicketStatus.waiting_internal,
                        SupportTicketStatus.validating,
                    ]),
                    SupportTicket.first_responded_at.is_(None),
                    SupportTicket.first_response_due_at < datetime.now(UTC),
                )
                statement = statement.where(
                    overdue_clause if job.filters["overdue"] else ~overdue_clause
                )
            result = await self.db.stream(statement.order_by(SupportTicket.id))
            async for ticket, workspace in result:
                yield {"numero": ticket.public_number, "assunto": ticket.subject, "conta": workspace.name, "fila": _enum(ticket.queue), "prioridade": _enum(ticket.priority), "severidade": _enum(ticket.severity), "status": _enum(ticket.status), "criado_em": ticket.created_at, "resolvido_em": ticket.resolved_at}
            return
        if job.dataset == "leads":
            statement = select(GrowthProspect)
            search = str(job.filters.get("search", "")).strip()
            if search:
                statement = statement.where(or_(
                    GrowthProspect.nome_escritorio.ilike(f"%{search}%"),
                    GrowthProspect.contato_nome.ilike(f"%{search}%"),
                    GrowthProspect.contato_email.ilike(f"%{search}%"),
                ))
            for key, column in {
                "stage": GrowthProspect.estagio, "source": GrowthProspect.origem,
                "ownerUserId": GrowthProspect.owner_user_id,
            }.items():
                if job.filters.get(key):
                    value = UUID(str(job.filters[key])) if key == "ownerUserId" else job.filters[key]
                    statement = statement.where(column == value)
            result = await self.db.stream(statement.order_by(GrowthProspect.id))
            async for lead in result.scalars():
                yield {"empresa": lead.nome_escritorio, "contato": lead.contato_nome, "email": lead.contato_email if root else _masked_email(lead.contato_email), "cidade": lead.cidade, "uf": lead.uf, "origem": _enum(lead.origem), "estagio": _enum(lead.estagio), "valor_estimado_centavos": lead.valor_estimado_cents, "criado_em": lead.created_at}
            return
        if job.dataset == "audit":
            statement = select(PlatformAuditEvent)
            for key, column in {
                "module": PlatformAuditEvent.module, "action": PlatformAuditEvent.action,
                "result": PlatformAuditEvent.result, "actorId": PlatformAuditEvent.actor_id,
                "workspaceId": PlatformAuditEvent.workspace_id,
                "correlationId": PlatformAuditEvent.correlation_id,
            }.items():
                if job.filters.get(key):
                    value = UUID(str(job.filters[key])) if key in {"actorId", "workspaceId"} else job.filters[key]
                    statement = statement.where(column == value)
            result = await self.db.stream(statement.order_by(
                PlatformAuditEvent.occurred_at.desc(), PlatformAuditEvent.id.desc()
            ))
            async for event in result.scalars():
                # Eventos legados podem anteceder o sanitizer no write. Reaplique o
                # sanitizer canônico antes de qualquer dado chegar ao arquivo.
                safe_event = sanitize_audit_value({
                    "before": event.before,
                    "after": event.after,
                    "reason": event.reason,
                })
                safe_paths = changed_paths(
                    safe_event.get("before"),
                    safe_event.get("after"),
                )
                yield {"quando": event.occurred_at, "ator_papel": event.actor_role_snapshot, "root": event.is_platform_root, "modulo": event.module, "acao": event.action, "recurso": event.resource_label_snapshot or event.resource_type, "resultado": event.result, "motivo": safe_event.get("reason"), "correlacao": event.correlation_id, "campos_alterados": ",".join(safe_paths)}
            return
        if job.dataset == "bulk_result":
            bulk_job_id = UUID(str(job.filters["bulkJobId"]))
            statement = (
                select(PlatformBulkJobItem, Workspace)
                .outerjoin(Workspace, Workspace.id == PlatformBulkJobItem.resource_id)
                .where(PlatformBulkJobItem.job_id == bulk_job_id)
            )
            if job.filters.get("status"):
                statement = statement.where(PlatformBulkJobItem.status == job.filters["status"])
            result = await self.db.stream(statement.order_by(PlatformBulkJobItem.ordinal))
            async for item, workspace in result:
                yield {"ordem": item.ordinal, "conta": workspace.name if workspace else "Registro removido", "status": item.status, "tentativas": item.attempts, "codigo_erro": item.error_code, "processado_em": item.processed_at}
            return
        raise PlatformExportError("PLATFORM_EXPORT_DATASET_INVALID", "Dataset inválido.")

    @staticmethod
    def _write_xlsx(rows_path: Path, destination: Path, headers: list[str]) -> None:
        with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
            archive.writestr("_rels/.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
            archive.writestr("xl/workbook.xml", '<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Export" sheetId="1" r:id="rId1"/></sheets></workbook>')
            archive.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
            with archive.open("xl/worksheets/sheet1.xml", "w") as sheet, rows_path.open("r", encoding="utf-8") as source:
                sheet.write(b'<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>')
                for row_number, line in enumerate(source, start=1):
                    values = json.loads(line)
                    cells = "".join(f'<c t="inlineStr"><is><t>{escape(str(_csv_safe(value)))}</t></is></c>' for value in values)
                    sheet.write(f'<row r="{row_number}">{cells}</row>'.encode())
                sheet.write(b"</sheetData></worksheet>")

    async def run_job(self, job_id: UUID) -> PlatformExportJob:
        job = await self.db.scalar(
            select(PlatformExportJob)
            .where(PlatformExportJob.id == job_id)
            .with_for_update()
        )
        if job is None:
            raise PlatformExportError(
                "PLATFORM_EXPORT_NOT_FOUND",
                "Exportação não encontrada.",
                status_code=404,
            )
        if job.status in self.TERMINAL:
            return job
        # Recovery first moves a stale running job back to queued. A second
        # concurrent delivery must not process a job already claimed.
        if job.status != "queued":
            return job
        job.status = "running"
        job.started_at = job.started_at or datetime.now(UTC)
        job.processed_rows = 0
        job.row_count = None
        job.byte_size = None
        job.checksum_sha256 = None
        job.storage_path = None
        job.error_code = None
        job.error_detail = None
        await self.db.commit()

        root = self._storage_root()
        temporary_dir = Path(tempfile.mkdtemp(prefix=f"export-{job.id}-", dir=root))
        destination = temporary_dir / f"artifact.{job.format}"
        rows_path = temporary_dir / "rows.ndjson"
        headers: list[str] = job.columns or DEFAULT_COLUMNS[job.dataset]
        storage_key = self._storage_key(job)
        artifact_persisted = False
        try:
            actor = await self.db.get(User, job.actor_user_id)
            self.authorize(actor, job.dataset)
            with rows_path.open("w", encoding="utf-8", newline="") as row_file:
                row_file.write(json.dumps(headers, ensure_ascii=False) + "\n")
                async for row in self._rows(job, actor=actor):
                    if job.cancel_requested_at is not None:
                        job.status = "cancelled"
                        break
                    row_file.write(json.dumps([_iso(row.get(key)) for key in headers], ensure_ascii=False, default=str) + "\n")
                    job.processed_rows += 1
                    if job.processed_rows % 250 == 0:
                        cancel_requested_at = await self.db.scalar(
                            select(PlatformExportJob.cancel_requested_at).where(
                                PlatformExportJob.id == job.id
                            )
                        )
                        if cancel_requested_at is not None:
                            job.cancel_requested_at = cancel_requested_at
                            job.status = "cancelled"
                            break
                    if job.processed_rows % 5_000 == 0:
                        await self.db.commit()
            if job.status == "cancelled":
                job.completed_at = datetime.now(UTC)
                await self.db.commit()
                return job
            if job.format == "ndjson":
                with destination.open("w", encoding="utf-8") as output, rows_path.open("r", encoding="utf-8") as source:
                    header_row = json.loads(source.readline() or "[]")
                    for line in source:
                        values = json.loads(line)
                        output.write(json.dumps(dict(zip(header_row, values, strict=False)), ensure_ascii=False) + "\n")
            elif job.format == "csv":
                with destination.open("w", encoding="utf-8-sig", newline="") as output, rows_path.open("r", encoding="utf-8") as source:
                    writer = csv.writer(output)
                    for line in source:
                        writer.writerow([_csv_safe(value) for value in json.loads(line)])
            else:
                if job.processed_rows > 200_000:
                    raise PlatformExportError("PLATFORM_EXPORT_XLSX_LIMIT", "XLSX aceita no máximo 200 mil linhas.")
                self._write_xlsx(rows_path, destination, headers)
            size = destination.stat().st_size
            if size > settings.PLATFORM_EXPORT_MAX_BYTES:
                raise PlatformExportError("PLATFORM_EXPORT_TOO_LARGE", "Arquivo excede 500 MB; restrinja os filtros.")
            job.row_count = job.processed_rows
            job.byte_size = size
            digest = hashlib.sha256()
            with destination.open("rb") as exported:
                for chunk in iter(lambda: exported.read(1024 * 1024), b""):
                    digest.update(chunk)
            job.checksum_sha256 = digest.hexdigest()
            await self._persist_artifact(
                source=destination,
                storage_key=storage_key,
                content_type=EXPORT_MEDIA_TYPES[job.format],
                checksum_sha256=job.checksum_sha256,
                dataset=job.dataset,
                format=job.format,
            )
            artifact_persisted = True
            job.storage_path = storage_key
            job.status = "completed"
            job.completed_at = datetime.now(UTC)
            job.expires_at = job.completed_at + timedelta(hours=24)
            await self.db.commit()
            return job
        except Exception as exc:
            destination.unlink(missing_ok=True)
            if artifact_persisted:
                try:
                    await self.delete_artifact(storage_key)
                except Exception:
                    pass
            job.status = "failed"
            job.error_code, job.error_detail = _safe_export_failure(exc)
            job.completed_at = datetime.now(UTC)
            await self.db.commit()
            return job
        finally:
            shutil.rmtree(temporary_dir, ignore_errors=True)
