"""
Flow Builder schemas
"""

import re
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from jsonschema import Draft202012Validator
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.base import CamelCaseSchema, WriteSchema
from app.services.flow_node_registry import (
    FLOW_NODE_SPEC_BY_TYPE,
    FLOW_NODE_TYPES,
    validate_condition_config,
)
from app.services.whatsapp.helpers import normalize_phone_number

FLOW_CONDITION_OPERATORS = frozenset(
    {"eq", "contains", "gt", "gte", "lt", "lte", "exists", "not_empty"}
)
FLOW_ERROR_STRATEGIES = frozenset({"fail", "retry", "route", "wait_human"})
FLOW_VALUE_TYPES = frozenset({"auto", "string", "number", "boolean", "json"})
FLOW_AGGREGATE_OPERATIONS = frozenset({"count", "sum", "first", "last", "join"})
FLOW_LINK_KINDS = frozenset(
    {"website", "booking", "proposal", "document", "checkout", "purchase", "custom"}
)
FLOW_PAYMENT_METHODS = frozenset({"pix", "boleto", "credit_card", "checkout", "any"})
SEQUENCE_TEMPLATE_STATUSES = frozenset({"approved", "pending"})
SEQUENCE_DELAY_UNITS = frozenset({"hours", "days"})
FLOW_SOCIAL_ACTIONS = frozenset(
    {"public_reply", "private_reply", "comment_like", "comment_hide", "comment_delete"}
)
FLOW_CARD_UPDATE_FIELDS = frozenset(
    {
        "column_id",
        "micro_status",
        "priority",
        "urgency",
        "tags",
        "demand_type",
        "lead_source",
        "health_subtheme",
        "health_subtheme_custom",
        "health_object_name",
        "next_action",
    }
)


def _graph_node_config(node: dict[str, Any]) -> dict[str, Any]:
    config = node.get("config")
    if isinstance(config, dict):
        return config
    data = node.get("data")
    if isinstance(data, dict):
        return data
    return {}


def _validate_sequence_stage_config(config: dict[str, Any], *, node_id: str) -> None:
    """Validate the additive sequence contract for scheduled-message nodes.

    Legacy scheduled nodes may still use only ``message`` and ``scheduled_at``.
    Once a node opts into sequence metadata, all safety-critical fields are
    validated here so an invalid stage cannot reach the worker.
    """
    sequence_opted_in = bool(
        config.get("sequence_stage")
        or config.get("template_name")
        or config.get("template_status")
        or config.get("bindings")
        or config.get("variable_bindings")
        or config.get("delay_value") is not None
        or config.get("delay_unit") is not None
        or config.get("send_now")
    )
    if not sequence_opted_in:
        return

    template_name = str(config.get("template_name") or "").strip()
    if not template_name:
        raise ValueError(f"Etapa agendada {node_id} exige template_name")
    template_status = str(config.get("template_status") or "").strip().lower()
    if template_status not in SEQUENCE_TEMPLATE_STATUSES:
        raise ValueError(
            f"Etapa agendada {node_id} exige template_status approved ou pending"
        )

    raw_bindings = config.get("bindings", config.get("variable_bindings"))
    if raw_bindings is None:
        raw_bindings = []
    if not isinstance(raw_bindings, list):
        raise ValueError(f"Etapa agendada {node_id} possui bindings inválidos")
    variables: set[str] = set()
    for index, binding in enumerate(raw_bindings):
        if not isinstance(binding, dict):
            raise ValueError(f"Etapa agendada {node_id} possui binding inválido em {index}")
        raw_scope = binding.get("scope", binding.get("component", "body"))
        if isinstance(raw_scope, dict):
            raw_scope = raw_scope.get("type")
        scope = str(raw_scope or "body").strip().lower()
        if scope != "body":
            raise ValueError(
                f"Etapa agendada {node_id} só suporta bindings do body; "
                f"scope '{scope}' requer suporte explícito"
            )
        variable = str(binding.get("variable") or binding.get("name") or "").strip()
        field_key = str(binding.get("fieldKey") or binding.get("field_key") or "").strip()
        if not variable or not field_key:
            raise ValueError(
                f"Etapa agendada {node_id} exige variable e fieldKey em cada binding"
            )
        if variable in variables:
            raise ValueError(f"Etapa agendada {node_id} possui variável duplicada: {variable}")
        variables.add(variable)

    message = str(config.get("message") or "")
    placeholders = set(re.findall(r"\{([A-Za-z_][A-Za-z0-9_.]*)\}", message))
    missing = sorted(placeholders - variables)
    extra = sorted(variables - placeholders)
    if missing or extra:
        details = []
        if missing:
            details.append(f"sem binding: {', '.join(missing)}")
        if extra:
            details.append(f"binding sem variável: {', '.join(extra)}")
        raise ValueError(
            f"Etapa agendada {node_id} possui bindings incompatíveis ({'; '.join(details)})"
        )

    send_now = config.get("send_now", False)
    if not isinstance(send_now, bool):
        raise ValueError(f"Etapa agendada {node_id} exige send_now booleano")
    has_new_delay = config.get("delay_value") is not None or config.get("delay_unit") is not None
    if has_new_delay:
        raw_value = config.get("delay_value")
        if (
            not isinstance(raw_value, int | float)
            or isinstance(raw_value, bool)
            or float(raw_value) < 0
        ):
            raise ValueError(f"Etapa agendada {node_id} exige delay_value não negativo")
        delay_unit = str(config.get("delay_unit") or "").strip().lower()
        if delay_unit not in SEQUENCE_DELAY_UNITS:
            raise ValueError(f"Etapa agendada {node_id} exige delay_unit hours ou days")
        if float(raw_value) == 0 and not send_now:
            raise ValueError(
                f"Etapa agendada {node_id} exige send_now=true para delay_value zero"
            )
    interrupt_on_reply = config.get("interrupt_on_reply", True)
    if not isinstance(interrupt_on_reply, bool):
        raise ValueError(f"Etapa agendada {node_id} exige interrupt_on_reply booleano")


def validate_flow_graph(
    nodes: list[dict[str, Any]] | None,
    edges: list[dict[str, Any]] | None,
    *,
    check_orphans: bool = True,
) -> None:
    """Valida invariantes estruturais que o worker pressupõe ao executar o grafo."""
    if nodes is not None and not isinstance(nodes, list):
        raise ValueError("nodes deve ser uma lista")
    if edges is not None and not isinstance(edges, list):
        raise ValueError("edges deve ser uma lista")

    node_ids: set[str] = set()
    for index, node in enumerate(nodes or []):
        if not isinstance(node, dict):
            raise ValueError(f"nodes[{index}] deve ser um objeto")
        node_id = node.get("id")
        if not isinstance(node_id, str) or not node_id.strip():
            raise ValueError(f"nodes[{index}].id deve ser uma string não vazia")
        if node_id in node_ids:
            raise ValueError(f"ID de nó duplicado: {node_id}")
        node_ids.add(node_id)

        node_type = node.get("type")
        if not isinstance(node_type, str) or node_type not in FLOW_NODE_TYPES:
            raise ValueError(f"Tipo de nó inválido em {node_id}: {node_type}")
        if node_type == "condition":
            # Registro v2: validação schema-driven co-localizada (só o nó
            # condition). Aditiva e fail-closed em tipo/enum, sem exigir
            # presença (required) — grafos legados seguem válidos.
            validate_condition_config(_graph_node_config(node), node_id=node_id)
        config = _graph_node_config(node)
        if node_type == "scheduled_message":
            _validate_sequence_stage_config(config, node_id=node_id)
        error_strategy = config.get("error_strategy")
        if error_strategy is not None and error_strategy not in FLOW_ERROR_STRATEGIES:
            raise ValueError(f"Estratégia de erro inválida em {node_id}: {error_strategy}")
        if node_type == "router":
            routes = config.get("routes")
            if not isinstance(routes, list) or len(routes) < 2:
                raise ValueError(f"Roteador {node_id} exige ao menos uma regra e um fallback")
            route_keys = [
                str(route.get("key") or "").strip() for route in routes if isinstance(route, dict)
            ]
            if len(route_keys) != len(routes) or any(not key for key in route_keys):
                raise ValueError(f"Roteador {node_id} possui rota sem chave")
            if len(route_keys) != len(set(route_keys)):
                raise ValueError(f"Roteador {node_id} possui chaves de rota duplicadas")
            fallback_key = str(config.get("fallback_route_key") or "fallback")
            if fallback_key not in route_keys:
                raise ValueError(f"Roteador {node_id} exige a rota fallback '{fallback_key}'")
            for route in routes:
                if not isinstance(route, dict) or str(route.get("key")) == fallback_key:
                    continue
                operator = route.get("operator", "eq")
                if operator not in FLOW_CONDITION_OPERATORS:
                    raise ValueError(f"Operador de rota inválido em {node_id}: {operator}")
        if node_type == "loop":
            if not str(config.get("collection_path") or "").strip():
                raise ValueError(f"Iterador {node_id} exige collection_path")
            raw_limit = config.get("max_iterations", 100)
            if not isinstance(raw_limit, int) or not 1 <= raw_limit <= 500:
                raise ValueError(f"Iterador {node_id} exige max_iterations entre 1 e 500")
        if node_type == "set_variable":
            if not str(config.get("variable_name") or "").strip():
                raise ValueError(f"Nó {node_id} exige variable_name")
            value_type = str(config.get("value_type") or "auto")
            if value_type not in FLOW_VALUE_TYPES:
                raise ValueError(f"Tipo de variável inválido em {node_id}: {value_type}")
        if node_type == "transform":
            mappings = config.get("mappings")
            if not isinstance(mappings, dict) or not mappings:
                raise ValueError(f"Transformação {node_id} exige mappings")
        if node_type == "aggregate":
            if (
                not str(config.get("source_path") or "").strip()
                or not str(config.get("target_variable") or "").strip()
            ):
                raise ValueError(f"Agregador {node_id} exige origem e variável de destino")
            operation = str(config.get("aggregate_operation") or "count")
            if operation not in FLOW_AGGREGATE_OPERATIONS:
                raise ValueError(f"Agregação inválida em {node_id}: {operation}")
        if node_type == "wait":
            raw_delay = config.get("delay_hours", 24)
            if not isinstance(raw_delay, int | float) or not 0 < float(raw_delay) <= 8760:
                raise ValueError(f"Espera {node_id} exige delay_hours entre 0 e 8760")
        if node_type == "link_action":
            link_kind = str(config.get("link_kind") or "website")
            if link_kind not in FLOW_LINK_KINDS:
                raise ValueError(f"Tipo de link inválido em {node_id}: {link_kind}")
            if not str(config.get("url") or "").strip():
                raise ValueError(f"Link {node_id} exige url")
        if node_type == "landing_page":
            if not str(config.get("url") or "").strip():
                raise ValueError(f"Página de destino {node_id} exige url")
            url = str(config.get("url") or "").strip()
            if not (url.startswith("https://") or url.startswith("http://") or url.startswith("/")):
                raise ValueError(f"Página de destino {node_id} exige http(s) ou rota interna")
        if node_type == "form":
            fields = config.get("fields")
            if not isinstance(fields, list) or not fields:
                raise ValueError(f"Formulário {node_id} exige fields")
            names: list[str] = []
            for field in fields:
                if not isinstance(field, dict) or not str(field.get("name") or "").strip():
                    raise ValueError(f"Formulário {node_id} possui campo sem name")
                name = str(field["name"]).strip()
                if name in names:
                    raise ValueError(f"Formulário {node_id} possui campo duplicado: {name}")
                names.append(name)
                if str(field.get("type") or "text") not in {
                    "text",
                    "long_text",
                    "number",
                    "boolean",
                    "choice",
                    "phone",
                    "email",
                    "file",
                }:
                    raise ValueError(f"Tipo de campo inválido no formulário {node_id}")
        if node_type == "action" and (
            not str(config.get("connector") or "").strip()
            or not str(config.get("operation") or "").strip()
        ):
            raise ValueError(f"Ação {node_id} exige connector e operation")
        if (
            node_type == "human_transfer"
            and not str(
                config.get("team") or config.get("queue") or config.get("reason") or ""
            ).strip()
        ):
            raise ValueError(f"Transferência humana {node_id} exige team, queue ou reason")
        if (
            node_type == "llm"
            and not str(config.get("prompt") or config.get("agent") or "").strip()
        ):
            raise ValueError(f"Modelo de IA {node_id} exige prompt ou agent")
        if node_type == "payment_request":
            method = str(config.get("payment_method") or "any")
            if method not in FLOW_PAYMENT_METHODS:
                raise ValueError(f"Forma de pagamento inválida em {node_id}: {method}")
            if not str(config.get("amount_expression") or "").strip():
                raise ValueError(f"Cobrança {node_id} exige amount_expression")
        if node_type == "social_action":
            social_action = str(config.get("social_action") or "")
            if social_action not in FLOW_SOCIAL_ACTIONS:
                raise ValueError(f"Ação social inválida em {node_id}: {social_action}")
            if (
                social_action in {"public_reply", "private_reply"}
                and not str(config.get("message") or "").strip()
            ):
                raise ValueError(f"Ação social {node_id} exige message")
        if node_type == "create_task" and not str(config.get("task_title") or "").strip():
            raise ValueError(f"Tarefa {node_id} exige task_title")
        if node_type == "update_card":
            updates = config.get("updates")
            if not isinstance(updates, dict) or not updates:
                raise ValueError(f"Atualização de card {node_id} exige updates")
            forbidden = sorted(set(updates) - FLOW_CARD_UPDATE_FIELDS)
            if forbidden:
                raise ValueError(
                    f"Atualização de card {node_id} contém campos não permitidos: {forbidden}"
                )
        if (
            node_type == "internal_note"
            and not str(config.get("note") or config.get("message") or "").strip()
        ):
            raise ValueError(f"Nota interna {node_id} exige conteúdo")
        if node_type == "audit_event" and not str(config.get("event_type") or "").strip():
            raise ValueError(f"Evento de auditoria {node_id} exige event_type")
        if node_type == "connector_action" and (
            not str(config.get("connector") or "").strip()
            or not str(config.get("operation") or "").strip()
        ):
            raise ValueError(f"Conector {node_id} exige connector e operation")

    edge_ids: set[str] = set()
    for index, edge in enumerate(edges or []):
        if not isinstance(edge, dict):
            raise ValueError(f"edges[{index}] deve ser um objeto")
        edge_id = edge.get("id")
        if not isinstance(edge_id, str) or not edge_id.strip():
            raise ValueError(f"edges[{index}].id deve ser uma string não vazia")
        if edge_id in edge_ids:
            raise ValueError(f"ID de edge duplicado: {edge_id}")
        edge_ids.add(edge_id)

        source = edge.get("source")
        target = edge.get("target")
        if not isinstance(source, str) or not source.strip():
            raise ValueError(f"edges[{index}].source deve ser uma string não vazia")
        if not isinstance(target, str) or not target.strip():
            raise ValueError(f"edges[{index}].target deve ser uma string não vazia")
        if check_orphans and source not in node_ids:
            raise ValueError(f"Edge {edge_id} referencia source órfão: {source}")
        if check_orphans and target not in node_ids:
            raise ValueError(f"Edge {edge_id} referencia target órfão: {target}")

        route_key = edge.get("route_key", edge.get("routeKey"))
        if route_key is not None and (not isinstance(route_key, str) or not route_key.strip()):
            raise ValueError(f"Edge {edge_id} possui route_key inválida")
        priority = edge.get("priority")
        if priority is not None and (not isinstance(priority, int) or priority < 0):
            raise ValueError(f"Edge {edge_id} possui prioridade inválida")


class FlowGraphIssue(BaseModel):
    code: str
    severity: Literal["error", "warning", "info"]
    message: str
    node_ids: list[str] = Field(default_factory=list)


class FlowGraphValidationRequest(BaseModel):
    nodes: list[dict[str, Any]] = Field(default_factory=list, max_length=500)
    edges: list[dict[str, Any]] = Field(default_factory=list, max_length=1500)


class FlowGraphValidationRead(BaseModel):
    valid: bool
    node_count: int
    edge_count: int
    reachable_node_count: int
    estimated_cost_units: int
    external_action_count: int
    human_gate_count: int
    issues: list[FlowGraphIssue]


def analyze_flow_graph(
    nodes: list[dict[str, Any]], edges: list[dict[str, Any]]
) -> FlowGraphValidationRead:
    """Compile a draft graph without executing tools or external effects.

    This deliberately complements ``validate_flow_graph``: the legacy validator
    preserves compatible persisted drafts while this compiler applies the
    stricter publication contract used by the editor and CI.
    """
    issues: list[FlowGraphIssue] = []
    try:
        validate_flow_graph(nodes, edges)
    except ValueError as error:
        return FlowGraphValidationRead(
            valid=False,
            node_count=len(nodes),
            edge_count=len(edges),
            reachable_node_count=0,
            estimated_cost_units=0,
            external_action_count=0,
            human_gate_count=0,
            issues=[FlowGraphIssue(code="invalid_graph", severity="error", message=str(error))],
        )

    by_id = {str(node["id"]): node for node in nodes}
    outgoing: dict[str, list[str]] = {node_id: [] for node_id in by_id}
    incoming: dict[str, int] = {node_id: 0 for node_id in by_id}
    for edge in edges:
        source, target = str(edge["source"]), str(edge["target"])
        outgoing[source].append(target)
        incoming[target] += 1

    trigger_roots = [
        node_id for node_id, node in by_id.items() if str(node.get("type")) == "trigger"
    ]
    roots = trigger_roots or [node_id for node_id, count in incoming.items() if count == 0]
    if nodes and not roots:
        issues.append(
            FlowGraphIssue(
                code="missing_entrypoint",
                severity="error",
                message="O fluxo não possui ponto de entrada identificável.",
            )
        )

    reachable: set[str] = set()
    stack = list(roots)
    while stack:
        node_id = stack.pop()
        if node_id in reachable:
            continue
        reachable.add(node_id)
        stack.extend(outgoing[node_id])
    unreachable = sorted(set(by_id) - reachable)
    if unreachable:
        issues.append(
            FlowGraphIssue(
                code="unreachable_nodes",
                severity="error",
                message="Existem nós inalcançáveis a partir da entrada do fluxo.",
                node_ids=unreachable,
            )
        )

    # Strongly connected components identify cycles. A cycle is publishable
    # only when it includes the explicit bounded loop node.
    index = 0
    indexes: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    component_stack: list[str] = []
    on_stack: set[str] = set()
    components: list[list[str]] = []

    def visit(node_id: str) -> None:
        nonlocal index
        indexes[node_id] = lowlinks[node_id] = index
        index += 1
        component_stack.append(node_id)
        on_stack.add(node_id)
        for target in outgoing[node_id]:
            if target not in indexes:
                visit(target)
                lowlinks[node_id] = min(lowlinks[node_id], lowlinks[target])
            elif target in on_stack:
                lowlinks[node_id] = min(lowlinks[node_id], indexes[target])
        if lowlinks[node_id] == indexes[node_id]:
            component: list[str] = []
            while component_stack:
                member = component_stack.pop()
                on_stack.remove(member)
                component.append(member)
                if member == node_id:
                    break
            components.append(component)

    for node_id in by_id:
        if node_id not in indexes:
            visit(node_id)
    for component in components:
        cyclic = len(component) > 1 or any(member in outgoing[member] for member in component)
        if cyclic and not any(str(by_id[member].get("type")) == "loop" for member in component):
            issues.append(
                FlowGraphIssue(
                    code="unbounded_cycle",
                    severity="error",
                    message="Ciclo sem nó de repetição limitado por max_iterations.",
                    node_ids=sorted(component),
                )
            )

    external_count = 0
    human_count = 0
    estimated_cost = 0
    for node_id, node in by_id.items():
        node_type = str(node.get("type"))
        spec = FLOW_NODE_SPEC_BY_TYPE[node_type]
        config = _graph_node_config(node)
        schema = spec.get("config_schema")
        if not isinstance(schema, dict):
            issues.append(
                FlowGraphIssue(
                    code="missing_node_schema",
                    severity="error",
                    message="O tipo de nó não possui contrato de configuração.",
                    node_ids=[node_id],
                )
            )
        else:
            validation_errors = sorted(
                Draft202012Validator(schema).iter_errors(config),
                key=lambda error: tuple(str(part) for part in error.absolute_path),
            )
            for error in validation_errors:
                path = ".".join(str(part) for part in error.absolute_path)
                location = f" em '{path}'" if path else ""
                issues.append(
                    FlowGraphIssue(
                        code="invalid_node_config",
                        severity="error",
                        message=f"Configuração inválida{location}: {error.message}",
                        node_ids=[node_id],
                    )
                )
        external = (
            spec.get("side_effect") == "external" or spec.get("execution_mode") == "gated_external"
        )
        risk = str(spec.get("risk") or "low")
        if external:
            external_count += 1
            if spec.get("approval_policy") != "required":
                issues.append(
                    FlowGraphIssue(
                        code="external_without_approval",
                        severity="error",
                        message="Ação externa exige política de aprovação explícita.",
                        node_ids=[node_id],
                    )
                )
        if node_type.startswith("human_") or node_type in {"human_pause", "human_transfer"}:
            human_count += 1
        if risk in {"high", "critical"} and not (
            spec.get("human_fallback") or config.get("human_fallback") or external
        ):
            issues.append(
                FlowGraphIssue(
                    code="high_risk_without_human_fallback",
                    severity="error",
                    message="Nó de risco elevado exige fallback humano.",
                    node_ids=[node_id],
                )
            )
        estimated_cost += 10 if node_type in {"agent", "llm"} else (3 if external else 1)

    return FlowGraphValidationRead(
        valid=not any(issue.severity == "error" for issue in issues),
        node_count=len(nodes),
        edge_count=len(edges),
        reachable_node_count=len(reachable),
        estimated_cost_units=estimated_cost,
        external_action_count=external_count,
        human_gate_count=human_count,
        issues=issues,
    )


def ensure_publishable_flow_graph(
    nodes: list[dict[str, Any]], edges: list[dict[str, Any]]
) -> None:
    """Fail closed when a graph is activated outside the visual editor."""
    report = analyze_flow_graph(nodes, edges)
    if report.valid:
        return
    details = "; ".join(
        f"{issue.code}: {issue.message}" for issue in report.issues if issue.severity == "error"
    )
    raise ValueError(f"Flow não pode ser ativado: {details}")


class FlowNodeConfig(BaseModel):
    """Configuração de um nó do flow"""

    message: str | None = None  # send_message: texto com {variaveis}
    delay_hours: float | None = None  # wait: horas de espera
    condition: str | None = None  # wait/condition: no_response | always
    field: str | None = None  # condition: campo a avaliar
    operator: str | None = None  # condition: eq | gt/gte | lt/lte | contains | exists | not_empty
    value: str | None = None  # condition: valor a comparar
    agent: str | None = None  # agent: klm_sdr | klm_analyst | klm_drafter
    doc_type: str | None = None  # zapsign: procuracao | contrato | outros
    provider: str | None = None  # integração: zapsign | docusign | manual
    auth_mode: str | None = None  # assinatura: assinaturaTela-tokenEmail | tokenSms | ...
    reason: str | None = None  # human_pause: instrução para o advogado
    scheduled_time: str | None = None  # scheduled: HH:MM
    scheduled_at: str | None = None  # scheduled_message: HH:MM (formato atual do editor)
    # Sequence stage contract (all fields are additive to legacy flow nodes).
    sequence_stage: bool | None = None
    template_status: Literal["approved", "pending"] | None = None
    template_id: UUID | None = None
    whatsapp_connection_id: UUID | None = None
    template_language: str | None = None
    bindings: list[dict[str, Any]] | None = None
    variable_bindings: list[dict[str, Any]] | None = None
    delay_value: float | None = Field(default=None, ge=0)
    delay_unit: Literal["hours", "days"] | None = None
    send_now: bool = False
    interrupt_on_reply: bool = True
    tariff: Any = None
    column_id: str | None = None  # trigger: coluna alvo (column_moved)
    event: str | None = None  # trigger: new_lead | column_moved | etc
    trigger_type: str | None = None
    keywords: list[str] | None = None
    excluded_keywords: list[str] | None = None
    keyword_mode: str | None = None
    match_mode: str | None = None
    source_ids: list[str] | None = None
    case_sensitive: bool | None = None
    first_level_only: bool | None = None
    follower_status: str | None = None
    verified_status: str | None = None
    messaging_window: str | None = None
    data_availability_policy: str | None = None
    required_capability: str | None = None
    stage_key: str | None = None  # agent: etapa base para política do workspace
    prompt: str | None = None  # agent: instruções específicas do nó
    model: str | None = None  # agent: model override
    context_mode: str | None = None  # agent: essential | balanced | full
    use_rag: bool | None = None  # agent: usar recuperação do card
    multimodal_enabled: bool | None = None
    human_review_required: bool | None = None
    template_name: str | None = None
    integration_notes: str | None = None
    routes: list[dict[str, Any]] | None = None
    fallback_route_key: str | None = None
    collection_path: str | None = None
    item_variable: str | None = None
    index_variable: str | None = None
    max_iterations: int | None = Field(default=None, ge=1, le=500)
    merge_mode: str | None = None
    variable_name: str | None = None
    value_expression: Any = None
    value_type: str | None = None
    mappings: dict[str, Any] | None = None
    source_path: str | None = None
    target_variable: str | None = None
    aggregate_operation: str | None = None
    separator: str | None = None
    link_kind: str | None = None
    fields: list[dict[str, Any]] | None = None
    submit_label: str | None = None
    url: str | None = None
    link_title: str | None = None
    target: str | None = None
    open_in_new_tab: bool | None = None
    payment_provider: str | None = None
    payment_method: str | None = None
    amount_expression: Any = None
    due_days: int | None = Field(default=None, ge=0, le=3650)
    payment_description: str | None = None
    social_action: str | None = None
    target_external_id: str | None = None
    task_title: str | None = None
    task_description: str | None = None
    task_priority: str | None = None
    updates: dict[str, Any] | None = None
    note: str | None = None
    event_type: str | None = None
    summary: str | None = None
    connector: str | None = None
    operation: str | None = None
    input_mapping: dict[str, Any] | None = None
    output_mapping: dict[str, Any] | None = None
    timeout_seconds: int | None = Field(default=None, ge=1, le=900)
    error_strategy: str | None = None
    max_retries: int | None = Field(default=None, ge=0, le=10)
    retry_delay_seconds: int | None = Field(default=None, ge=0, le=86400)
    clear_error: bool | None = None
    team: str | None = None
    queue: str | None = None
    end_status: str | None = None


class FlowNode(BaseModel):
    id: str
    # O catálogo autoritativo é app.services.flow_node_registry.FLOW_NODE_SPECS.
    type: str
    config: FlowNodeConfig = Field(default_factory=FlowNodeConfig)
    position: dict[str, float] = Field(default_factory=lambda: {"x": 0, "y": 0})
    label: str | None = None


class FlowEdge(BaseModel):
    id: str
    source: str
    target: str
    label: str | None = None  # "Sim" | "Não" para condition nodes
    condition_value: str | None = None  # "true" | "false"
    route_key: str | None = None
    priority: int | None = Field(default=None, ge=0)
    is_fallback: bool = False
    filter: dict[str, Any] | None = None


class FlowNodePortRead(BaseModel):
    key: str
    label: str


class FlowNodeCatalogEntryRead(BaseModel):
    type: str
    label: str
    description: str
    category: str
    execution_mode: Literal["native", "control", "gated_external"]
    side_effect: Literal["none", "local", "external"]
    input_ports: list[FlowNodePortRead]
    output_ports: list[FlowNodePortRead]
    supports_dry_run: bool
    supports_error_route: bool
    config_version: int
    # Registro v2 (aditivo, opcional): contrato schema-driven co-localizado.
    # Serializado como schema/uischema/outputSchema (convenção JsonForms);
    # ausente nos tipos ainda no contrato v1 (ver response_model_exclude_none).
    config_schema: dict[str, Any] | None = Field(default=None, alias="schema")
    ui_schema: dict[str, Any] | None = Field(default=None, alias="uischema")
    output_schema: dict[str, Any] | None = Field(default=None, alias="outputSchema")
    availability: Literal["available", "unavailable"] | None = None
    unavailable_reason: str | None = None
    # S19: o frontend lê `availabilityReason` (FlowBuilder, painel de operações);
    # o motivo nunca chegava. Espelho aditivo, preenchido a partir de
    # `unavailable_reason` quando não vier explícito.
    availability_reason: str | None = Field(default=None, alias="availabilityReason")
    capability_key: str | None = None
    risk: Literal["low", "medium", "high", "critical"] | None = None
    required_permission: str | None = None
    approval_policy: str | None = None
    idempotency_policy: str | None = None
    retry_policy: str | None = None
    compensation_policy: str | None = None
    human_fallback: str | None = None

    model_config = ConfigDict(populate_by_name=True)

    @model_validator(mode="after")
    def _mirror_availability_reason(self) -> "FlowNodeCatalogEntryRead":
        if self.availability_reason is None and self.unavailable_reason:
            self.availability_reason = self.unavailable_reason
        return self


class FlowNodeCatalogRead(BaseModel):
    # Marcador de versão da leitura do catálogo. Subiu para 2 ao introduzir o
    # contrato schema-driven do nó condition (aditivo, sem quebrar clientes v1).
    schema_version: int = 2
    node_count: int
    external_node_types: list[str]
    nodes: list[FlowNodeCatalogEntryRead]


class FlowCopilotAttachment(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    media_type: str = Field(min_length=1, max_length=160)
    size_bytes: int = Field(ge=0, le=20_000_000)
    text_excerpt: str | None = Field(default=None, max_length=8000)

    @model_validator(mode="after")
    def reject_audio(self) -> "FlowCopilotAttachment":
        if self.media_type.casefold().startswith("audio/"):
            raise ValueError("O copiloto de flows não aceita arquivos de áudio")
        return self


class FlowCopilotPlanRequest(BaseModel):
    instruction: str = Field(min_length=3, max_length=12000)
    flow_id: UUID | None = None
    practice_area: str = Field(default="universal", max_length=160)
    current_nodes: list[dict[str, Any]] = Field(default_factory=list, max_length=200)
    current_edges: list[dict[str, Any]] = Field(default_factory=list, max_length=400)
    attachments: list[FlowCopilotAttachment] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def enforce_attachment_budget(self) -> "FlowCopilotPlanRequest":
        if sum(item.size_bytes for item in self.attachments) > 40_000_000:
            raise ValueError("Os anexos do copiloto não podem exceder 40 MB por solicitação")
        return self


class FlowCopilotPlanRead(BaseModel):
    proposal_id: UUID
    status: Literal["awaiting_approval"]
    engine: str
    summary: str
    rationale: list[str]
    suggestions: list[str]
    questions: list[str]
    warnings: list[str]
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    added_node_ids: list[str]
    playbook_name: str | None = None
    playbook_version: int | None = None
    account_context_used: bool
    attachment_count: int
    approval_required: bool = True
    external_actions_executed: int = 0
    generated_at: datetime


class FlowCreate(BaseModel):
    name: str
    description: str | None = None
    practice_area: str = "universal"
    trigger: str = "manual"
    trigger_config: dict[str, Any] | None = None
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)
    is_active: bool = True

    @model_validator(mode="after")
    def validate_graph(self) -> "FlowCreate":
        validate_flow_graph(self.nodes, self.edges)
        return self


class FlowUpdate(BaseModel):
    # `version` e `current_revision` são aliases explícitos para clientes que
    # já fazem round-trip do FlowRead. O serviço só infere ausência no baseline.
    expected_revision: int | None = Field(
        default=None,
        ge=1,
        validation_alias=AliasChoices(
            "expected_revision",
            "current_revision",
            "version",
        ),
    )
    name: str | None = None
    description: str | None = None
    practice_area: str | None = None
    trigger: str | None = None
    trigger_config: dict[str, Any] | None = None
    nodes: list[dict[str, Any]] | None = None
    edges: list[dict[str, Any]] | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def validate_graph(self) -> "FlowUpdate":
        validate_flow_graph(
            self.nodes,
            self.edges,
            check_orphans=self.nodes is not None and self.edges is not None,
        )
        return self


class FlowRead(BaseModel):
    id: UUID
    workspace_id: UUID | None
    name: str
    description: str | None
    practice_area: str
    is_template: bool
    is_active: bool
    trigger: str
    trigger_config: dict[str, Any] | None
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    run_count: int
    current_revision_id: UUID | None
    current_revision: int
    version: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class FlowRevisionRead(BaseModel):
    id: UUID
    flow_id: UUID
    revision_number: int
    snapshot_schema_version: int
    snapshot: dict[str, Any]
    snapshot_hash: str
    source: str
    created_by_user_id: UUID | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class FlowRunRead(BaseModel):
    id: UUID
    flow_id: UUID
    revision_id: UUID
    matter_id: UUID
    status: str
    current_node_id: str | None
    context: dict[str, Any] | None
    pause_reason: str | None
    started_at: datetime
    completed_at: datetime | None
    paused_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class TaskFlowRunCreate(WriteSchema):
    flow_id: UUID


class TaskFlowRunRead(CamelCaseSchema):
    id: UUID
    flow_id: UUID
    revision_id: UUID
    matter_id: UUID
    task_id: UUID
    flow_name: str
    status: str
    current_node_id: str | None
    pause_reason: str | None
    error_message: str | None
    started_at: datetime
    completed_at: datetime | None
    paused_at: datetime | None


class MatterFlowJourneyStepRead(BaseModel):
    node_id: str
    node_type: str
    label: str
    state: Literal["completed", "current", "upcoming", "failed", "waiting_human"]
    step: int | None = None
    occurred_at: datetime | None = None
    decision: dict[str, Any] | None = None


class MatterFlowJourneyRead(BaseModel):
    run_id: UUID
    flow_id: UUID
    flow_name: str
    revision: int
    status: str
    progress_percent: float
    completed_steps: int
    total_steps: int
    current_node_id: str | None
    current_step_label: str | None
    pause_reason: str | None
    error_message: str | None
    started_at: datetime
    completed_at: datetime | None
    paused_at: datetime | None
    elapsed_seconds: int
    timeline: list[MatterFlowJourneyStepRead]


class MatterFlowJourneyKpisRead(BaseModel):
    total_runs: int
    active_runs: int
    waiting_human_runs: int
    completed_runs: int
    failed_runs: int
    completion_rate: float
    overall_progress_percent: float
    average_duration_seconds: int | None
    completed_steps: int
    total_steps: int


class MatterFlowJourneyOverviewRead(BaseModel):
    matter_id: UUID
    generated_at: datetime
    kpis: MatterFlowJourneyKpisRead
    journeys: list[MatterFlowJourneyRead]


class FlowRunRequest(BaseModel):
    matter_id: UUID
    thread_id: UUID | None = None
    context: dict[str, Any] | None = None  # Variáveis iniciais: {nome, phone, etc}


class FlowExternalActionReconciliationRequest(BaseModel):
    outcome: Literal["delivered", "not_delivered"]
    provider_reference: str | None = Field(default=None, max_length=255)
    note: str | None = Field(default=None, max_length=1000)


class FlowDryRunRequest(BaseModel):
    flow_id: UUID
    matter_id: UUID
    revision: int | None = Field(default=None, ge=1)
    context: dict[str, Any] = Field(default_factory=dict)
    max_steps: int = Field(default=100, ge=1, le=200)


class FlowDryRunRead(BaseModel):
    flow_id: UUID
    revision_id: UUID
    revision: int
    version: int
    matter_id: UUID
    status: str
    trace: list[dict[str, Any]]
    max_steps: int
    external_actions_executed: int = 0
    context: dict[str, Any]


class ScheduledMessageCreate(BaseModel):
    matter_id: UUID | None = None
    contact_phone: str
    message: str
    scheduled_at: datetime
    flow_run_id: UUID | None = None

    @field_validator("contact_phone")
    @classmethod
    def normalize_contact_phone(cls, value: str) -> str:
        normalized = normalize_phone_number(value)
        if normalized is None:
            raise ValueError(
                "Informe DDI, DDD e número válidos. Exemplo aceito pela Meta: 5561999999999"
            )
        return normalized


class ScheduledMessageReconciliationRequest(BaseModel):
    outcome: Literal["delivered", "not_delivered"]
    provider_reference: str | None = Field(default=None, max_length=255)
    retry_at: datetime | None = None
    note: str | None = Field(default=None, max_length=1000)


class ScheduledMessageRead(BaseModel):
    id: UUID
    flow_run_id: UUID | None
    stage_id: str | None = None
    template_id: UUID | None = None
    whatsapp_connection_id: UUID | None = None
    template_name: str | None = None
    template_language: str | None = None
    template_status: str | None = None
    template_bindings: list[dict[str, Any]] | None = None
    delay_value: float | None = None
    delay_unit: str | None = None
    send_now: bool = False
    interrupt_on_reply: bool = True
    pending_grace_until: datetime | None = None
    pending_retry_at: datetime | None = None
    tariff_status: str = "unverified"
    tariff_amount: float | None = None
    matter_id: UUID | None = None
    contact_phone: str
    message: str
    scheduled_at: datetime
    sent_at: datetime | None
    status: str
    error_message: str | None
    delivery_attempt_count: int
    provider_supports_idempotency: bool
    provider_message_id: str | None
    delivery_uncertain_at: datetime | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
