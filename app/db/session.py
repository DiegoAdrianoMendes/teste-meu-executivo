"""
Configuração do engine e sessão assíncrona do SQLAlchemy.

Pool strategy:
  - Testes explícitos: NullPool (sem estado compartilhado entre event loops)
  - Servidor SQLite local: pool pequeno e persistente (evita exaustão de descritores)
  - FastAPI (HTTP): AsyncAdaptedQueuePool (pool persistente, melhor latência)
  - Celery workers: NullPool (cada task cria seu próprio loop — pool compartilhado causaria
    "Future attached to a different loop" no asyncpg)
"""
import asyncio
import os

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import AsyncAdaptedQueuePool, NullPool

from app.core.config import settings
from app.db.post_commit import (
    discard_realtime_events,
    publish_committed_realtime_events,
)
from app.db.rls import install_rls_runtime_guard
from app.db.tenant import TenantSession


class KlmAsyncSession(AsyncSession):
    """Publishes advisory realtime events only after the DB commit succeeds."""

    async def commit(self) -> None:
        await super().commit()
        await publish_committed_realtime_events(self)

    async def rollback(self) -> None:
        discard_realtime_events(self)
        await super().rollback()

_is_test = os.environ.get("TESTING", "").lower() in ("1", "true", "yes")
_is_sqlite = "sqlite" in settings.DATABASE_URL.lower()

# SQLite local has one effective writer. This lock is used only by the
# canonical message endpoint; PostgreSQL keeps its normal write concurrency.
sqlite_message_write_lock = asyncio.Lock()


def _configure_sqlite_connection(dbapi_connection, _connection_record) -> None:
    """Make the local SQLite runtime tolerate concurrent HTTP writes.

    The local verifier intentionally replays idempotent requests in parallel.
    WAL allows readers alongside the short write transactions and the extended
    busy timeout lets the losing writers observe the unique idempotency row
    instead of surfacing a transient 500.
    """
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=30000")
    finally:
        cursor.close()

# ── Engine principal (FastAPI / HTTP) ────────────────────────────────────────
if _is_test:
    engine = create_async_engine(
        settings.DATABASE_URL,
        echo=False,
        pool_pre_ping=False,
        poolclass=NullPool,
        connect_args={"timeout": 30},
        hide_parameters=True,
    )
elif _is_sqlite:
    # O servidor local permanece ativo por horas e atende o Blackbox completo.
    # NullPool abre uma conexão/descritor por request; sob carga longa, threads
    # aiosqlite ainda em encerramento podem ultrapassar o limite baixo do launchd
    # no macOS. Um pool pequeno limita deterministicamente esse consumo.
    engine = create_async_engine(
        settings.DATABASE_URL,
        echo=False,
        pool_pre_ping=True,
        poolclass=AsyncAdaptedQueuePool,
        pool_size=5,
        max_overflow=5,
        pool_timeout=30,
        pool_recycle=1800,
        connect_args={"timeout": 30},
        hide_parameters=True,
    )
else:
    engine = create_async_engine(
        settings.DATABASE_URL,
        echo=settings.is_development and settings.DATABASE_SQL_ECHO,
        pool_pre_ping=True,
        poolclass=AsyncAdaptedQueuePool,
        pool_size=10,
        max_overflow=20,
        pool_timeout=30,
        pool_recycle=1800,
        hide_parameters=True,
    )

if "sqlite" in settings.DATABASE_URL.lower():
    event.listen(engine.sync_engine, "connect", _configure_sqlite_connection)

install_rls_runtime_guard(engine)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=KlmAsyncSession,
    sync_session_class=TenantSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)

# ── Engine para Celery workers (NullPool — sem compartilhamento entre loops) ─
# Cada task Celery cria um novo event loop via asyncio.new_event_loop().
# O pool do engine principal está vinculado ao loop da FastAPI, causando
# "Future attached to a different loop" no asyncpg quando reutilizado.
_worker_engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_pre_ping=False,
    poolclass=NullPool,  # Nova conexão por task — sem conflito de loops
    hide_parameters=True,
)
install_rls_runtime_guard(_worker_engine)

WorkerSessionLocal = async_sessionmaker(
    bind=_worker_engine,
    class_=KlmAsyncSession,
    sync_session_class=TenantSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


def model_usage_session_factory():
    """Select the loop-safe session maker at call time.

    FastAPI keeps the queue pool. A Celery task gets the NullPool worker engine,
    including when the gateway singleton was imported before task execution.
    """
    try:
        from celery import current_task

        request = getattr(current_task, "request", None)
        if getattr(request, "id", None):
            return WorkerSessionLocal
    except Exception:
        pass
    return AsyncSessionLocal
