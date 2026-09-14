"""Registro de tool executors READ-ONLY para o model mode dos agentes KLM.

Escopo (S7-a, spec docs/PLANO_FINANCEIRO_PROFUNDO_2026-07-24.md):
- somente ferramentas de LEITURA já declaradas em `AgentDefinition.tools`;
- nenhuma escrita, nenhuma chamada externa, nenhuma rede;
- cada executor lê exclusivamente do `context` já compilado por
  `KlmAgentService._context` (mesmo dado que já embasa o prompt), então não
  há nova consulta ao banco nem novo escopo de dado a validar;
- qualquer tool key fora deste registro é recusada fail-closed pelo chamador
  (loop do model mode), que também exige a chave estar em `agent.tools`
  (dupla checagem: governança + registro).

O contrato de execução é: `executor(context: dict) -> dict` síncrono e puro
sobre o dicionário de contexto já carregado — sem I/O adicional.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

ToolExecutor = Callable[[Mapping[str, Any]], dict[str, Any]]


def _crm_read(context: Mapping[str, Any]) -> dict[str, Any]:
    matter = context["matter"]
    return {
        "clientName": matter.client.name,
        "caseNumber": matter.case_number,
        "columnId": matter.column_id.value,
        "microStatus": matter.micro_status.value,
        "priority": matter.priority.value,
        "urgency": matter.urgency.value,
        "manualContextRevision": matter.manual_context_revision,
    }


def _processos_read(context: Mapping[str, Any]) -> dict[str, Any]:
    matter = context["matter"]
    return {
        "caseNumber": matter.case_number,
        "practiceArea": (
            matter.practice_area.value
            if hasattr(matter.practice_area, "value")
            else str(matter.practice_area)
        ),
        "demandType": matter.demand_type,
        "documents": context.get("documents", []),
        "missingDocuments": context.get("missingDocuments", []),
    }


def _audit_read(context: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "recentMessageCount": context.get("recentMessageCount", 0),
        "recentPublicMessageCount": context.get("recentPublicMessageCount", 0),
        "recentInternalMessageCount": context.get("recentInternalMessageCount", 0),
        "retrievalReceipt": context.get("retrievalReceipt"),
    }


def _delivery_audit_read(context: Mapping[str, Any]) -> dict[str, Any]:
    return _audit_read(context)


def _conversation_rag_read(context: Mapping[str, Any]) -> dict[str, Any]:
    chunks = context.get("knowledgeChunks", [])
    return {
        "knowledgeChunkCount": context.get("knowledgeCount", 0),
        "knowledgeChunks": [
            {"title": chunk.get("title"), "sourceType": chunk.get("sourceType")}
            for chunk in chunks[:6]
        ],
    }


def _manual_revision_check_read(context: Mapping[str, Any]) -> dict[str, Any]:
    matter = context["matter"]
    return {
        "manualContextRevision": matter.manual_context_revision,
        "manualContextUpdatedAt": (
            matter.manual_context_updated_at.isoformat()
            if matter.manual_context_updated_at
            else None
        ),
    }


# Somente ferramentas de LEITURA já declaradas em `AGENT_DEFINITIONS` no
# klm_agent_service. Adicionar uma tool aqui NÃO a autoriza sozinha: ela
# também precisa estar em `agent.tools` para o agente que a solicitar.
READ_ONLY_TOOL_EXECUTORS: dict[str, ToolExecutor] = {
    "crm_read": _crm_read,
    "processos_read": _processos_read,
    "audit_read": _audit_read,
    "delivery_audit_read": _delivery_audit_read,
    "conversation_rag": _conversation_rag_read,
    "manual_revision_check": _manual_revision_check_read,
}

# Limite duro de tool-calls por run do model mode (S7-a).
MAX_MODEL_MODE_TOOL_CALLS = 3


def is_safe_read_tool(tool_key: str) -> bool:
    return tool_key in READ_ONLY_TOOL_EXECUTORS


def execute_tool(tool_key: str, context: Mapping[str, Any]) -> dict[str, Any]:
    executor = READ_ONLY_TOOL_EXECUTORS.get(tool_key)
    if executor is None:
        raise KeyError(f"Tool desconhecida ou não READ-ONLY: {tool_key}")
    return executor(context)
