"""Durable, idempotent timeline entries for flow executions."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def _step_number(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def record_flow_trace(
    run: Any,
    *,
    node_id: str,
    node_type: str,
    label: str,
    step: int,
    status: str,
    decision: dict[str, Any] | None = None,
    occurred_at: datetime | None = None,
) -> dict[str, Any]:
    """Upsert one execution step without duplicating retries of the same node."""
    now = occurred_at or datetime.now(UTC)
    context = dict(run.context or {})
    raw_trace = context.get("trace")
    trace = (
        [dict(item) for item in raw_trace if isinstance(item, dict)]
        if isinstance(raw_trace, list)
        else []
    )
    trace_key = (max(1, int(step)), str(node_id))
    entry_index = next(
        (
            index
            for index, item in enumerate(trace)
            if (_step_number(item.get("step")), str(item.get("nodeId") or "")) == trace_key
        ),
        None,
    )
    previous = trace[entry_index] if entry_index is not None else {}
    safe_decision = (
        {
            key: decision[key]
            for key in (
                "field",
                "operator",
                "branch",
                "strategy",
                "attempt",
                "maxRetries",
                "retryAt",
                "errorType",
                "errorRoute",
            )
            if key in decision
        }
        if decision
        else None
    )
    entry = {
        **previous,
        "step": trace_key[0],
        "nodeId": trace_key[1],
        "type": str(node_type or previous.get("type") or "step"),
        "label": str(label or previous.get("label") or node_id),
        "status": status,
        "occurredAt": previous.get("occurredAt") or now.isoformat(),
        "updatedAt": now.isoformat(),
    }
    if status in {"completed", "failed"}:
        entry["completedAt"] = now.isoformat()
    else:
        entry.pop("completedAt", None)
    if safe_decision:
        entry["decision"] = safe_decision
    if entry_index is None:
        trace.append(entry)
    else:
        trace[entry_index] = entry
    context["trace"] = trace
    run.context = context
    return entry


def transition_flow_trace(
    run: Any,
    *,
    node_id: str,
    status: str,
    occurred_at: datetime | None = None,
) -> bool:
    """Transition the latest trace entry for a node when a paused step settles."""
    context = dict(run.context or {})
    raw_trace = context.get("trace")
    if not isinstance(raw_trace, list):
        return False
    trace = [dict(item) for item in raw_trace if isinstance(item, dict)]
    matching = [
        index for index, item in enumerate(trace) if str(item.get("nodeId") or "") == str(node_id)
    ]
    if not matching:
        return False
    index = matching[-1]
    now = occurred_at or datetime.now(UTC)
    trace[index]["status"] = status
    trace[index]["updatedAt"] = now.isoformat()
    if status in {"completed", "failed"}:
        trace[index]["completedAt"] = now.isoformat()
    context["trace"] = trace
    run.context = context
    return True
