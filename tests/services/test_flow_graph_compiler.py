from app.schemas.flow import analyze_flow_graph


def node(node_id: str, node_type: str, **config):
    return {"id": node_id, "type": node_type, "config": config}


def edge(edge_id: str, source: str, target: str):
    return {"id": edge_id, "source": source, "target": target}


def test_compiler_rejects_unreachable_nodes():
    report = analyze_flow_graph(
        [
            node("start", "trigger"),
            node("visible", "audit_event", event_type="visible"),
            node("orphan", "audit_event", event_type="orphan"),
        ],
        [edge("e1", "start", "visible")],
    )

    assert report.valid is False
    issue = next(item for item in report.issues if item.code == "unreachable_nodes")
    assert issue.node_ids == ["orphan"]


def test_compiler_rejects_cycle_without_bounded_loop():
    report = analyze_flow_graph(
        [
            node("start", "trigger"),
            node("a", "audit_event", event_type="a"),
            node("b", "audit_event", event_type="b"),
        ],
        [
            edge("e1", "start", "a"),
            edge("e2", "a", "b"),
            edge("e3", "b", "a"),
        ],
    )

    assert report.valid is False
    issue = next(item for item in report.issues if item.code == "unbounded_cycle")
    assert issue.node_ids == ["a", "b"]


def test_compiler_accepts_cycle_with_explicit_bounded_loop():
    report = analyze_flow_graph(
        [
            node("start", "trigger"),
            node("loop", "loop", collection_path="items", max_iterations=20),
            node("work", "audit_event", event_type="iteration", summary="Iteração concluída"),
        ],
        [
            edge("e1", "start", "loop"),
            edge("e2", "loop", "work"),
            edge("e3", "work", "loop"),
        ],
    )

    assert report.valid is True
    assert report.reachable_node_count == 3
    assert report.estimated_cost_units == 3


def test_compiler_reports_external_actions_and_human_gates():
    report = analyze_flow_graph(
        [
            node("start", "trigger"),
            node("approval", "human_approval", required_roles=["owner"]),
            node(
                "payment",
                "payment_request",
                amount_expression="invoice.total",
                payment_provider="internal",
                payment_method="pix",
            ),
        ],
        [edge("e1", "start", "approval"), edge("e2", "approval", "payment")],
    )

    assert report.valid is True
    assert report.external_action_count == 1
    assert report.human_gate_count == 1
    assert report.estimated_cost_units >= 5


def test_compiler_rejects_invalid_config_for_non_condition_node():
    report = analyze_flow_graph(
        [
            node("start", "trigger"),
            node("finance", "finance_read", operation="not-supported"),
        ],
        [edge("e1", "start", "finance")],
    )

    assert report.valid is False
    issue = next(item for item in report.issues if item.code == "invalid_node_config")
    assert issue.node_ids == ["finance"]
