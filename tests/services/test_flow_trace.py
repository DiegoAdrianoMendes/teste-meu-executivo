from types import SimpleNamespace

from app.services.flow_trace import record_flow_trace, transition_flow_trace


def test_flow_trace_upserts_retried_step_without_duplicate_or_sensitive_values():
    run = SimpleNamespace(context={})

    record_flow_trace(
        run,
        node_id="condition-1",
        node_type="condition",
        label="Lead qualificado",
        step=2,
        status="waiting_human",
        decision={
            "field": "lead_score",
            "operator": "gte",
            "branch": "true",
            "actual": 91,
            "expected": 80,
        },
    )
    record_flow_trace(
        run,
        node_id="condition-1",
        node_type="condition",
        label="Lead qualificado",
        step=2,
        status="completed",
    )

    trace = run.context["trace"]
    assert len(trace) == 1
    assert trace[0]["status"] == "completed"
    assert trace[0]["decision"] == {
        "field": "lead_score",
        "operator": "gte",
        "branch": "true",
    }
    assert "completedAt" in trace[0]


def test_flow_trace_transitions_latest_repeated_node_only():
    run = SimpleNamespace(
        context={
            "trace": [
                {"step": 1, "nodeId": "loop", "status": "completed"},
                {"step": 3, "nodeId": "loop", "status": "waiting_human"},
            ]
        }
    )

    assert transition_flow_trace(run, node_id="loop", status="completed") is True
    assert [item["status"] for item in run.context["trace"]] == [
        "completed",
        "completed",
    ]
