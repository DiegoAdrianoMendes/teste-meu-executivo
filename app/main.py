"""KLM Hub — backend FastAPI."""

import asyncio
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from starlette.requests import Request

from app.api.public_discovery import router as public_discovery_router
from app.api.v1.router import api_router
from app.core.config import settings
from app.core.deps import get_db
from app.core.klm_runtime import (
    aws_external_access_allowed,
    aws_private_storage_allowed,
    warn_retired_flags,
)
from app.core.logging import logger, setup_logging
from app.core.logging_privacy import safe_error_type
from app.core.redis_client import close_shared_redis_clients
from app.core.request_body import RouteBodyLimitMiddleware
from app.middleware.error_tracking import ErrorTrackingMiddleware
from app.middleware.security_headers import SecurityHeadersMiddleware
from app.websockets.endpoint import router as ws_router
from app.websockets.manager import ws_manager
from app.websockets.platform_manager import platform_ws_manager


def generate_operation_id(route: APIRoute) -> str:
    """Gera IDs estáveis e únicos sem acoplar o contrato ao nome da função."""
    methods = "_".join(sorted(method.lower() for method in (route.methods or {"get"})))
    path = re.sub(r"[^a-zA-Z0-9]+", "_", route.path_format).strip("_")
    return f"{route.name}_{methods}_{path}"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Gerencia o ciclo de vida da aplicação: startup e shutdown."""
    # ── Startup ──
    setup_logging()

    warn_retired_flags()

    if settings.uses_local_infrastructure and settings.DATABASE_URL.startswith("sqlite"):
        Path(".local").mkdir(parents=True, exist_ok=True)
        import app.models  # noqa: F401
        from app.db.base import Base
        from app.db.session import engine

        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            from app.db.local_schema import ensure_local_sqlite_schema

            await connection.run_sync(ensure_local_sqlite_schema)
        from app.db.session import AsyncSessionLocal
        from app.services.message_evidence_service import MessageEvidenceService

        async with AsyncSessionLocal() as evidence_db:
            backfilled = await MessageEvidenceService(
                evidence_db
            ).backfill_missing_projection_captures()
            await evidence_db.commit()
        if backfilled:
            logger.info("startup.message_evidence_backfilled", count=backfilled)
        logger.info("startup.local_sqlite_ready")

    await ws_manager.start_redis_listener()
    await platform_ws_manager.start_redis_listener()

    if aws_external_access_allowed() or aws_private_storage_allowed():
        from app.services.s3_service import S3Service

        await asyncio.to_thread(S3Service().verify_private_bucket)

    # Seed flow templates
    try:
        from app.data.flow_templates import seed_templates
        from app.db.session import AsyncSessionLocal

        async with AsyncSessionLocal() as _db:
            inserted = await seed_templates(_db)
            if inserted > 0:
                logger.info("startup.templates_seeded", count=inserted)
    except Exception as _e:
        logger.warning("startup.templates_seed_failed", error_type=safe_error_type(_e))

    yield  # Aplicação em execução

    # ── Shutdown ──
    await ws_manager.stop_redis_listener()
    await platform_ws_manager.stop_redis_listener()
    await close_shared_redis_clients()


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Backend local do Meu Executivo para operação jurídica, CRM, automação e agentes.",
    docs_url="/docs" if not settings.is_production else None,
    redoc_url="/redoc" if not settings.is_production else None,
    openapi_url=(
        "/openapi.json"
        if (not settings.is_production or settings.OPENAPI_ENABLED_IN_PRODUCTION)
        else None
    ),
    lifespan=lifespan,
    generate_unique_id_function=generate_operation_id,
)


def _validation_input_strings(value: object) -> list[str]:
    if isinstance(value, dict):
        return [
            candidate for child in value.values() for candidate in _validation_input_strings(child)
        ]
    if isinstance(value, list | tuple):
        return [candidate for child in value for candidate in _validation_input_strings(child)]
    return [value] if isinstance(value, str) and value else []


@app.exception_handler(RequestValidationError)
async def safe_request_validation_error(
    _request: Request,
    error: RequestValidationError,
) -> JSONResponse:
    """Return useful locations and messages without reflecting submitted values."""

    details: list[dict[str, object]] = []
    for item in error.errors():
        message = str(item.get("msg") or "Valor inválido.")
        for submitted in sorted(
            set(_validation_input_strings(item.get("input"))),
            key=len,
            reverse=True,
        ):
            message = message.replace(submitted, "[redacted]")
        details.append(
            {
                "type": str(item.get("type") or "value_error")[:120],
                "loc": list(item.get("loc") or ()),
                "msg": message[:500],
            }
        )
    return JSONResponse(status_code=422, content={"detail": details})


# ── Error Tracking — captura exceções 5xx e persiste no error_logs ──
app.add_middleware(ErrorTrackingMiddleware)

# ── GZip — comprime respostas JSON >= 1 KB (reduz latência no Brasil ~30-40%) ──
app.add_middleware(GZipMiddleware, minimum_size=1000)

# ── CORS ──
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Multipart parsing happens before endpoint code.  Enforce the scanner ceiling
# at the raw ASGI receive boundary so an oversized legal upload cannot first be
# spooled to worker disk and only then receive a 413.
app.add_middleware(
    RouteBodyLimitMiddleware,
    route_limits={
        # The raw request also contains multipart boundaries and bounded form
        # metadata. The endpoint still enforces DOCUMENT_AV_MAX_BYTES on the
        # actual file; this extra MiB prevents rejecting a valid file exactly
        # at the scanner ceiling while keeping pre-parser disk use bounded.
        ("POST", "/api/v1/legal/documents/upload"): (settings.DOCUMENT_AV_MAX_BYTES + 1024 * 1024),
        ("POST", "/api/v1/finance/reconciliation/import/ofx"): (
            settings.BANK_RECONCILIATION_UPLOAD_MAX_BYTES + 1024 * 1024
        ),
        ("POST", "/api/v1/finance/reconciliation/import/csv"): (
            settings.BANK_RECONCILIATION_UPLOAD_MAX_BYTES + 1024 * 1024
        ),
        ("POST", "/api/v1/finance/harmonization/runs"): (
            settings.FINANCE_HARMONIZATION_UPLOAD_MAX_BYTES + 1024 * 1024
        ),
        ("POST", "/api/v1/migration/jobs"): (settings.MIGRATION_UPLOAD_MAX_BYTES + 1024 * 1024),
        (
            "POST",
            ("/api/v1/legal/demands/{demand_id}/artifacts/{artifact_id}/reimport"),
        ): settings.LEGAL_DOCX_UPLOAD_MAX_BYTES + 1024 * 1024,
    },
)

# Security headers wrap CORS/GZip so they also cover preflights and compressed responses.
app.add_middleware(SecurityHeadersMiddleware, is_production=settings.is_production)

# ── Routers ──
app.include_router(api_router)
app.include_router(ws_router)
app.include_router(public_discovery_router)


# ── OpenAPI com camelCase ──
# Override do método openapi() para garantir que o schema exportado
# use os aliases camelCase (mesmos nomes que o frontend recebe na resposta JSON).
# Isso é crítico para a validação automática de contrato via GitHub Actions.
def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    openapi_schema = get_openapi(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description="Backend local do Meu Executivo para operação jurídica, CRM, automação e agentes.",
        routes=app.routes,
    )
    # Reprocessar todos os schemas para usar aliases camelCase
    # O Pydantic v2 com alias_generator exporta snake_case por padrão no OpenAPI
    # Esta função força o uso dos aliases
    from app.schemas.agent_policy import StagePolicyRead, WorkspaceAutomationSettingsRead
    from app.schemas.agent_runtime import AgentRunRead, HumanOverrideRead
    from app.schemas.approval import ApprovalRead
    from app.schemas.client import ClientRead
    from app.schemas.conversation import ConversationRead
    from app.schemas.document import DocumentRead
    from app.schemas.finance import (
        FinanceEntryRead,
        FinanceOverviewRead,
        FinanceReminderRead,
        FinanceSettingsRead,
        IntegrationConnectionRead,
    )
    from app.schemas.integration import (
        IntegrationCatalogProviderRead,
        OpenApiSurfaceRead,
        WebhookReceiptRead,
    )
    from app.schemas.knowledge_chunk import KnowledgeChunkRead
    from app.schemas.matter import MatterListItem, MatterRead
    from app.schemas.notification import NotificationRead
    from app.schemas.reports import ChartDataPoint, KPIAggregateRead, KPIMetric, ReportsSummary
    from app.schemas.task import TaskRead
    from app.schemas.timeline_event import TimelineEventRead
    from app.schemas.user import UserRead

    alias_schemas = [
        MatterRead,
        MatterListItem,
        TaskRead,
        DocumentRead,
        TimelineEventRead,
        ConversationRead,
        ApprovalRead,
        NotificationRead,
        UserRead,
        ClientRead,
        KPIMetric,
        ReportsSummary,
        ChartDataPoint,
        KPIAggregateRead,
        AgentRunRead,
        HumanOverrideRead,
        KnowledgeChunkRead,
        WorkspaceAutomationSettingsRead,
        StagePolicyRead,
        FinanceEntryRead,
        FinanceReminderRead,
        IntegrationConnectionRead,
        FinanceOverviewRead,
        FinanceSettingsRead,
        IntegrationCatalogProviderRead,
        OpenApiSurfaceRead,
        WebhookReceiptRead,
    ]
    components = openapi_schema.setdefault("components", {})
    schemas = components.setdefault("schemas", {})
    for schema_cls in alias_schemas:
        name = schema_cls.__name__
        schemas[name] = schema_cls.model_json_schema(by_alias=True)

    # Every administrative mutation carries one machine-readable command
    # descriptor. The coverage gate rejects any future route that escapes this
    # catalog, while domain endpoints can still override the generated fields
    # with stricter adapters through openapi_extra.
    from app.core.platform_mutation_registry import administrative_mutation_descriptor

    admin_prefixes = (
        "/api/v1/platform",
        "/api/v1/error-logs",
        "/api/v1/status",
        "/api/v1/super-admin",
        # Control-plane capabilities that are operated from /app as well as
        # Blackbox. They still require the same machine-readable mutation
        # contract, even though they are scoped to a tenant workspace.
        "/api/v1/agent-platform",
        "/api/v1/finance/dimensional",
        "/api/v1/finance/exports",
    )
    for path, path_item in openapi_schema.get("paths", {}).items():
        if not path.startswith(admin_prefixes):
            continue
        for method, operation in path_item.items():
            if method.upper() not in {"POST", "PUT", "PATCH", "DELETE"} or not isinstance(
                operation, dict
            ):
                continue
            operation.setdefault(
                "x-platform-mutation",
                administrative_mutation_descriptor(
                    path=path, method=method.upper(), operation=operation
                ),
            )

    app.openapi_schema = openapi_schema
    return app.openapi_schema


app.openapi = custom_openapi


# ── Health Check ──
@app.get("/health", tags=["Health"], summary="Status da aplicação")
async def health_check():
    """Liveness: o processo responde. Não consulta dependências (o target group do
    ALB e o smoke do deploy batem aqui; banco fora não deve reciclar tasks)."""
    return JSONResponse(
        content={
            "status": "ok",
            "app": settings.APP_NAME,
            "version": settings.APP_VERSION,
            "environment": settings.deployment_environment,
        }
    )


async def _probe_database(db) -> str:
    from sqlalchemy import text

    await asyncio.wait_for(db.execute(text("SELECT 1")), timeout=2.0)
    return "ok"


async def _probe_redis() -> str:
    if settings.uses_in_memory_redis:
        return "skipped"
    from app.core.cache import get_redis

    client = await asyncio.wait_for(get_redis(), timeout=2.0)
    await asyncio.wait_for(client.ping(), timeout=2.0)
    return "ok"


@app.get("/health/ready", tags=["Health"], summary="Prontidão: banco e cache")
async def readiness_check(db=Depends(get_db)):
    """S11: `/health` era estático e ficava verde com o banco fora. Aqui o banco
    (SELECT 1, 2 s) decide o código: 503 quando está fora; Redis fora só degrada
    o corpo (a API funciona sem cache)."""
    checks: dict[str, str] = {}
    try:
        checks["database"] = await _probe_database(db)
    except Exception:  # noqa: BLE001 - qualquer falha é "fora" para a prontidão
        checks["database"] = "down"
    try:
        checks["redis"] = await _probe_redis()
    except Exception:  # noqa: BLE001
        checks["redis"] = "down"
    healthy = checks["database"] == "ok"
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={
            "status": "ok" if healthy else "degraded",
            "checks": checks,
            "app": settings.APP_NAME,
            "version": settings.APP_VERSION,
            "environment": settings.deployment_environment,
        },
    )
