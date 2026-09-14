"""
Endpoint público de status do sistema.
Monitora componentes em tempo real: DB, Redis, WhatsApp, ZapSign, Celery.
"""
import asyncio
import time
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache_get, cache_set
from app.core.config import settings
from app.core.deps import get_db
from app.core.logging import logger
from app.core.logging_privacy import safe_error_type
from app.core.platform_access import require_platform_permission
from app.models.omnichannel_v6 import (
    OmnichannelProviderWebhookEnvelope,
    OmnichannelProviderWebhookProcessingState,
)
from app.models.user import User
from app.services.periodic_task_health_service import PeriodicTaskHealthService
from app.services.system_status_service import SystemStatusHistoryService

router = APIRouter(prefix="/status", tags=["status"])

CELERY_STATUS_CACHE_TTL_SECONDS = 10.0
CELERY_INSPECT_TIMEOUT_SECONDS = 0.75
CELERY_STATUS_CACHE_KEY = "system-status:celery"
WHATSAPP_STATUS_CACHE_TTL_SECONDS = 30.0
WHATSAPP_STATUS_CACHE_KEY = "system-status:whatsapp"
STATUS_OBSERVATION_INTERVAL_SECONDS = 300.0
_celery_status_cache: tuple[float, dict] | None = None
_celery_status_task: asyncio.Task[dict] | None = None
_celery_status_task_loop: asyncio.AbstractEventLoop | None = None
_whatsapp_status_cache: tuple[float, dict] | None = None
_whatsapp_status_task: asyncio.Task[dict] | None = None
_whatsapp_status_task_loop: asyncio.AbstractEventLoop | None = None
_status_observation_guard = Lock()
_status_observation_next_at = 0.0


def _reserve_status_observation(*, now: float | None = None) -> bool:
    """Reserve one persistence slot without making concurrent readers queue on DB writes."""
    global _status_observation_next_at

    current = time.monotonic() if now is None else now
    with _status_observation_guard:
        if current < _status_observation_next_at:
            return False
        _status_observation_next_at = current + STATUS_OBSERVATION_INTERVAL_SECONDS
        return True


def _release_status_observation_reservation() -> None:
    global _status_observation_next_at

    with _status_observation_guard:
        _status_observation_next_at = 0.0


async def check_database() -> dict:
    start = time.monotonic()
    try:
        from app.db.session import AsyncSessionLocal
        async with AsyncSessionLocal() as db:
            await db.execute(text("SELECT 1"))
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        return {"status": "operational", "latency_ms": latency_ms}
    except Exception:
        return {"status": "degraded", "error": "Banco indisponível", "latency_ms": None}


async def check_redis() -> dict:
    start = time.monotonic()
    try:
        from app.core.cache import get_redis
        r = await get_redis()
        await r.ping()
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        return {"status": "operational", "latency_ms": latency_ms}
    except Exception:
        return {"status": "degraded", "error": "Cache indisponível", "latency_ms": None}


async def _inspect_celery() -> dict:
    try:
        from app.workers.celery_app import celery_app

        inspector = celery_app.control.inspect(timeout=CELERY_INSPECT_TIMEOUT_SECONDS)
        active = await asyncio.to_thread(inspector.active)
        if active is None:
            if settings.uses_local_infrastructure:
                return {
                    "status": "not_configured",
                    "workers": 0,
                    "note": "Worker distribuído não iniciado no modo local",
                }
            return {"status": "degraded", "workers": 0, "note": "Nenhum worker respondeu"}
        worker_count = len(active)
        return {"status": "operational", "workers": worker_count}
    except Exception:
        return {"status": "unknown", "error": "Workers não puderam ser verificados"}


async def check_celery() -> dict:
    """Verifica workers sem multiplicar broadcasts concorrentes no endpoint público."""
    global _celery_status_cache, _celery_status_task, _celery_status_task_loop

    now = time.monotonic()
    if _celery_status_cache is not None and _celery_status_cache[0] > now:
        return dict(_celery_status_cache[1])

    shared = await cache_get(CELERY_STATUS_CACHE_KEY)
    if isinstance(shared, dict) and isinstance(shared.get("status"), str):
        _celery_status_cache = (
            time.monotonic() + CELERY_STATUS_CACHE_TTL_SECONDS,
            dict(shared),
        )
        return dict(shared)

    loop = asyncio.get_running_loop()
    task = _celery_status_task
    if task is None or task.done() or _celery_status_task_loop is not loop:
        task = loop.create_task(_inspect_celery())
        _celery_status_task = task
        _celery_status_task_loop = loop

    result = await asyncio.shield(task)
    if _celery_status_task is task:
        await cache_set(
            CELERY_STATUS_CACHE_KEY,
            result,
            ttl_seconds=int(CELERY_STATUS_CACHE_TTL_SECONDS),
        )
        _celery_status_cache = (
            time.monotonic() + CELERY_STATUS_CACHE_TTL_SECONDS,
            dict(result),
        )
        _celery_status_task = None
        _celery_status_task_loop = None
    return dict(result)


async def _inspect_whatsapp() -> dict:
    """Separa prontidão da plataforma de uma conexão realmente operacional."""
    try:
        from app.db.session import AsyncSessionLocal
        from app.db.tenant import set_workspace_context
        from app.models.whatsapp import (
            WaConnectionStatus,
            WaMessageDirection,
            WaMessageStatus,
            WhatsAppConnection,
            WhatsAppMessage,
        )
        from app.models.workspace import Workspace

        async with AsyncSessionLocal() as control_db:
            workspace_ids = tuple(
                await control_db.scalars(
                    select(Workspace.id).where(Workspace.is_active.is_(True))
                )
            )

        connected_id = None
        recent_provider_acceptance = None
        cutoff = datetime.now(UTC) - timedelta(hours=24)
        for workspace_id in workspace_ids:
            # WhatsApp tables are FORCE-RLS tenant tables. Inspect each workspace
            # in its own transaction instead of attempting an unsafe global query.
            async with AsyncSessionLocal() as connection_db:
                await set_workspace_context(connection_db, workspace_id)
                eligible_connection = (
                    WhatsAppConnection.status == WaConnectionStatus.connected,
                    WhatsAppConnection.phone_number_id.is_not(None),
                    (
                        WhatsAppConnection.access_token_encrypted.is_not(None)
                        | WhatsAppConnection.business_token_encrypted.is_not(None)
                    ),
                )
                recent_provider_acceptance = await connection_db.scalar(
                    select(WhatsAppMessage.id)
                    .join(
                        WhatsAppConnection,
                        WhatsAppConnection.id
                        == WhatsAppMessage.whatsapp_connection_id,
                    )
                    .where(
                        *eligible_connection,
                        WhatsAppMessage.direction == WaMessageDirection.outbound,
                        WhatsAppMessage.status.in_(
                            (
                                WaMessageStatus.sent,
                                WaMessageStatus.delivered,
                                WaMessageStatus.read,
                            )
                        ),
                        WhatsAppMessage.wamid.is_not(None),
                        WhatsAppMessage.timestamp >= cutoff,
                    )
                    .limit(1)
                )
                if recent_provider_acceptance is not None:
                    break
                if connected_id is None:
                    connected_id = await connection_db.scalar(
                        select(WhatsAppConnection.id)
                        .where(*eligible_connection)
                        .limit(1)
                    )
        if recent_provider_acceptance is not None:
            return {
                "status": "operational",
                "note": "A Meta confirmou atividade de envio nas últimas 24 horas",
            }
        if connected_id is not None:
            return {
                "status": "connected_unverified",
                "note": "Número cadastrado localmente; funcionamento externo não confirmado",
            }

        encryption_ready = bool(
            settings.WHATSAPP_SECRET_ENCRYPTION_KEYS.strip()
            or settings.INTEGRATION_SECRET_ENCRYPTION_KEY.strip()
        )
        embedded_signup_ready = all(
            (
                settings.META_APP_ID.strip(),
                settings.META_CONFIG_ID.strip(),
                settings.WHATSAPP_APP_SECRET.strip(),
                settings.WHATSAPP_VERIFY_TOKEN.strip(),
            )
        )
        if (
            len(settings.WHATSAPP_ACCESS_TOKEN.strip()) > 10
            and bool(settings.WHATSAPP_PHONE_NUMBER_ID.strip())
        ):
            return {
                "status": "configured_unverified",
                "note": "Credencial global presente; funcionamento externo não confirmado",
            }
        if embedded_signup_ready and encryption_ready:
            return {
                "status": "prepared",
                "note": "Plataforma preparada; nenhum número operacional conectado",
            }
        return {
            "status": "not_configured",
            "note": "Provisionamento Meta da plataforma incompleto",
        }
    except Exception:
        return {"status": "unknown", "error": "Configuração não pôde ser verificada"}


async def check_whatsapp() -> dict:
    """Cacheia e agrega a inspeção tenant-safe sem fan-out por leitor público."""
    global _whatsapp_status_cache, _whatsapp_status_task, _whatsapp_status_task_loop

    now = time.monotonic()
    if _whatsapp_status_cache is not None and _whatsapp_status_cache[0] > now:
        return dict(_whatsapp_status_cache[1])

    shared = await cache_get(WHATSAPP_STATUS_CACHE_KEY)
    if isinstance(shared, dict) and isinstance(shared.get("status"), str):
        _whatsapp_status_cache = (
            time.monotonic() + WHATSAPP_STATUS_CACHE_TTL_SECONDS,
            dict(shared),
        )
        return dict(shared)

    loop = asyncio.get_running_loop()
    task = _whatsapp_status_task
    if task is None or task.done() or _whatsapp_status_task_loop is not loop:
        task = loop.create_task(_inspect_whatsapp())
        _whatsapp_status_task = task
        _whatsapp_status_task_loop = loop

    result = await asyncio.shield(task)
    if _whatsapp_status_task is task:
        await cache_set(
            WHATSAPP_STATUS_CACHE_KEY,
            result,
            ttl_seconds=int(WHATSAPP_STATUS_CACHE_TTL_SECONDS),
        )
        _whatsapp_status_cache = (
            time.monotonic() + WHATSAPP_STATUS_CACHE_TTL_SECONDS,
            dict(result),
        )
        _whatsapp_status_task = None
        _whatsapp_status_task_loop = None
    return dict(result)


async def check_zapsign() -> dict:
    """Verifica se ZapSign global está configurado."""
    try:
        from app.core.config import settings
        token = getattr(settings, "ZAPSIGN_API_TOKEN", "")
        if token and len(token) > 10:
            return {"status": "operational", "note": "Credencial global configurada"}
        return {"status": "not_configured", "note": "Credencial externa não configurada"}
    except Exception:
        return {"status": "unknown", "error": "Configuração não pôde ser verificada"}


_UNVERIFIED_STATUSES = frozenset({"prepared", "connected_unverified", "configured_unverified"})
_EXTERNAL_INTEGRATION_COMPONENTS = frozenset({"whatsapp_api", "zapsign_api"})
_COMPONENT_LABELS = {"whatsapp_api": "WhatsApp", "zapsign_api": "ZapSign", "workers": "worker"}


def _partial_message(components: dict) -> str:
    """V3: `partial` juntava "não configuradas" e "cadastradas, não verificadas" numa
    frase só, e a tela dizia as duas coisas ao mesmo tempo. A frase agora nasce dos
    componentes que a causaram; worker local não iniciado não é integração externa."""
    not_configured = sorted(
        _COMPONENT_LABELS.get(name, name)
        for name, value in components.items()
        if value.get("status") == "not_configured" and name in _EXTERNAL_INTEGRATION_COMPONENTS
    )
    unverified = sorted(
        _COMPONENT_LABELS.get(name, name)
        for name, value in components.items()
        if value.get("status") in _UNVERIFIED_STATUSES
    )
    workers_off = components.get("workers", {}).get("status") == "not_configured"
    frases = ["O núcleo está disponível"]
    if not_configured:
        frases.append(f"integrações externas ainda não configuradas: {', '.join(not_configured)}")
    if unverified:
        frases.append(
            f"integrações cadastradas e ainda não verificadas: {', '.join(unverified)}"
        )
    if workers_off:
        frases.append("worker distribuído não iniciado no modo local")
    if len(frases) == 1:
        frases.append("há componentes parcialmente configurados")
    return "; ".join(frases) + "."


def _overall_status(components: dict) -> str:
    statuses = [v.get("status") for v in components.values()]
    if any(s == "degraded" for s in statuses):
        return "degraded"
    if any(s == "not_configured" for s in statuses):
        return "partial"
    if any(
        s in {"prepared", "connected_unverified", "configured_unverified"}
        for s in statuses
    ):
        return "partial"
    if any(s == "unknown" for s in statuses):
        return "unknown"
    return "operational"


@router.get("")
async def get_system_status(db: AsyncSession = Depends(get_db)):
    """Status público do sistema — sem autenticação."""

    # Checar todos em paralelo
    db_result, redis_result, celery_result, wa_result, zs_result = await asyncio.gather(
        check_database(),
        check_redis(),
        check_celery(),
        check_whatsapp(),
        check_zapsign(),
        return_exceptions=True,
    )

    def safe(r):
        if isinstance(r, Exception):
            return {"status": "unknown", "error": "Verificação indisponível"}
        return r

    components = {
        "database": safe(db_result),
        "cache": safe(redis_result),
        "workers": safe(celery_result),
        "whatsapp_api": safe(wa_result),
        "zapsign_api": safe(zs_result),
    }

    overall = _overall_status(components)

    observed_at = datetime.now(UTC)
    message = {
        "operational": "Todos os componentes verificados estão operando normalmente.",
        "degraded": "Um ou mais componentes apresentaram falha na observação atual.",
        "partial": _partial_message(components),
        "unknown": "O estado de um ou mais componentes não pôde ser determinado.",
    }.get(overall, "")
    history_recorded = False
    if _reserve_status_observation():
        try:
            history_recorded = await SystemStatusHistoryService(db).record(
                overall_status=overall,
                components=components,
                message=message,
                observed_at=observed_at,
            )
            await db.commit()
        except Exception as error:
            _release_status_observation_reservation()
            await db.rollback()
            logger.warning(
                "status.observation_persist_failed", error_type=safe_error_type(error)
            )

    return {
        "status": overall,
        "timestamp": observed_at.isoformat(),
        "version": settings.APP_VERSION,
        "components": components,
        "message": message,
        "historyRecorded": history_recorded,
    }


@router.get("/tasks")
async def get_periodic_task_status(
    _: User = Depends(require_platform_permission("infrastructure", "view")),
    db: AsyncSession = Depends(get_db),
):
    """Estado por task individual do Celery Beat — restrito ao Blackbox técnico.

    O `GET /status` agregado é público de propósito: responde "no ar / fora do
    ar". Este aqui é outra coisa — enumera as 27 rotinas internas do deploy
    (campanha de WhatsApp, hidratação de lead, régua de cobrança, OAuth Meta)
    com contador de falha por rotina. Isso é mapa de arquitetura e estado
    operacional, não sinal de disponibilidade, e por isso exige root.
    A mensagem crua do erro continua fora da resposta mesmo assim: fica no
    banco e no log. Só o TIPO do erro sai daqui. Funcionários com
    `infrastructure:view` podem observar as rotinas sem receber poder root.
    """
    try:
        return {"available": True, **await PeriodicTaskHealthService(db).overview()}
    except Exception as error:
        await db.rollback()
        logger.warning("status.periodic_tasks_failed", error_type=safe_error_type(error))
        # `available: false` em vez de um resumo zerado: "não consegui ler" e
        # "nenhuma task falhando" são estados opostos e não podem ter a mesma
        # aparência na tela.
        return {
            "available": False,
            "generatedAt": datetime.now(UTC).isoformat(),
            "graceSeconds": 0,
            "summary": None,
            "tasks": [],
        }


@router.get("/webhooks/whatsapp")
async def get_whatsapp_webhook_processing_status(
    processing_status: Literal[
        "queued",
        "processing",
        "processed",
        "retryable_failed",
        "terminal_failed",
    ]
    | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    _: User = Depends(require_platform_permission("infrastructure", "view")),
    db: AsyncSession = Depends(get_db),
):
    """Queryable durable webhook state for Blackbox operations.

    Signed bytes, signature headers and request metadata never leave custody.
    The response exposes only processing metadata and the sanitized error code.
    """

    statuses = (
        "queued",
        "processing",
        "processed",
        "retryable_failed",
        "terminal_failed",
    )
    try:
        counts = {status_name: 0 for status_name in statuses}
        grouped = await db.execute(
            select(
                OmnichannelProviderWebhookProcessingState.status,
                func.count(),
            ).group_by(OmnichannelProviderWebhookProcessingState.status)
        )
        for status_name, count in grouped.all():
            if status_name in counts:
                counts[status_name] = int(count)

        statement = (
            select(
                OmnichannelProviderWebhookProcessingState,
                OmnichannelProviderWebhookEnvelope.provider,
            )
            .join(
                OmnichannelProviderWebhookEnvelope,
                OmnichannelProviderWebhookEnvelope.id
                == OmnichannelProviderWebhookProcessingState.envelope_id,
            )
            .order_by(
                OmnichannelProviderWebhookProcessingState.updated_at.desc(),
                OmnichannelProviderWebhookProcessingState.envelope_id,
            )
            .limit(limit)
        )
        if processing_status is not None:
            statement = statement.where(
                OmnichannelProviderWebhookProcessingState.status
                == processing_status
            )
        rows = (await db.execute(statement)).all()
        return {
            "available": True,
            "generatedAt": datetime.now(UTC).isoformat(),
            "filter": processing_status,
            "counts": counts,
            "items": [
                {
                    "envelopeId": state.envelope_id,
                    "provider": provider,
                    "status": state.status,
                    "attempts": state.attempts,
                    "queuedAt": state.queued_at,
                    "processingStartedAt": state.processing_started_at,
                    "lastAttemptedAt": state.last_attempted_at,
                    "nextRetryAt": state.next_retry_at,
                    "processedAt": state.processed_at,
                    "lastFailedAt": state.last_failed_at,
                    "terminalFailedAt": state.terminal_failed_at,
                    "leaseExpiresAt": state.lease_expires_at,
                    "lastErrorCode": state.last_error_code,
                }
                for state, provider in rows
            ],
        }
    except Exception as error:
        await db.rollback()
        logger.warning(
            "status.whatsapp_webhook_processing_failed",
            error_type=safe_error_type(error),
        )
        return {
            "available": False,
            "generatedAt": datetime.now(UTC).isoformat(),
            "filter": processing_status,
            "counts": None,
            "items": [],
        }


@router.get("/history")
async def get_status_history(db: AsyncSession = Depends(get_db)):
    """Agrega somente observações reais persistidas nos últimos 30 dias."""
    try:
        return await SystemStatusHistoryService(db).history()
    except Exception as error:
        await db.rollback()
        logger.warning("status.history_failed", error_type=safe_error_type(error))
        return {
            "state": "unavailable",
            "periodDays": 30,
            "observations": 0,
            "uptimePercent": None,
            "firstObservedAt": None,
            "lastObservedAt": None,
            "daysObserved": 0,
            "latestStatus": None,
            "daily": [],
            "incidents": [],
        }
