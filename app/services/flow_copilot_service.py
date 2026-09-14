"""Governed local copilot that proposes typed Flow Builder patches."""

from __future__ import annotations

import re
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.flow import Flow
from app.models.playbook import PlaybookScopeDimension
from app.schemas.flow import FlowCopilotPlanRequest, validate_flow_graph
from app.services.flow_node_registry import FLOW_NODE_SPEC_BY_TYPE
from app.services.playbook_service import PlaybookNotFoundError, PlaybookService

FlowCopilotStep = tuple[str, str, dict[str, Any]]


def _text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).casefold()


class FlowCopilotService:
    """Creates reviewable graph patches; it never saves or runs a flow."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def plan(
        self,
        workspace_id: UUID,
        payload: FlowCopilotPlanRequest,
    ) -> dict[str, Any]:
        source_nodes = deepcopy(payload.current_nodes)
        source_edges = deepcopy(payload.current_edges)
        practice_area = payload.practice_area
        if payload.flow_id:
            flow = await self.db.scalar(
                select(Flow).where(
                    Flow.id == payload.flow_id,
                    Flow.workspace_id == workspace_id,
                    Flow.deleted_at.is_(None),
                )
            )
            if flow is None:
                raise LookupError("Fluxo não encontrado neste workspace.")
            practice_area = flow.practice_area
            if not source_nodes:
                source_nodes = deepcopy(flow.nodes or [])
                source_edges = deepcopy(flow.edges or [])

        playbook_name: str | None = None
        playbook_version: int | None = None
        try:
            playbook = await PlaybookService(self.db).effective(
                workspace_id=workspace_id,
                selectors={
                    PlaybookScopeDimension.area: practice_area,
                    PlaybookScopeDimension.channel: None,
                    PlaybookScopeDimension.stage: None,
                    PlaybookScopeDimension.agent: None,
                    PlaybookScopeDimension.offer: None,
                },
            )
            playbook_name = playbook.name
            playbook_version = playbook.published_version or playbook.current_version
        except PlaybookNotFoundError:
            playbook = None

        instruction = _text(payload.instruction)
        attachment_context, interpreted_attachments = self._attachment_context(payload)
        planning_context = " ".join(part for part in (instruction, attachment_context) if part)
        requested = self._requested_steps(planning_context)
        gate_added = self._enforce_playbook_gate(playbook, requested)
        source_node_count = len(source_nodes)
        if not source_nodes and not any(item[0] == "trigger" for item in requested):
            requested.insert(0, ("trigger", "Início solicitado", {"event": "manual"}))

        nodes = source_nodes
        edges = source_edges
        if nodes:
            validate_flow_graph(nodes, edges)

        requested_trigger = next(
            (item for item in requested if item[0] == "trigger"),
            None,
        )
        trigger_updated = False
        if nodes and requested_trigger:
            self._update_existing_trigger(nodes, edges, requested_trigger)
            requested = [item for item in requested if item is not requested_trigger]
            trigger_updated = True

        insertion_target: str | None = None
        insert_before_end: str | None = None
        if nodes and requested:
            insertion_target, insert_before_end = self._insertion_point(nodes, edges)
            if insert_before_end and requested[-1][0] == "end":
                requested_end = requested.pop()
                self._update_node(nodes, insert_before_end, requested_end)

        existing_ids = {str(node.get("id")) for node in nodes}
        added_ids: list[str] = []
        new_node_ids: list[str] = []
        origin_x = (
            max(
                (float((node.get("position") or {}).get("x", 0)) for node in nodes),
                default=-280,
            )
            + 280
        )
        origin_y = max(
            (float((node.get("position") or {}).get("y", 0)) for node in nodes),
            default=40,
        )

        for index, (node_type, label, config) in enumerate(requested):
            if node_type not in FLOW_NODE_SPEC_BY_TYPE:
                continue
            node_id = f"copilot-{uuid4().hex[:10]}"
            while node_id in existing_ids:
                node_id = f"copilot-{uuid4().hex[:10]}"
            existing_ids.add(node_id)
            added_ids.append(node_id)
            new_node_ids.append(node_id)
            spec = FLOW_NODE_SPEC_BY_TYPE[node_type]
            nodes.append(
                {
                    "id": node_id,
                    "type": node_type,
                    "label": label,
                    "position": {
                        "x": origin_x + (index % 3) * 280,
                        "y": origin_y + (index // 3) * 170,
                    },
                    "config": config,
                    "data": {
                        "label": label,
                        "category": spec["category"],
                        "description": spec["description"],
                        "copilot_proposed": True,
                    },
                }
            )

        if new_node_ids:
            if nodes and source_node_count:
                self._connect_to_existing_graph(
                    edges=edges,
                    new_node_ids=new_node_ids,
                    insertion_target=insertion_target,
                    insert_before_end=insert_before_end,
                )
            else:
                self._connect_sequence(edges, new_node_ids)

        validate_flow_graph(nodes, edges)

        attachment_names = [item.name for item in payload.attachments]
        rationale = [
            f"{len(added_ids)} etapas tipadas foram propostas a partir do pedido.",
        ]
        if source_node_count and added_ids:
            rationale.append(
                "O grafo atual foi preservado e a proposta foi inserida "
                "na única saída estruturalmente segura."
            )
        elif not source_node_count:
            rationale.append("Um novo caminho linear e validado foi preparado para o canvas.")
        if trigger_updated:
            rationale.append(
                "O gatilho existente foi atualizado; nenhum gatilho foi ligado ao final do fluxo."
            )
        if playbook_name:
            rationale.append(
                f"Playbook efetivo “{playbook_name}” v{playbook_version} "
                "definiu a governança da proposta."
            )
        if gate_added:
            rationale.append(
                "Um gate humano foi incluído porque o playbook exige revisão "
                "antes de ações externas ou irreversíveis."
            )
        if interpreted_attachments:
            rationale.append(
                "Trechos textuais interpretados: " + ", ".join(interpreted_attachments[:6]) + "."
            )
        if attachment_names:
            reference_only = [
                name for name in attachment_names if name not in interpreted_attachments
            ]
        else:
            reference_only = []
        if reference_only:
            rationale.append(
                "Anexos mantidos somente como referência nominal, sem alegação de leitura: "
                + ", ".join(reference_only[:6])
                + "."
            )
        if source_node_count:
            rationale.append(
                f"O desenho atual da conta, com {source_node_count} etapas existentes, "
                "foi preservado como base."
            )

        warnings = [
            "A proposta não foi salva nem executada.",
            "Ações externas permanecem bloqueadas até salvar, testar e publicar a revisão.",
        ]
        if not playbook:
            warnings.append(
                "Nenhum playbook publicado correspondeu à área; valide tom, gates e oferta."
            )
        if reference_only:
            warnings.append(
                "PDF, Word e imagens sem trecho textual exigem extração ou OCR "
                "antes de influenciarem o plano."
            )

        questions = []
        graph_has_trigger = any(node.get("type") == "trigger" for node in nodes)
        if "quando" not in instruction and not graph_has_trigger:
            questions.append("Qual evento deve iniciar este fluxo?")
        if not any(item[0] in {"human_pause", "human_transfer"} for item in requested):
            questions.append("Em qual etapa uma pessoa deve revisar ou assumir?")

        return {
            "proposal_id": uuid4(),
            "status": "awaiting_approval",
            "engine": "klm_flow_copilot_rules_v2",
            "summary": self._summary(payload.instruction, added_ids),
            "rationale": rationale,
            "suggestions": self._suggestions(requested),
            "questions": questions,
            "warnings": warnings,
            "nodes": nodes,
            "edges": edges,
            "added_node_ids": added_ids,
            "playbook_name": playbook_name,
            "playbook_version": playbook_version,
            "account_context_used": bool(playbook or source_nodes or payload.flow_id),
            "attachment_count": len(payload.attachments),
            "approval_required": True,
            "external_actions_executed": 0,
            "generated_at": datetime.now(UTC),
        }

    @staticmethod
    def _attachment_context(
        payload: FlowCopilotPlanRequest,
    ) -> tuple[str, list[str]]:
        excerpts: list[str] = []
        interpreted: list[str] = []
        for attachment in payload.attachments:
            excerpt = (attachment.text_excerpt or "").strip()
            if not excerpt:
                continue
            excerpts.append(excerpt)
            interpreted.append(attachment.name)
        return _text(" ".join(excerpts)), interpreted

    @staticmethod
    def _enforce_playbook_gate(
        playbook: Any,
        requested: list[FlowCopilotStep],
    ) -> bool:
        if playbook is None:
            return False
        policy = playbook.content.policy
        approval_required = bool(
            policy.hard_gates
            or playbook.content.proposals.approval_required
            or playbook.content.commercial.require_human_review
        )
        risky_types = {
            "connector_action",
            "end",
            "human_transfer",
            "payment_request",
            "social_action",
            "zapsign_request",
        }
        if not approval_required or not any(item[0] in risky_types for item in requested):
            return False
        if any(item[0] in {"human_pause", "human_transfer"} for item in requested):
            return False
        insertion_index = next(
            (index for index, item in enumerate(requested) if item[0] == "end"),
            len(requested),
        )
        requested.insert(
            insertion_index,
            (
                "human_pause",
                "Aprovação exigida pelo playbook",
                {"reason": "Gate obrigatório do playbook efetivo"},
            ),
        )
        return True

    @staticmethod
    def _root_triggers(
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        incoming_targets = {str(edge.get("target")) for edge in edges}
        return [
            node
            for node in nodes
            if node.get("type") == "trigger" and str(node.get("id")) not in incoming_targets
        ]

    @classmethod
    def _update_existing_trigger(
        cls,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        requested_trigger: FlowCopilotStep,
    ) -> None:
        root_triggers = cls._root_triggers(nodes, edges)
        if len(root_triggers) != 1:
            raise ValueError(
                "O flow precisa ter exatamente um gatilho raiz "
                "para que o copiloto altere sua entrada."
            )
        cls._update_node(nodes, str(root_triggers[0]["id"]), requested_trigger)

    @staticmethod
    def _update_node(
        nodes: list[dict[str, Any]],
        node_id: str,
        requested_node: FlowCopilotStep,
    ) -> None:
        node_type, label, config = requested_node
        node = next((item for item in nodes if str(item.get("id")) == node_id), None)
        if node is None:
            raise ValueError("A etapa que seria atualizada não existe mais no canvas.")
        spec = FLOW_NODE_SPEC_BY_TYPE[node_type]
        node["type"] = node_type
        node["label"] = label
        node["config"] = config
        data = node.get("data")
        node["data"] = {
            **(data if isinstance(data, dict) else {}),
            "label": label,
            "category": spec["category"],
            "description": spec["description"],
            "copilot_proposed": True,
        }

    @staticmethod
    def _insertion_point(
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
    ) -> tuple[str | None, str | None]:
        outgoing_sources = {str(edge.get("source")) for edge in edges}
        terminal_nodes = [node for node in nodes if str(node.get("id")) not in outgoing_sources]
        if len(terminal_nodes) != 1:
            raise ValueError(
                "O flow possui múltiplas saídas ou um ciclo. "
                "Escolha a ramificação antes de pedir a inserção."
            )
        terminal = terminal_nodes[0]
        terminal_id = str(terminal["id"])
        if terminal.get("type") == "end":
            if not any(str(edge.get("target")) == terminal_id for edge in edges):
                raise ValueError(
                    "O encerramento atual não possui uma entrada segura para receber novas etapas."
                )
            return None, terminal_id
        return terminal_id, None

    @staticmethod
    def _new_edge(source: str, target: str) -> dict[str, str]:
        return {
            "id": f"edge-copilot-{uuid4().hex[:12]}",
            "source": source,
            "target": target,
        }

    @classmethod
    def _connect_sequence(
        cls,
        edges: list[dict[str, Any]],
        node_ids: list[str],
    ) -> None:
        for source, target in zip(node_ids, node_ids[1:], strict=False):
            edges.append(cls._new_edge(source, target))

    @classmethod
    def _connect_to_existing_graph(
        cls,
        *,
        edges: list[dict[str, Any]],
        new_node_ids: list[str],
        insertion_target: str | None,
        insert_before_end: str | None,
    ) -> None:
        first_id = new_node_ids[0]
        last_id = new_node_ids[-1]
        if insert_before_end:
            for edge in edges:
                if str(edge.get("target")) == insert_before_end:
                    edge["target"] = first_id
            edges.append(cls._new_edge(last_id, insert_before_end))
        elif insertion_target:
            edges.append(cls._new_edge(insertion_target, first_id))
        else:
            raise ValueError("Não foi encontrada uma saída segura para inserir a proposta.")
        cls._connect_sequence(edges, new_node_ids)

    @staticmethod
    def _requested_steps(instruction: str) -> list[FlowCopilotStep]:
        steps: list[FlowCopilotStep] = []
        if any(term in instruction for term in ("comentário", "comentario", "reel", "instagram")):
            steps.append(
                (
                    "trigger",
                    "Comentário elegível",
                    {"event": "keyword_comment", "trigger_type": "keyword_comment"},
                )
            )
        elif any(
            term in instruction for term in ("novo lead", "lead entrar", "formulário", "formulario")
        ):
            steps.append(("trigger", "Novo lead", {"event": "new_lead"}))
        elif any(
            term in instruction
            for term in ("mensagem recebida", "quando responder", "quando chegar")
        ):
            steps.append(("trigger", "Mensagem recebida", {"event": "message_received"}))

        if any(term in instruction for term in ("classif", "triagem", "qualifi")):
            steps.append(
                (
                    "agent",
                    "Qualificar e rotear",
                    {
                        "agent": "klm_sdr",
                        "stage_key": "entrada",
                        "context_mode": "balanced",
                        "use_rag": True,
                        "human_review_required": False,
                    },
                )
            )
        if any(term in instruction for term in ("document", "anexo", "laudo", "negativa")):
            steps.extend(
                [
                    (
                        "send_message",
                        "Solicitar documentos",
                        {"message": "Envie os documentos necessários para continuarmos."},
                    ),
                    ("wait", "Aguardar documentos", {"delay_hours": 24, "condition": "always"}),
                    (
                        "condition",
                        "Documentos suficientes?",
                        {"field": "doc_score", "operator": "gt", "value": "87"},
                    ),
                ]
            )
        if any(
            term in instruction
            for term in (
                "follow-up",
                "follow up",
                "cobrar retorno",
                "não responder",
                "nao responder",
            )
        ):
            steps.extend(
                [
                    ("wait", "Aguardar resposta", {"delay_hours": 18, "condition": "no_response"}),
                    (
                        "send_message",
                        "Follow-up contextual",
                        {"message": "Olá {nome}, posso ajudar a concluir o próximo passo?"},
                    ),
                ]
            )
        if any(term in instruction for term in ("agendar", "reunião", "reuniao", "agenda")):
            steps.append(
                (
                    "link_action",
                    "Oferecer agenda",
                    {"link_kind": "booking", "url": "/app/agenda", "target": "customer"},
                )
            )
        if any(
            term in instruction for term in ("site", "página", "pagina", "link externo", "checkout")
        ):
            steps.append(
                (
                    "link_action",
                    "Liberar link",
                    {
                        "link_kind": "checkout" if "checkout" in instruction else "website",
                        "url": "/app",
                        "link_title": "Abrir",
                        "target": "customer",
                        "open_in_new_tab": True,
                    },
                )
            )
        if any(
            term in instruction
            for term in ("webhook", "api externa", "integração", "integracao", "mcp")
        ):
            steps.append(
                (
                    "connector_action",
                    "Acionar integração",
                    {
                        "connector": "webhook",
                        "operation": "post",
                        "input_mapping": {},
                        "output_mapping": {},
                        "timeout_seconds": 30,
                        "error_strategy": "wait_human",
                    },
                )
            )
        if any(
            term in instruction
            for term in (
                "atualizar crm",
                "atualizar card",
                "mover card",
                "próxima ação",
                "proxima acao",
            )
        ):
            steps.append(
                (
                    "update_card",
                    "Atualizar Caso 360",
                    {"updates": {"next_action": "Revisar próximo passo do fluxo"}},
                )
            )
        if "nota interna" in instruction:
            steps.append(
                (
                    "internal_note",
                    "Registrar nota interna",
                    {"note": "Checkpoint solicitado ao copiloto de flows."},
                )
            )
        if any(
            term in instruction for term in ("criar tarefa", "enfileirar tarefa", "fila de tarefa")
        ):
            steps.append(
                (
                    "create_task",
                    "Criar tarefa",
                    {
                        "task_title": "Executar ação definida no flow",
                        "task_priority": "media",
                    },
                )
            )
        if any(term in instruction for term in ("proposta", "contrato", "assinatura")):
            steps.append(
                (
                    "zapsign_request",
                    "Preparar assinatura",
                    {
                        "provider": "zapsign",
                        "doc_type": "contrato",
                        "auth_mode": "assinaturaTela-tokenEmail",
                        "human_review_required": True,
                    },
                )
            )
        if any(term in instruction for term in ("pagamento", "pix", "boleto", "checkout")):
            steps.append(
                (
                    "payment_request",
                    "Preparar cobrança",
                    {
                        "payment_provider": "asaas",
                        "payment_method": "any",
                        "amount_expression": "{{honorarios.iniciais}}",
                        "due_days": 3,
                        "payment_description": "Honorários advocatícios",
                        "human_review_required": True,
                    },
                )
            )
        if any(term in instruction for term in ("responder comentário", "responder comentario")):
            steps.append(
                (
                    "social_action",
                    "Responder comentário",
                    {
                        "social_action": "public_reply",
                        "message": "Olá, enviei uma mensagem privada para entender melhor.",
                        "target_external_id": "{{social.eventId}}",
                    },
                )
            )
        if any(term in instruction for term in ("privado", "direct", "dm")):
            steps.append(
                (
                    "social_action",
                    "Enviar resposta privada",
                    {
                        "social_action": "private_reply",
                        "message": "Olá. Posso entender melhor o que você precisa?",
                        "target_external_id": "{{social.eventId}}",
                    },
                )
            )
        if any(term in instruction for term in ("humano", "aprovação", "aprovacao", "revisar")):
            steps.append(
                ("human_pause", "Aprovação humana", {"reason": "Revisar proposta do copiloto"})
            )
        if any(
            term in instruction for term in ("transferir", "assumir atendimento", "fila humana")
        ):
            steps.append(
                (
                    "human_transfer",
                    "Transferir para equipe",
                    {"team": "Atendimento", "reason": "Atendimento humano necessário"},
                )
            )
        if any(
            term in instruction for term in ("encerrar fluxo", "finalizar fluxo", "concluir fluxo")
        ):
            steps.append(
                ("end", "Encerrar fluxo", {"end_status": "completed", "reason": "Fluxo concluído"})
            )
        if not steps:
            steps.extend(
                [
                    (
                        "agent",
                        "Analisar solicitação",
                        {"agent": "orchestrator", "use_rag": True, "human_review_required": True},
                    ),
                    (
                        "create_task",
                        "Enfileirar próxima ação",
                        {
                            "task_title": "Executar próximo passo do fluxo",
                            "task_priority": "media",
                        },
                    ),
                    (
                        "human_pause",
                        "Revisar antes de continuar",
                        {"reason": "Pedido complexo criado pelo copiloto"},
                    ),
                ]
            )
        if steps[-1][0] not in {"human_pause", "human_transfer", "end"}:
            steps.append(
                (
                    "audit_event",
                    "Registrar conclusão",
                    {
                        "event_type": "flow_copilot_checkpoint",
                        "summary": "Checkpoint criado pela proposta aprovada do copiloto.",
                    },
                )
            )
        return steps

    @staticmethod
    def _summary(instruction: str, added_ids: list[str]) -> str:
        compact = re.sub(r"\s+", " ", instruction.strip())
        return f"Proposta com {len(added_ids)} novas etapas para: {compact[:240]}"

    @staticmethod
    def _suggestions(
        steps: list[tuple[str, str, dict[str, Any]]],
    ) -> list[str]:
        node_types = {item[0] for item in steps}
        suggestions = ["Teste o caminho feliz e pelo menos uma falha antes de publicar."]
        if "error_handler" not in node_types:
            suggestions.append("Adicione uma rota de erro para falhas de API ou dados incompletos.")
        if "human_pause" not in node_types and "human_transfer" not in node_types:
            suggestions.append("Inclua uma aprovação humana antes de ações irreversíveis.")
        if "audit_event" not in node_types:
            suggestions.append("Registre checkpoints para auditoria e métricas.")
        return suggestions
