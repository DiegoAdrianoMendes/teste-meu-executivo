import pytest

from app.schemas.flow import validate_flow_graph
from app.services.flow_node_registry import FLOW_EXTERNAL_NODE_TYPES, flow_node_catalog
from app.services.flow_runtime_primitives import apply_data_node


def _graph(node_type: str, config: dict):
    return [{"id": "n1", "type": node_type, "config": config}], []


def test_catalog_exposes_advanced_native_contracts_and_keeps_external_gate():
    catalog = {entry["type"]: entry for entry in flow_node_catalog()}
    assert {"form", "action", "end", "human_transfer", "landing_page", "llm"} <= set(catalog)
    assert catalog["form"]["side_effect"] == "none"
    assert catalog["action"]["execution_mode"] == "gated_external"
    assert "action" in FLOW_EXTERNAL_NODE_TYPES


def test_form_and_landing_page_have_deterministic_local_preview_effects():
    validate_flow_graph(*_graph("form", {"fields": [{"name": "nome", "type": "text"}]}))
    context = {}
    form_trace = apply_data_node(
        "form", {"fields": [{"name": "nome", "type": "text"}], "submit_label": "Enviar dados"}, context
    )
    assert form_trace["form"]["status"] == "aguardando_envio"
    assert context["pendingForm"]["submitLabel"] == "Enviar dados"

    validate_flow_graph(*_graph("landing_page", {"url": "/agendar", "link_title": "Agendar"}))
    link_trace = apply_data_node("landing_page", {"url": "/agendar"}, context)
    assert link_trace["link"]["url"] == "/agendar"


@pytest.mark.parametrize(
    ("node_type", "config"),
    [
        ("form", {"fields": []}),
        ("form", {"fields": [{"name": "x"}, {"name": "x"}]}),
        ("action", {"connector": "webhook"}),
        ("human_transfer", {}),
        ("llm", {}),
        ("landing_page", {"url": "javascript:alert(1)"}),
    ],
)
def test_advanced_nodes_fail_closed_when_configuration_is_incomplete(node_type, config):
    with pytest.raises(ValueError):
        validate_flow_graph(*_graph(node_type, config))


def test_terminal_node_records_completion_without_following_edges():
    context = {}
    trace = apply_data_node("end", {"reason": "Concluído com sucesso"}, context)
    assert trace == {"status": "completed", "reason": "Concluído com sucesso"}
    assert context["flowEnded"]["status"] == "completed"
