"""Shared ORM and SQLite guards for append-only evidence tables."""

from __future__ import annotations

from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Connection


class ImmutableRecordError(RuntimeError):
    """Raised before the ORM mutates or deletes immutable evidence."""


def _trigger_name(table_name: str, operation: str) -> str:
    return f"trg_{table_name}_immutable_{operation}"


def install_sqlite_immutable_triggers(connection: Connection, table_name: str) -> None:
    """Install idempotent SQLite UPDATE/DELETE guards for one evidence table."""
    if connection.dialect.name != "sqlite":
        return
    message = f"{table_name} is immutable"
    for operation in ("update", "delete"):
        connection.exec_driver_sql(
            f'''
            CREATE TRIGGER IF NOT EXISTS "{_trigger_name(table_name, operation)}"
            BEFORE {operation.upper()} ON "{table_name}"
            BEGIN
                SELECT RAISE(ABORT, '{message}');
            END
            '''
        )


def register_append_only_model(model: type[Any]) -> None:
    """Reject ORM mutation and add guards when SQLite creates the model table."""
    table_name = str(model.__tablename__)

    def reject_mutation(_mapper, _connection, target: Any) -> None:
        identifier = getattr(target, "id", None)
        suffix = f" {identifier}" if identifier is not None else ""
        raise ImmutableRecordError(f"{model.__name__}{suffix} is immutable")

    event.listen(model, "before_update", reject_mutation)
    event.listen(model, "before_delete", reject_mutation)

    def install_after_create(_target, connection: Connection, **_kwargs: Any) -> None:
        install_sqlite_immutable_triggers(connection, table_name)

    event.listen(model.__table__, "after_create", install_after_create)


__all__ = [
    "ImmutableRecordError",
    "install_sqlite_immutable_triggers",
    "register_append_only_model",
]
