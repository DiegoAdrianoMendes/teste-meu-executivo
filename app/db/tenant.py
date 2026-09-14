"""Transaction-local workspace context for PostgreSQL row-level security."""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from sqlalchemy.orm.session import SessionTransaction

WORKSPACE_SETTING = "app.current_workspace_id"
WORKSPACE_SESSION_KEY = "klm.workspace_id"

SET_WORKSPACE_CONTEXT_SQL = text(
    "SELECT set_config('app.current_workspace_id', :workspace_id, true)"
)

_APPLIED_TRANSACTION_KEY = "klm.workspace_context_transaction"
_SKIP_REAPPLY_KEY = "klm.workspace_context_skip_reapply"


class WorkspaceContextError(RuntimeError):
    """Raised when one ORM session attempts to cross workspace boundaries."""


class TenantSession(Session):
    """Synchronous session used behind AsyncSession for tenant event hooks."""


def _normalize_workspace_id(workspace_id: UUID | str) -> str:
    return str(UUID(str(workspace_id)))


@event.listens_for(TenantSession, "after_begin")
def _reapply_workspace_context(
    session: TenantSession,
    transaction: SessionTransaction,
    connection,
) -> None:
    """Reapply the transaction-local setting after commit or rollback."""
    workspace_id = session.info.get(WORKSPACE_SESSION_KEY)
    if workspace_id is None or connection.dialect.name != "postgresql":
        return
    if session.info.pop(_SKIP_REAPPLY_KEY, False):
        return

    connection.execute(SET_WORKSPACE_CONTEXT_SQL, {"workspace_id": workspace_id})
    session.info[_APPLIED_TRANSACTION_KEY] = transaction


async def set_workspace_context(session: AsyncSession, workspace_id: UUID | str) -> None:
    """Bind a workspace to the session and its current PostgreSQL transaction.

    PostgreSQL receives ``set_config(..., true)``, which is equivalent to a
    transaction-local setting. SQLite records the session invariant only and
    executes no dialect-specific SQL.
    """
    normalized = _normalize_workspace_id(workspace_id)
    sync_session = session.sync_session
    existing = sync_session.info.get(WORKSPACE_SESSION_KEY)
    if existing is not None and existing != normalized:
        raise WorkspaceContextError(
            "A database session cannot switch workspaces; open a new session instead."
        )
    sync_session.info[WORKSPACE_SESSION_KEY] = normalized

    bind = session.get_bind()
    if bind.dialect.name != "postgresql":
        return

    transaction = sync_session.get_transaction()
    if (
        transaction is not None
        and sync_session.info.get(_APPLIED_TRANSACTION_KEY) is transaction
    ):
        return

    starts_transaction = transaction is None
    if starts_transaction:
        sync_session.info[_SKIP_REAPPLY_KEY] = True
    try:
        await session.execute(
            SET_WORKSPACE_CONTEXT_SQL,
            {"workspace_id": normalized},
        )
    finally:
        sync_session.info.pop(_SKIP_REAPPLY_KEY, None)

    current_transaction = sync_session.get_transaction()
    if current_transaction is not None:
        sync_session.info[_APPLIED_TRANSACTION_KEY] = current_transaction


def get_workspace_context(session: AsyncSession) -> UUID | None:
    """Return the workspace bound to this session, if any."""
    value = session.sync_session.info.get(WORKSPACE_SESSION_KEY)
    return UUID(value) if value is not None else None
