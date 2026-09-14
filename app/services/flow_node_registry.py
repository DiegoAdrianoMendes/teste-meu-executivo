"""Catálogo autoritativo dos nós executáveis pelo runtime de flows do KLM."""

from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict


class FlowNodePort(TypedDict):
    key: str
    label: str


class FlowNodeSpec(TypedDict):
    type: str
    label: str
    description: str
    category: str
    execution_mode: Literal["native", "control", "gated_external"]
    side_effect: Literal["none", "local", "external"]
    input_ports: list[FlowNodePort]
    output_ports: list[FlowNodePort]
    supports_dry_run: bool
    supports_error_route: bool
    config_version: int
    # Registro v2 (aditivo; hoje só o nó 'condition'): contrato de configuração
    # co-localizado por tipo (padrão Node-RED). O editor renderiza o formulário
    # via JsonForms a partir de {config_schema, ui_schema}, e outputSchema
    # descreve as portas de saída. Chaves ausentes = nó ainda no contrato v1.
    config_schema: NotRequired[dict[str, Any]]
    ui_schema: NotRequired[dict[str, Any]]
    output_schema: NotRequired[dict[str, Any]]
    # A declared node can be deliberately unavailable when its canonical
    # domain binding has not been implemented.  Editors must not present it
    # as runnable merely because it is known to the schema validator.
    availability: NotRequired[Literal["available", "unavailable"]]
    unavailable_reason: NotRequired[str]
    # V3 execution contract (defaults are conservative for legacy nodes).
    capability_key: NotRequired[str]
    risk: NotRequired[Literal["low", "medium", "high", "critical"]]
    required_permission: NotRequired[str | None]
    approval_policy: NotRequired[str]
    idempotency_policy: NotRequired[str]
    retry_policy: NotRequired[str]
    compensation_policy: NotRequired[str | None]
    human_fallback: NotRequired[str | None]


def _spec(
    node_type: str,
    label: str,
    description: str,
    category: str,
    *,
    execution_mode: Literal["native", "control", "gated_external"] = "native",
    side_effect: Literal["none", "local", "external"] = "none",
    input_ports: tuple[tuple[str, str], ...] = (("input", "Entrada"),),
    output_ports: tuple[tuple[str, str], ...] = (("output", "Próximo"),),
    supports_error_route: bool = True,
    config_version: int = 1,
    config_schema: dict[str, Any] | None = None,
    ui_schema: dict[str, Any] | None = None,
    output_schema: dict[str, Any] | None = None,
) -> FlowNodeSpec:
    spec: FlowNodeSpec = {
        "type": node_type,
        "label": label,
        "description": description,
        "category": category,
        "execution_mode": execution_mode,
        "side_effect": side_effect,
        "input_ports": [{"key": key, "label": port_label} for key, port_label in input_ports],
        "output_ports": [{"key": key, "label": port_label} for key, port_label in output_ports],
        "supports_dry_run": True,
        "supports_error_route": supports_error_route,
        "config_version": config_version,
    }
    # Anexa o contrato schema-driven só quando o tipo já foi migrado para v2.
    if config_schema is not None:
        spec["config_schema"] = config_schema
        spec["ui_schema"] = ui_schema if ui_schema is not None else {}
        spec["output_schema"] = output_schema if output_schema is not None else {}
    spec["capability_key"] = node_type
    spec["risk"] = (
        "high" if side_effect == "external" else ("medium" if side_effect == "local" else "low")
    )
    spec["required_permission"] = None
    spec["approval_policy"] = "required" if execution_mode == "gated_external" else "none"
    spec["idempotency_policy"] = "required" if side_effect != "none" else "not_applicable"
    spec["retry_policy"] = "transient_only"
    spec["compensation_policy"] = "manual" if side_effect != "none" else None
    spec["human_fallback"] = "human_review" if execution_mode == "gated_external" else None
    return spec


# ---------------------------------------------------------------------------
# Registro v2 — contrato schema-driven do nó 'condition' (co-localizado).
#
# Fonte única dos operadores de condição reconhecidos pelo runtime
# (app/workers/flow_tasks.py::_evaluate_condition). O conjunto espelhado em
# app/schemas/flow.py::FLOW_CONDITION_OPERATORS deve permanecer idêntico
# (garantido por teste de não-divergência).
# ---------------------------------------------------------------------------
CONDITION_OPERATORS: tuple[str, ...] = (
    "eq",
    "contains",
    "gt",
    "gte",
    "lt",
    "lte",
    "exists",
    "not_empty",
)

# Operadores unários: dispensam o campo "value" (o runtime só olha o campo).
CONDITION_UNARY_OPERATORS: frozenset[str] = frozenset({"exists", "not_empty"})

_CONDITION_OPERATOR_SET: frozenset[str] = frozenset(CONDITION_OPERATORS)

# Escalares aceitos como "field"/"value" na leitura de grafos.
_CONDITION_SCALAR_TYPES: tuple[type, ...] = (str, int, float, bool)

CONDITION_CONFIG_SCHEMA: dict[str, Any] = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "title": "Condição",
    "description": "Decisão binária por campo e operador tipado.",
    "properties": {
        "field": {
            "type": "string",
            "title": "Campo",
            "description": "Caminho no contexto da execução a avaliar (ex.: lead.status).",
            "minLength": 1,
        },
        "operator": {
            "type": "string",
            "title": "Operador",
            "description": "Comparação aplicada ao campo.",
            "enum": list(CONDITION_OPERATORS),
            "default": "eq",
        },
        "value": {
            "type": "string",
            "title": "Valor de comparação",
            "description": "Valor comparado ao campo. Ignorado por 'exists' e 'not_empty'.",
        },
    },
    "required": ["field", "operator"],
    # Grafos legados podem trazer campos auxiliares (ex.: error_strategy);
    # não recusar por propriedades extras.
    "additionalProperties": True,
}

CONDITION_CONFIG_UISCHEMA: dict[str, Any] = {
    "type": "VerticalLayout",
    "elements": [
        {
            "type": "Control",
            "scope": "#/properties/field",
            "options": {"placeholder": "lead.status"},
        },
        {"type": "Control", "scope": "#/properties/operator"},
        {
            "type": "Control",
            "scope": "#/properties/value",
            # Esconde "value" quando o operador é unário (exists/not_empty).
            "rule": {
                "effect": "HIDE",
                "condition": {
                    "scope": "#/properties/operator",
                    "schema": {"enum": ["exists", "not_empty"]},
                },
            },
        },
    ],
}

CONDITION_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "title": "Saídas da condição",
    "description": "Portas de saída avaliadas pelo runtime.",
    "properties": {
        "true": {
            "type": "boolean",
            "title": "Sim",
            "description": "Rota seguida quando a condição é satisfeita.",
        },
        "false": {
            "type": "boolean",
            "title": "Não",
            "description": "Rota seguida quando a condição não é satisfeita.",
        },
    },
    "ports": [
        {"key": "true", "label": "Sim"},
        {"key": "false", "label": "Não"},
    ],
}


def validate_condition_config(config: Any, *, node_id: str) -> None:
    """Valida a config do nó 'condition' contra CONDITION_CONFIG_SCHEMA.

    Fail-closed nas violações de tipo/enum (operador fora do conjunto, campo ou
    valor não escalar), sempre com erro claro. Deliberadamente NÃO exige a
    presença dos campos `required` do schema: obrigatoriedade é contrato de
    autoria (guiada pelo painel JsonForms), não de leitura. Assim grafos já
    persistidos, que podem não trazer `field`/`value`, seguem válidos
    (compat de leitura).
    """
    if not isinstance(config, dict):
        return

    operator = config.get("operator")
    if operator is not None and (
        not isinstance(operator, str) or operator not in _CONDITION_OPERATOR_SET
    ):
        raise ValueError(f"Operador de condição inválido em {node_id}: {operator}")

    field = config.get("field")
    if field is not None and not isinstance(field, _CONDITION_SCALAR_TYPES):
        raise ValueError(
            f"Campo da condição em {node_id} deve ser texto, não {type(field).__name__}"
        )

    value = config.get("value")
    if value is not None and not isinstance(value, _CONDITION_SCALAR_TYPES):
        raise ValueError(
            f"Valor da condição em {node_id} deve ser escalar, não {type(value).__name__}"
        )


FLOW_NODE_SPECS: tuple[FlowNodeSpec, ...] = (
    _spec(
        "trigger",
        "Gatilho",
        "Entrada por evento, agenda, mensagem, card, formulário ou execução manual.",
        "Entradas",
        execution_mode="control",
        input_ports=(),
        supports_error_route=False,
    ),
    _spec(
        "condition",
        "Condição",
        "Decisão binária por campo e operador tipado.",
        "Controle",
        execution_mode="control",
        output_ports=(("true", "Sim"), ("false", "Não")),
        supports_error_route=False,
        # Registro v2: primeiro (e único, por ora) nó com contrato schema-driven.
        config_version=2,
        config_schema=CONDITION_CONFIG_SCHEMA,
        ui_schema=CONDITION_CONFIG_UISCHEMA,
        output_schema=CONDITION_OUTPUT_SCHEMA,
    ),
    _spec(
        "router",
        "Roteador",
        "Seleciona a primeira rota compatível e usa fallback obrigatório.",
        "Controle",
        execution_mode="control",
        output_ports=(),
        supports_error_route=False,
    ),
    _spec(
        "merge",
        "Unir rotas",
        "Reconverge caminhos exclusivos em uma continuação determinística.",
        "Controle",
        execution_mode="control",
        input_ports=(("input", "Rota"),),
        supports_error_route=False,
    ),
    _spec(
        "loop",
        "Iterador",
        "Percorre uma coleção com limite explícito e saída de conclusão.",
        "Controle",
        execution_mode="control",
        output_ports=(("loop", "Iterar"), ("done", "Concluído")),
        supports_error_route=True,
    ),
    _spec(
        "error_handler",
        "Tratamento de erro",
        "Recebe uma rota de falha, registra o contexto e aplica a recuperação configurada.",
        "Controle",
        execution_mode="control",
        input_ports=(("error", "Erro"),),
        supports_error_route=False,
    ),
    _spec(
        "set_variable",
        "Definir variável",
        "Grava um valor tipado no contexto da execução.",
        "Dados",
    ),
    _spec(
        "transform",
        "Mapear dados",
        "Transforma múltiplos campos por expressões declarativas.",
        "Dados",
    ),
    _spec(
        "aggregate",
        "Agregar",
        "Conta, soma, seleciona ou concatena itens de uma coleção.",
        "Dados",
    ),
    _spec(
        "wait",
        "Aguardar",
        "Retoma a execução no horário calculado, sem manter worker bloqueado.",
        "Controle",
        execution_mode="control",
        side_effect="local",
    ),
    _spec(
        "link_action",
        "Link ou botão",
        "Materializa um CTA seguro para site, agenda, proposta, documento ou checkout.",
        "Conversão",
        side_effect="local",
    ),
    _spec(
        "landing_page",
        "Página de destino",
        "Publica uma página ou CTA seguro e o deixa disponível no contexto do caso.",
        "Conversão",
        side_effect="local",
    ),
    _spec(
        "form",
        "Formulário",
        "Define campos tipados para coleta e só avança após submissão válida.",
        "Dados",
        input_ports=(),
        output_ports=(("submit", "Enviado"), ("cancel", "Cancelado")),
    ),
    _spec(
        "action",
        "Ação externa",
        "Executa um conector tipado somente depois da credencial e aprovação do canal.",
        "Integrações",
        execution_mode="gated_external",
        side_effect="external",
    ),
    _spec(
        "human_transfer",
        "Transferir para humano",
        "Entrega a conversa a uma equipe ou fila humana e pausa o fluxo.",
        "Governança",
        execution_mode="control",
        side_effect="local",
    ),
    _spec(
        "llm",
        "Modelo de IA",
        "Executa uma instrução de IA com contexto e RAG definidos pelo workspace.",
        "Inteligência",
        side_effect="local",
    ),
    _spec(
        "end",
        "Encerrar fluxo",
        "Finaliza o fluxo com status e motivo auditáveis.",
        "Controle",
        execution_mode="control",
        output_ports=(),
        supports_error_route=False,
    ),
    _spec(
        "send_message",
        "Enviar mensagem",
        "Entrega uma mensagem interpolada pelo canal vinculado ao caso.",
        "Atendimento",
        execution_mode="gated_external",
        side_effect="external",
    ),
    _spec(
        "scheduled_message",
        "Agendar mensagem",
        "Cria uma entrega futura, idempotente e reconciliável.",
        "Atendimento",
        execution_mode="gated_external",
        side_effect="external",
    ),
    _spec(
        "payment_request",
        "Solicitar pagamento",
        "Prepara cobrança por PIX, boleto, cartão ou checkout no provedor aprovado.",
        "Conversão",
        execution_mode="gated_external",
        side_effect="external",
    ),
    _spec(
        "social_action",
        "Ação social",
        "Responde, envia DM, curte, oculta ou trata uma interação social oficial.",
        "Social",
        execution_mode="gated_external",
        side_effect="external",
    ),
    _spec(
        "agent",
        "Agente de IA",
        "Executa um agente especializado com contexto, RAG e revisão configuráveis.",
        "Inteligência",
        side_effect="local",
    ),
    _spec(
        "human_pause",
        "Pausa humana (legado)",
        "Compatibilidade com fluxos antigos; novos fluxos devem usar Aprovação humana governada.",
        "Governança",
        execution_mode="control",
        side_effect="local",
    ),
    _spec(
        "create_task",
        "Criar tarefa",
        "Cria uma tarefa jurídica idempotente no caso.",
        "Ferramentas internas",
        side_effect="local",
    ),
    _spec(
        "update_card",
        "Atualizar card",
        "Atualiza somente campos permitidos do Caso 360.",
        "Ferramentas internas",
        side_effect="local",
    ),
    _spec(
        "internal_note",
        "Nota interna",
        "Registra uma nota invisível ao cliente na conversa vinculada.",
        "Ferramentas internas",
        side_effect="local",
    ),
    _spec(
        "audit_event",
        "Evento de auditoria",
        "Persiste um checkpoint pesquisável da execução.",
        "Observabilidade",
        side_effect="local",
    ),
    _spec(
        "zapsign_request",
        "Solicitar assinatura",
        "Prepara contrato ou procuração e aciona o provedor aprovado.",
        "Jurídico",
        execution_mode="gated_external",
        side_effect="external",
    ),
    _spec(
        "connector_action",
        "Ação de conector",
        "Contrato tipado para API, webhook ou MCP; só executa após credencial e gate.",
        "Integrações",
        execution_mode="gated_external",
        side_effect="external",
    ),
)


def _config_object(
    title: str,
    properties: dict[str, Any],
    *,
    required: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Build the authoring contract used by both API validation and JsonForms."""
    schema: dict[str, Any] = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "title": title,
        "properties": properties,
        "additionalProperties": True,
    }
    if required:
        schema["required"] = list(required)
    return schema


_TEXT = {"type": "string"}
_NON_EMPTY_TEXT = {"type": "string", "minLength": 1}
_HUMAN_ROUTE_PROPERTIES: dict[str, Any] = {
    "required_roles": {
        "type": "array",
        "title": "Funções elegíveis",
        "items": {"type": "string", "minLength": 1},
        "minItems": 1,
        "default": ["workspace_owner"],
    },
    "required_skills": {
        "type": "array",
        "title": "Competências necessárias",
        "items": {"type": "string", "minLength": 1},
        "default": [],
    },
    "sla_seconds": {
        "type": "integer",
        "title": "SLA em segundos",
        "minimum": 60,
        "maximum": 604800,
        "default": 3600,
    },
    "max_hops": {
        "type": "integer",
        "title": "Máximo de escaladas",
        "minimum": 1,
        "maximum": 8,
        "default": 4,
    },
    "priority": {
        "type": "string",
        "enum": ["low", "normal", "high", "critical"],
        "default": "normal",
    },
}
_FINANCE_PROPERTIES: dict[str, Any] = {
    "operation": {
        "type": "string",
        "title": "Operação financeira",
        "enum": [
            "analytics",
            "drilldown",
            "dre_cash",
            "dre_accrual",
            "forecast",
            "valuation",
            "tax_reform",
            "allocation_draft",
            "collection_draft",
        ],
    },
    "cost_center_id": {"type": "string", "format": "uuid"},
    "client_id": {"type": "string", "format": "uuid"},
    "contract_id": {"type": "string", "format": "uuid"},
    "matter_id": {"type": "string", "format": "uuid"},
    "parameters": {"type": "object", "default": {}},
    "human_review_required": {"type": "boolean", "default": True},
}

_NODE_CONFIG_SCHEMAS: dict[str, dict[str, Any]] = {
    "trigger": _config_object(
        "Gatilho",
        {
            # The editor persists both names for backwards compatibility and
            # the trigger dispatcher accepts the workspace event catalogue,
            # which is wider than the small V3 examples originally listed.
            "event": _NON_EMPTY_TEXT,
            "trigger_type": _NON_EMPTY_TEXT,
            "filters": {"type": "object", "default": {}},
        },
        # API clients may keep the trigger in Flow.trigger/trigger_config;
        # the visual editor persists event in the node as an optional mirror.
        required=(),
    ),
    "router": _config_object(
        "Roteador",
        {
            "routes": {"type": "array", "items": {"type": "object"}, "minItems": 1},
            "fallback_route_key": _NON_EMPTY_TEXT,
        },
        required=("routes", "fallback_route_key"),
    ),
    "merge": _config_object(
        "Unir rotas",
        {"merge_mode": {"type": "string", "enum": ["exclusive", "first", "all"]}},
    ),
    "loop": _config_object(
        "Iterador",
        {
            "collection_path": _NON_EMPTY_TEXT,
            "item_variable": {"type": "string", "default": "item"},
            "index_variable": {"type": "string", "default": "index"},
            "max_iterations": {"type": "integer", "minimum": 1, "maximum": 500},
        },
        required=("collection_path", "max_iterations"),
    ),
    "error_handler": _config_object(
        "Tratamento de erro",
        {
            "error_strategy": {
                "type": "string",
                "enum": ["fail", "retry", "route", "wait_human"],
            },
            "max_retries": {"type": "integer", "minimum": 0, "maximum": 10},
            "retry_delay_seconds": {"type": "integer", "minimum": 0, "maximum": 86400},
            "clear_error": {"type": "boolean", "default": True},
        },
    ),
    "set_variable": _config_object(
        "Definir variável",
        {
            "variable_name": _NON_EMPTY_TEXT,
            "value_expression": {},
            "value_type": {
                "type": "string",
                "enum": ["auto", "string", "number", "boolean", "json"],
            },
        },
        required=("variable_name",),
    ),
    "transform": _config_object(
        "Mapear dados", {"mappings": {"type": "object", "minProperties": 1}}, required=("mappings",)
    ),
    "aggregate": _config_object(
        "Agregar",
        {
            "source_path": _NON_EMPTY_TEXT,
            "aggregate_operation": {
                "type": "string",
                "enum": ["count", "sum", "first", "last", "join"],
            },
            "target_variable": _NON_EMPTY_TEXT,
            "separator": _TEXT,
        },
        required=("source_path", "aggregate_operation", "target_variable"),
    ),
    "wait": _config_object(
        "Aguardar",
        {"delay_hours": {"type": "number", "minimum": 0, "maximum": 8760}},
        required=("delay_hours",),
    ),
    "link_action": _config_object(
        "Link ou botão",
        {
            "link_title": _NON_EMPTY_TEXT,
            "url": _NON_EMPTY_TEXT,
            "link_kind": _NON_EMPTY_TEXT,
            "target": _TEXT,
            "target_variable": _TEXT,
            "open_in_new_tab": {"type": "boolean", "default": True},
        },
        required=("link_title", "url"),
    ),
    "landing_page": _config_object(
        "Página de destino",
        {
            "link_title": _NON_EMPTY_TEXT,
            "url": _NON_EMPTY_TEXT,
            "link_kind": _NON_EMPTY_TEXT,
            "target": _TEXT,
            "open_in_new_tab": {"type": "boolean", "default": True},
        },
        required=("link_title", "url"),
    ),
    "form": _config_object(
        "Formulário",
        {
            "fields": {"type": "array", "items": {"type": "object"}, "minItems": 1},
            "submit_label": _TEXT,
        },
        required=("fields",),
    ),
    "action": _config_object(
        "Ação externa",
        {
            "connector": _NON_EMPTY_TEXT,
            "operation": _NON_EMPTY_TEXT,
            "input_mapping": {"type": "object"},
            "output_mapping": {"type": "object"},
            "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 900},
        },
        required=("connector", "operation"),
    ),
    "human_transfer": _config_object(
        "Transferir para humano", {**_HUMAN_ROUTE_PROPERTIES, "reason": _NON_EMPTY_TEXT}
    ),
    "llm": _config_object(
        "Modelo de IA",
        {
            "agent": _NON_EMPTY_TEXT,
            "prompt": _NON_EMPTY_TEXT,
            "model": _TEXT,
            "context_mode": {"type": "string", "enum": ["essential", "balanced", "full"]},
            "use_rag": {"type": "boolean", "default": True},
            "human_review_required": {"type": "boolean", "default": True},
        },
        required=("agent", "prompt"),
    ),
    "end": _config_object(
        "Encerrar fluxo",
        {
            "end_status": {"type": "string", "enum": ["completed", "cancelled", "failed"]},
            "reason": _NON_EMPTY_TEXT,
        },
        required=("end_status",),
    ),
    "send_message": _config_object(
        "Enviar mensagem",
        {"message": _NON_EMPTY_TEXT, "channel": _TEXT},
        required=("message",),
    ),
    "scheduled_message": _config_object(
        "Agendar mensagem",
        {
            "message": _NON_EMPTY_TEXT,
            "scheduled_at": {
                "type": "string",
                "pattern": r"^([01]?\d|2[0-3]):[0-5]\d$",
            },
            "channel": _TEXT,
            "sequence_stage": {"type": "boolean", "default": False},
            "template_name": _NON_EMPTY_TEXT,
            "template_id": {"type": "string", "minLength": 1},
            "whatsapp_connection_id": {"type": "string", "minLength": 1},
            "connection_id": {"type": "string", "minLength": 1},
            "template_status": {"type": "string", "enum": ["approved", "pending"]},
            "template_language": _TEXT,
            "bindings": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "required": ["variable", "fieldKey"],
                    "properties": {
                        "variable": {"type": "string", "minLength": 1},
                        "fieldKey": {"type": "string", "minLength": 1},
                        "scope": {"type": "string", "enum": ["body"]},
                    },
                },
            },
            "delay_value": {"type": "number", "minimum": 0},
            "delay_unit": {"type": "string", "enum": ["hours", "days"]},
            "send_now": {"type": "boolean", "default": False},
            "interrupt_on_reply": {"type": "boolean", "default": True},
        },
        # ``scheduled_at`` is legacy; sequence stages use delay_value+
        # delay_unit instead. Runtime validation rejects a stage lacking both.
        required=("message",),
    ),
    "payment_request": _config_object(
        "Solicitar pagamento",
        {
            "amount_expression": {},
            "payment_provider": _NON_EMPTY_TEXT,
            "payment_method": _NON_EMPTY_TEXT,
            "payment_description": _TEXT,
            "due_days": {"type": "integer", "minimum": 0, "maximum": 3650},
        },
        required=("amount_expression", "payment_provider"),
    ),
    "social_action": _config_object(
        "Ação social",
        {
            "social_action": {
                "type": "string",
                "enum": [
                    "public_reply",
                    "private_reply",
                    "comment_like",
                    "comment_hide",
                    "comment_delete",
                ],
            },
            "message": _TEXT,
            "target_external_id": _NON_EMPTY_TEXT,
        },
        required=("social_action",),
    ),
    "agent": _config_object(
        "Agente de IA",
        {
            "agent": _NON_EMPTY_TEXT,
            "prompt": _TEXT,
            "model": _TEXT,
            "use_rag": {"type": "boolean", "default": True},
            "human_review_required": {"type": "boolean", "default": True},
        },
        required=("agent",),
    ),
    "human_pause": _config_object(
        "Aprovação humana", {**_HUMAN_ROUTE_PROPERTIES, "reason": _NON_EMPTY_TEXT}
    ),
    "create_task": _config_object(
        "Criar tarefa",
        {
            "task_title": _NON_EMPTY_TEXT,
            "task_description": _TEXT,
            "task_priority": {"type": "string", "enum": ["baixa", "media", "alta", "critica"]},
        },
        required=("task_title",),
    ),
    "update_card": _config_object(
        "Atualizar card", {"updates": {"type": "object", "minProperties": 1}}, required=("updates",)
    ),
    "internal_note": _config_object("Nota interna", {"note": _NON_EMPTY_TEXT}, required=("note",)),
    "audit_event": _config_object(
        "Evento de auditoria",
        {"event_type": _NON_EMPTY_TEXT, "summary": _NON_EMPTY_TEXT},
        required=("event_type", "summary"),
    ),
    "zapsign_request": _config_object(
        "Solicitar assinatura",
        {
            "provider": {
                "type": "string",
                "enum": ["zapsign", "docusign", "manual"],
            },
            "doc_type": _NON_EMPTY_TEXT,
            "integration_notes": _TEXT,
        },
        required=("provider", "doc_type"),
    ),
    "connector_action": _config_object(
        "Ação de conector",
        {
            "connector": _NON_EMPTY_TEXT,
            "operation": _NON_EMPTY_TEXT,
            "input_mapping": {"type": "object"},
            "output_mapping": {"type": "object"},
            "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 900},
        },
        required=("connector", "operation"),
    ),
}

for _human_type in (
    "human_assign",
    "human_request_input",
    "human_review",
    "human_approval",
    "human_takeover",
    "human_notify",
    "human_escalate",
    "human_schedule",
    "human_wait",
):
    _NODE_CONFIG_SCHEMAS[_human_type] = _config_object(
        _human_type,
        {
            **_HUMAN_ROUTE_PROPERTIES,
            "reason": _NON_EMPTY_TEXT,
            "options": {"type": "object", "default": {}},
        },
        required=("required_roles",),
    )
# These two nodes have real canonical bindings.  Keep their contracts typed so
# the worker never guesses a conversation or an appointment from free text.
_NODE_CONFIG_SCHEMAS["human_schedule"] = _config_object(
    "Agendar compromisso humano",
    {
        **_HUMAN_ROUTE_PROPERTIES,
        "reason": _NON_EMPTY_TEXT,
        "title": _NON_EMPTY_TEXT,
        "starts_at": {"type": "string", "format": "date-time"},
        "ends_at": {"type": "string", "format": "date-time"},
        "matter_id": {"type": "string", "format": "uuid"},
        "location": _TEXT,
        "meeting_url": {"type": "string", "format": "uri"},
        "notes": _TEXT,
        "options": {"type": "object", "default": {}},
    },
    required=("required_roles", "title", "starts_at"),
)
_NODE_CONFIG_SCHEMAS["human_takeover"] = _config_object(
    "Assumir conversa com humano",
    {
        **_HUMAN_ROUTE_PROPERTIES,
        "reason": _NON_EMPTY_TEXT,
        "thread_id": {"type": "string", "format": "uuid"},
        "options": {"type": "object", "default": {}},
    },
    required=("required_roles", "thread_id"),
)
_NODE_CONFIG_SCHEMAS["human_escalate"] = _config_object(
    "Escalonar decisão humana pendente",
    {
        "target_node_id": {
            "type": "string",
            "minLength": 1,
            "title": "Checkpoint humano a escalar",
            "description": (
                "ID de um nó humano anterior deste mesmo fluxo; não é UUID e "
                "não cria uma nova decisão."
            ),
        },
        "priority": {
            "type": "string",
            "enum": ["low", "normal", "high", "critical"],
            "default": "high",
        },
    },
    required=("target_node_id",),
)
_NODE_CONFIG_SCHEMAS["human_resolve"] = _config_object(
    "Resolver decisão", {"target_node_id": _NON_EMPTY_TEXT}, required=("target_node_id",)
)
_NODE_CONFIG_SCHEMAS["finance_read"] = _config_object(
    "Consultar financeiro", _FINANCE_PROPERTIES, required=("operation",)
)
_NODE_CONFIG_SCHEMAS["finance_draft"] = _config_object(
    "Rascunho financeiro", _FINANCE_PROPERTIES, required=("operation",)
)

for _registered_spec in FLOW_NODE_SPECS:
    if _registered_spec["type"] != "condition":
        _registered_spec["config_schema"] = _NODE_CONFIG_SCHEMAS[_registered_spec["type"]]
        _registered_spec["ui_schema"] = {}
        _registered_spec["output_schema"] = {
            "type": "object",
            "ports": [dict(port) for port in _registered_spec["output_ports"]],
        }

FLOW_NODE_SPEC_BY_TYPE: dict[str, FlowNodeSpec] = {spec["type"]: spec for spec in FLOW_NODE_SPECS}
# Canonical human/finance nodes are additive to preserve legacy registry order.
_CANONICAL_NODE_TYPES = {
    "human_assign": "Atribuir responsável",
    "human_request_input": "Solicitar informação",
    "human_review": "Revisão humana",
    "human_approval": "Aprovação humana",
    "human_takeover": "Assumir atendimento",
    "human_notify": "Notificar humano",
    "human_escalate": "Escalonar",
    "human_schedule": "Agendar",
    "human_wait": "Aguardar",
    "human_resolve": "Resolver decisão",
    "finance_read": "Consultar financeiro",
    "finance_draft": "Rascunho financeiro",
}
for _type, _label in _CANONICAL_NODE_TYPES.items():
    FLOW_NODE_SPEC_BY_TYPE.setdefault(
        _type,
        _spec(
            _type,
            _label,
            "Nó canônico governado",
            "Humanos" if _type.startswith("human") else "Financeiro",
            execution_mode="control" if _type.startswith("human") else "gated_external",
            # finance_draft persists an internal financial record.  It is not
            # an external payment, but it is a real local side effect and must
            # carry idempotency/compensation metadata plus human review.
            side_effect="local" if _type == "finance_draft" else "none",
            config_version=2,
            config_schema=_NODE_CONFIG_SCHEMAS[_type],
            ui_schema={},
            output_schema={"type": "object", "ports": [{"key": "output", "label": "Próximo"}]},
        ),
    )
for _bound_type in ("human_schedule", "human_takeover"):
    FLOW_NODE_SPEC_BY_TYPE[_bound_type]["availability"] = "available"
FLOW_NODE_SPEC_BY_TYPE["finance_read"]["required_permission"] = "finance:view"
FLOW_NODE_SPEC_BY_TYPE["finance_draft"]["required_permission"] = "finance:manage"
# Explicit roadmap contracts.  They are catalogued so the editor can explain
# the gap, but remain unavailable until a native executor and tests exist.
_PLANNED_UNAVAILABLE = {
    "debounce",
    "dedupe",
    "checkpoint",
    "retry",
    "timeout",
    "cancel",
    "load_client",
    "load_contract",
    "load_matter",
    "load_task",
    "load_documents",
    "load_finance",
    "load_tax_profile",
    "rag_query",
    "validate_data",
    "mask_data",
    "data_quality",
    "compare_versions",
    "classify_intent",
    "extract_data",
    "summarize",
    "draft_content",
    "review_content",
    "evaluate_risk",
    "evaluate_confidence",
    "generate_alternatives",
    "compare_scenarios",
    "multi_agent_consensus",
    "create_meeting",
    "update_client",
    "update_contract",
    "register_event",
    "generate_document",
    "generate_report",
    "request_export",
}
for _type in _PLANNED_UNAVAILABLE:
    FLOW_NODE_SPEC_BY_TYPE.setdefault(
        _type,
        _spec(
            _type, _type.replace("_", " ").title(), "Executor ainda não implementado", "Planejado"
        ),
    )["availability"] = "unavailable"
    FLOW_NODE_SPEC_BY_TYPE[_type]["unavailable_reason"] = (
        "Catálogo reservado; executor nativo, testes e gates ainda não estão implementados."
    )
# Provider-backed intelligence adapters are implemented and registered at
# worker startup; expose them as available in the catalog while keeping the
# remaining roadmap nodes honest.
for _intelligence_type in (
    "summarize",
    "classify_intent",
    "extract_data",
    "evaluate_risk",
    "evaluate_confidence",
    "draft_content",
    "review_content",
    "generate_alternatives",
    "compare_scenarios",
    "multi_agent_consensus",
):
    FLOW_NODE_SPEC_BY_TYPE[_intelligence_type]["availability"] = "available"
    FLOW_NODE_SPEC_BY_TYPE[_intelligence_type]["unavailable_reason"] = None
for _finance_type in (
    "finance_ledger_query",
    "finance_cost_center_query",
    "finance_forecast",
    "finance_valuation",
    "finance_insight",
    "finance_anomaly",
    "finance_allocation_suggest",
    "finance_reconcile",
):
    _finance_spec = FLOW_NODE_SPEC_BY_TYPE.setdefault(
        _finance_type,
        _spec(
            _finance_type,
            _finance_type.replace("_", " ").title(),
            "Consulta financeira governada",
            "Financeiro",
        ),
    )
    _finance_spec["availability"] = "available"
    _finance_spec["required_permission"] = (
        "finance:manage"
        if _finance_type in {"finance_allocation_suggest", "finance_reconcile"}
        else "finance:view"
    )
    _finance_spec["human_fallback"] = (
        "human_review"
        if _finance_type in {"finance_allocation_suggest", "finance_reconcile", "finance_anomaly"}
        else None
    )
    _finance_spec["config_schema"] = {
        "type": "object",
        "properties": {
            "asOf": {"type": "string", "format": "date"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            "offset": {"type": "integer", "minimum": 0},
        },
    }
    _finance_spec["output_schema"] = {
        "type": "object",
        "properties": {"node": {"type": "string"}, "result": {"type": "object"}},
    }
_CONTROL_SCHEMA = {
    "type": "object",
    "properties": {
        "key": {"type": "string"},
        "name": {"type": "string"},
        "delay_seconds": {"type": "integer"},
        "max_attempts": {"type": "integer"},
        "timeout_seconds": {"type": "integer"},
        "version": {"type": "integer"},
    },
}
for _type in ("debounce", "dedupe", "checkpoint", "retry", "timeout", "cancel"):
    FLOW_NODE_SPEC_BY_TYPE[_type]["availability"] = "available"
    FLOW_NODE_SPEC_BY_TYPE[_type]["unavailable_reason"] = ""
    FLOW_NODE_SPEC_BY_TYPE[_type]["config_schema"] = _CONTROL_SCHEMA
    FLOW_NODE_SPEC_BY_TYPE[_type]["ui_schema"] = {}
    FLOW_NODE_SPEC_BY_TYPE[_type]["output_schema"] = {"type": "object"}
_DATA_CONTEXT_TYPES = (
    "load_client",
    "load_contract",
    "load_matter",
    "load_task",
    "load_documents",
    "load_finance",
    "load_tax_profile",
    "validate_data",
    "mask_data",
    "data_quality",
    "compare_versions",
    "rag_query",
)
_DATA_SCHEMA = {"type": "object", "additionalProperties": True}
for _type in _DATA_CONTEXT_TYPES:
    FLOW_NODE_SPEC_BY_TYPE[_type]["availability"] = "available"
    FLOW_NODE_SPEC_BY_TYPE[_type]["unavailable_reason"] = ""
    FLOW_NODE_SPEC_BY_TYPE[_type]["config_schema"] = _DATA_SCHEMA
    FLOW_NODE_SPEC_BY_TYPE[_type]["ui_schema"] = {}
    FLOW_NODE_SPEC_BY_TYPE[_type]["output_schema"] = {"type": "object"}
FLOW_NODE_SPEC_BY_TYPE["load_finance"]["required_permission"] = "finance:view"
FLOW_NODE_SPEC_BY_TYPE["load_tax_profile"]["required_permission"] = "finance:view"
for _type in (
    "summarize",
    "classify_intent",
    "extract_data",
    "evaluate_risk",
    "evaluate_confidence",
):
    FLOW_NODE_SPEC_BY_TYPE[_type]["availability"] = "available"
    FLOW_NODE_SPEC_BY_TYPE[_type]["unavailable_reason"] = ""
    FLOW_NODE_SPEC_BY_TYPE[_type]["config_schema"] = {
        "type": "object",
        "additionalProperties": True,
    }
    FLOW_NODE_SPEC_BY_TYPE[_type]["output_schema"] = {"type": "object"}

# Native operational nodes are enabled only where the worker has a typed
# command path.  Meeting creation delegates to MeetingService, preserving its
# tenant checks, entitlement, conflict detection and creation receipt.
FLOW_NODE_SPEC_BY_TYPE["create_meeting"]["availability"] = "available"
FLOW_NODE_SPEC_BY_TYPE["create_meeting"]["unavailable_reason"] = ""
FLOW_NODE_SPEC_BY_TYPE["create_meeting"]["execution_mode"] = "native"
FLOW_NODE_SPEC_BY_TYPE["create_meeting"]["side_effect"] = "local"
FLOW_NODE_SPEC_BY_TYPE["create_meeting"]["required_permission"] = "kanban:edit"
FLOW_NODE_SPEC_BY_TYPE["create_meeting"]["risk"] = "medium"
FLOW_NODE_SPEC_BY_TYPE["create_meeting"]["config_schema"] = {
    "type": "object",
    "required": ["title", "starts_at"],
    "properties": {
        "title": {"type": "string", "minLength": 1, "maxLength": 255},
        "starts_at": {"type": "string", "format": "date-time"},
        "ends_at": {"type": ["string", "null"], "format": "date-time"},
        "matter_id": {"type": ["string", "null"], "format": "uuid"},
        "responsible_user_id": {"type": ["string", "null"], "format": "uuid"},
        "notes": {"type": ["string", "null"], "maxLength": 5000},
        "idempotency_key": {"type": "string", "minLength": 8, "maxLength": 120},
    },
    "additionalProperties": False,
}
FLOW_NODE_SPEC_BY_TYPE["create_meeting"]["ui_schema"] = {
    "order": ["title", "starts_at", "ends_at", "matter_id", "responsible_user_id", "notes"]
}
FLOW_NODE_SPEC_BY_TYPE["create_meeting"]["output_schema"] = {
    "type": "object",
    "properties": {"meetingId": {"type": "string"}},
}

# Native mutation nodes.  Their configuration is rendered by the existing
# schema-driven flow editor; making them available here and in the worker
# together prevents a dormant backend-only capability.
for _type, _permission, _resource_field in (
    ("update_client", "kanban:edit", "client_id"),
    ("update_contract", "finance:manage", "proposal_id"),
):
    _node = FLOW_NODE_SPEC_BY_TYPE[_type]
    _node["availability"] = "available"
    _node["unavailable_reason"] = ""
    _node["execution_mode"] = "native"
    _node["side_effect"] = "local"
    _node["risk"] = "medium"
    _node["required_permission"] = _permission
    _node["config_schema"] = {
        "type": "object",
        "required": [_resource_field, "changes"],
        "properties": {
            _resource_field: {"type": "string", "format": "uuid"},
            "changes": {"type": "object", "minProperties": 1},
            "expected_updated_at": {"type": "string", "format": "date-time"},
            "idempotency_key": {"type": "string", "minLength": 8, "maxLength": 120},
        },
        "additionalProperties": False,
    }
    _node["ui_schema"] = {
        "order": [_resource_field, "changes", "expected_updated_at", "idempotency_key"]
    }
    _node["output_schema"] = {"type": "object", "properties": {"resourceId": {"type": "string"}}}

_NATIVE_OPERATIONAL_NODE_SPECS = {
    "register_event": {
        "permission": "automations:manage",
        "risk": "low",
        "schema": {
            "type": "object",
            "required": ["event_type", "summary"],
            "properties": {
                "event_type": {"type": "string", "minLength": 3, "maxLength": 120},
                "summary": {"type": "string", "minLength": 3, "maxLength": 2000},
                "entity_type": {"type": "string", "maxLength": 100},
                "entity_id": {"type": "string", "format": "uuid"},
                "after_data": {"type": "object"},
            },
            "additionalProperties": False,
        },
    },
    "generate_report": {
        "permission": "relatorios:view",
        "risk": "low",
        "schema": {
            "type": "object",
            "properties": {"include_insights": {"type": "boolean"}},
            "additionalProperties": False,
        },
    },
    "request_export": {
        "permission": "finance:export",
        "risk": "medium",
        "schema": {
            "type": "object",
            "required": ["format"],
            "properties": {
                "format": {"type": "string", "enum": ["pdf", "xlsx", "docx"]},
                "drill_context": {"type": "object"},
            },
            "additionalProperties": False,
        },
    },
    "generate_document": {
        "permission": "kanban:edit",
        "risk": "medium",
        "schema": {
            "type": "object",
            "required": ["matter_id", "title"],
            "properties": {
                "matter_id": {"type": "string", "format": "uuid"},
                "title": {"type": "string", "minLength": 3, "maxLength": 255},
                "reason": {"type": "string", "maxLength": 1000},
            },
            "additionalProperties": False,
        },
    },
}
for _type, _contract in _NATIVE_OPERATIONAL_NODE_SPECS.items():
    _node = FLOW_NODE_SPEC_BY_TYPE[_type]
    _node.update(
        {
            "availability": "available",
            "unavailable_reason": "",
            "execution_mode": "native",
            "side_effect": "local",
            "risk": _contract["risk"],
            "required_permission": _contract["permission"],
            "config_schema": _contract["schema"],
            "ui_schema": {"order": list(_contract["schema"].get("properties", {}).keys())},
            "output_schema": {"type": "object", "properties": {"status": {"type": "string"}}},
        }
    )

# Nodes promoted from the roadmap must also stop presenting themselves as
# planned in the API/editor. Availability is an execution contract; retaining
# the old label after binding a tested executor makes the UI misrepresent a
# live capability.
_PROMOTED_NODE_CATEGORIES = {
    **{
        node_type: "Controle"
        for node_type in ("debounce", "dedupe", "checkpoint", "retry", "timeout", "cancel")
    },
    **{node_type: "Dados e contexto" for node_type in _DATA_CONTEXT_TYPES},
    **{
        node_type: "Inteligência"
        for node_type in (
            "summarize",
            "classify_intent",
            "extract_data",
            "draft_content",
            "review_content",
            "evaluate_risk",
            "evaluate_confidence",
            "generate_alternatives",
            "compare_scenarios",
            "multi_agent_consensus",
        )
    },
    **{
        node_type: "Operações internas"
        for node_type in (
            "create_meeting",
            "update_client",
            "update_contract",
            "register_event",
            "generate_document",
            "generate_report",
            "request_export",
        )
    },
}
for _type, _category in _PROMOTED_NODE_CATEGORIES.items():
    _node = FLOW_NODE_SPEC_BY_TYPE[_type]
    if _node.get("availability") == "available":
        _node["category"] = _category
        if _node.get("description") == "Executor ainda não implementado":
            _node["description"] = "Nó executável pelo runtime governado de flows."

# Public authoring contract: every catalogued node must remain renderable and
# explicit about availability, even while its native executor is being built.
# This prevents a registry entry from becoming a dormant backend-only type or
# forcing the frontend to infer an untyped configuration form.
for _type, _registered_spec in FLOW_NODE_SPEC_BY_TYPE.items():
    _registered_spec.setdefault("availability", "available")
    _registered_spec.setdefault(
        "config_schema",
        {
            "type": "object",
            "title": _registered_spec["label"],
            "additionalProperties": False,
            "properties": {},
        },
    )
    _registered_spec.setdefault("ui_schema", {})
    _registered_spec.setdefault(
        "output_schema",
        {
            "type": "object",
            "ports": [dict(port) for port in _registered_spec["output_ports"]],
        },
    )
FLOW_NODE_TYPES = frozenset(FLOW_NODE_SPEC_BY_TYPE)
FLOW_EXTERNAL_NODE_TYPES = frozenset(
    spec["type"] for spec in FLOW_NODE_SPECS if spec["side_effect"] == "external"
)


def flow_node_catalog() -> list[dict[str, Any]]:
    """Return detached JSON-safe entries so callers cannot mutate the registry."""
    catalog: list[dict[str, Any]] = []
    for spec in FLOW_NODE_SPEC_BY_TYPE.values():
        entry: dict[str, Any] = {
            **spec,
            "input_ports": [dict(port) for port in spec["input_ports"]],
            "output_ports": [dict(port) for port in spec["output_ports"]],
        }
        # Traduz as chaves internas v2 para o contrato público do catálogo
        # (JsonForms: schema/uischema/outputSchema). Aditivo: só aparece nos
        # tipos migrados; os demais permanecem sem essas chaves.
        if "config_schema" in entry:
            entry["schema"] = entry.pop("config_schema")
            entry["uischema"] = entry.pop("ui_schema")
            entry["outputSchema"] = entry.pop("output_schema")
        catalog.append(entry)
    return catalog
