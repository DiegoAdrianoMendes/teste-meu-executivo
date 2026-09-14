from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects import postgresql, sqlite

from app.models.flow import FlowRun
from app.workers import fanout
from app.workers.flow_tasks import (
    FLOW_ACTIVE_EXTERNAL_INTENT_KEY,
    _flow_recovery_work_predicate,
)


def test_batch_limits_are_bounded_and_workspace_ids_are_unique():
    workspace_ids = [uuid4(), uuid4(), uuid4()]

    assert fanout.bounded_batch_size(0) == 1
    assert fanout.bounded_batch_size(10_000) == fanout.MAX_WORKSPACE_BATCH_SIZE
    assert fanout.unique_workspace_ids(
        [workspace_ids[0], workspace_ids[0], *workspace_ids[1:]],
        limit=2,
    ) == workspace_ids[:2]


def test_fanout_metrics_emit_batch_and_amplification(monkeypatch):
    events: list[tuple[str, dict]] = []

    class RecordingLogger:
        def info(self, event: str, **fields):
            events.append((event, fields))

    monkeypatch.setattr(fanout, "logger", RecordingLogger())
    metrics = fanout.FanoutMetrics("test.fanout", 2)
    metrics.discovery_rows = 4
    metrics.discovered_workspaces = 2
    metrics.enqueued_workspaces = 2
    metrics.processed_items = 2
    metrics.emit()

    assert events[0][0] == "worker.fanout"
    assert events[0][1]["fanout_batch_limit"] == 2
    assert events[0][1]["fanout_batch_saturated"] is True
    assert events[0][1]["fanout_amplification"] == 0.5


def test_flow_recovery_discovery_compiles_to_native_json_for_both_dialects():
    postgres_statement = select(FlowRun.id).where(
        _flow_recovery_work_predicate(
            dialect_name="postgresql",
            available_before="2026-07-20T12:00:00+00:00",
        )
    )
    sqlite_statement = select(FlowRun.id).where(
        _flow_recovery_work_predicate(
            dialect_name="sqlite",
            available_before="2026-07-20T12:00:00+00:00",
        )
    )

    postgres_sql = str(postgres_statement.compile(dialect=postgresql.dialect()))
    postgres_compiled = postgres_statement.compile(dialect=postgresql.dialect())
    sqlite_sql = str(sqlite_statement.compile(dialect=sqlite.dialect()))
    assert "jsonb_each" in postgres_sql
    assert "CAST(flow_runs.context AS JSONB) ?" in postgres_sql
    intent_key_binds = [
        bind
        for bind in postgres_compiled.binds.values()
        if bind.value == FLOW_ACTIVE_EXTERNAL_INTENT_KEY
    ]
    assert intent_key_binds
    assert all(bind.type.python_type is str for bind in intent_key_binds)
    assert "json_each" in sqlite_sql
    assert "json_extract" in sqlite_sql
