"""Deterministic, governed planning for the operational page copilots."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.playbook import PlaybookScopeDimension
from app.schemas.operational_copilot import (
    OperationalCopilotPlanRequest,
)
from app.services.playbook_service import PlaybookNotFoundError, PlaybookService


def _normalized(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).casefold()


def _action(
    *,
    label: str,
    description: str,
    kind: str,
    risk: str,
    permission: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "action_id": uuid4(),
        "label": label,
        "description": description,
        "kind": kind,
        "risk": risk,
        "payload": payload,
        "required_permission": permission,
        "approval_required": True,
    }


class OperationalCopilotService:
    """Builds reviewable UI proposals; it never executes side effects."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def plan(
        self,
        workspace_id: UUID,
        payload: OperationalCopilotPlanRequest,
    ) -> dict[str, Any]:
        instruction = _normalized(payload.instruction)
        playbook_name, playbook_version = await self._effective_playbook(
            workspace_id,
            payload.context_snapshot,
        )
        actions = self._actions(payload.domain, instruction, payload.context_snapshot)
        attachment_names = [item.name for item in payload.attachments]
        interpreted = [
            item.name for item in payload.attachments if item.text_excerpt
        ]
        reference_only = [
            item for item in attachment_names if item not in interpreted
        ]

        rationale = [
            "A proposta usa somente ações tipadas e permitidas pela página atual.",
            "Nenhuma publicação, envio, conexão externa ou alteração persistente foi executada.",
        ]
        if playbook_name:
            rationale.append(
                f'O playbook efetivo "{playbook_name}" v{playbook_version} '
                "foi usado como referência."
            )
        if payload.context_snapshot:
            rationale.append("O estado atual da conta e da página foi considerado.")
        if interpreted:
            rationale.append(
                "Trechos textuais interpretados: " + ", ".join(interpreted[:6]) + "."
            )

        warnings = [
            "A proposta depende de aprovação explícita antes de alterar a interface.",
            "A aplicação será recusada se o contexto da página tiver mudado.",
        ]
        if reference_only:
            warnings.append(
                "Anexos sem trecho textual foram mantidos apenas como referência nominal: "
                + ", ".join(reference_only[:6])
                + "."
            )
        if not actions:
            warnings.append("O pedido precisa de mais contexto para gerar uma ação segura.")

        return {
            "proposal_id": uuid4(),
            "status": "awaiting_approval",
            "engine": "meu-executivo-operational-rules-v1",
            "domain": payload.domain,
            "summary": self._summary(payload.domain, actions),
            "rationale": rationale,
            "suggestions": self._suggestions(payload.domain),
            "questions": [] if actions else ["Qual resultado você quer preparar nesta página?"],
            "warnings": warnings,
            "actions": actions,
            "playbook_name": playbook_name,
            "playbook_version": playbook_version,
            "account_context_used": bool(payload.context_snapshot or playbook_name),
            "attachment_count": len(payload.attachments),
            "approval_required": True,
            "external_actions_executed": 0,
            "generated_at": datetime.now(UTC),
        }

    async def _effective_playbook(
        self,
        workspace_id: UUID,
        context: dict[str, Any],
    ) -> tuple[str | None, int | None]:
        area = context.get("practice_area")
        if not isinstance(area, str) or not area.strip():
            area = None
        try:
            playbook = await PlaybookService(self.db).effective(
                workspace_id=workspace_id,
                selectors={
                    PlaybookScopeDimension.area: area,
                    PlaybookScopeDimension.channel: None,
                    PlaybookScopeDimension.stage: None,
                    PlaybookScopeDimension.agent: None,
                    PlaybookScopeDimension.offer: None,
                },
            )
        except PlaybookNotFoundError:
            return None, None
        return (
            playbook.name,
            playbook.published_version or playbook.current_version,
        )

    @staticmethod
    def _summary(domain: str, actions: list[dict[str, Any]]) -> str:
        names = {
            "playbooks": "Playbooks",
            "tracking": "Rastreamento",
            "connections": "Central de Conexões",
            "social_comments": "Comentários Sociais",
            "agenda": "Agenda",
        }
        if not actions:
            return f"Preciso de mais contexto para preparar uma mudança segura em {names[domain]}."
        return (
            f"{len(actions)} ação(ões) preparada(s) em {names[domain]}. "
            "Revise a fila e aprove somente o que deseja aplicar."
        )

    @staticmethod
    def _suggestions(domain: str) -> list[str]:
        return {
            "playbooks": [
                "Revisar lacunas de cobertura e simulações antes de publicar.",
                "Criar um rascunho por área ou produto sem alterar a versão efetiva.",
            ],
            "tracking": [
                "Comparar atribuição, vendas e qualidade por período.",
                "Preparar feedback de conversão antes de qualquer envio ao provedor.",
            ],
            "connections": [
                "Validar primeiro o canal e o status da conexão.",
                "Nunca colar tokens, segredos ou PINs no copiloto.",
            ],
            "social_comments": [
                "Preparar resposta privada e regra de automação sem enviar ou ativar.",
                "Revisar elegibilidade e contexto antes de responder.",
            ],
            "agenda": [
                "Abrir um rascunho com data e hora antes de criar a reunião.",
                "Revisar o resumo diário e conflitos antes de confirmar.",
            ],
        }[domain]

    def _actions(
        self,
        domain: str,
        instruction: str,
        context: dict[str, Any],
    ) -> list[dict[str, Any]]:
        planner = {
            "playbooks": self._playbook_actions,
            "tracking": self._tracking_actions,
            "connections": self._connection_actions,
            "social_comments": self._social_actions,
            "agenda": self._agenda_actions,
        }[domain]
        return planner(instruction, context)

    @staticmethod
    def _playbook_actions(
        instruction: str,
        context: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if any(term in instruction for term in ("novo", "crie", "criar", "rascunho", "modelo")):
            title = re.sub(
                r"^(crie|criar|novo|nova|faça|faca)\s+",
                "",
                instruction,
                flags=re.IGNORECASE,
            ).strip()
            return [_action(
                label="Preparar rascunho de playbook",
                description="Preenche o formulário local; nada é criado até a confirmação humana.",
                kind="local_draft",
                risk="medium",
                permission="configuracoes:edit",
                payload={
                    "type": "playbooks.create_draft",
                    "name": title[:160] or "Novo playbook",
                    "description": "Rascunho preparado pelo copiloto para revisão humana.",
                    "autonomy_mode": "ai_triage_human_meeting",
                },
            )]
        if any(term in instruction for term in ("abrir", "editar")):
            selected_id = context.get("selected_playbook_id")
            if isinstance(selected_id, str) and selected_id:
                return [_action(
                    label="Abrir playbook selecionado",
                    description="Navega para o editor do registro já selecionado.",
                    kind="ui_update",
                    risk="low",
                    permission="configuracoes:view",
                    payload={"type": "playbooks.open_editor", "playbook_id": selected_id},
                )]
        return [_action(
            label="Mostrar lacunas prioritárias",
            description="Ordena a biblioteca pela menor cobertura estrutural.",
            kind="ui_update",
            risk="low",
            permission="configuracoes:view",
            payload={"type": "playbooks.review_gaps"},
        )]

    @staticmethod
    def _tracking_actions(
        instruction: str,
        _context: dict[str, Any],
    ) -> list[dict[str, Any]]:
        actions: list[dict[str, Any]] = []
        tab_terms = {
            "atribu": "atribuicao",
            "capta": "captacao",
            "evento": "eventos",
            "funil": "funil",
            "jornada": "jornadas",
            "qualidade": "qualidade",
            "feedback": "feedback",
            "remarketing": "remarketing",
            "canal": "canais",
        }
        for term, tab in tab_terms.items():
            if term in instruction:
                actions.append(_action(
                    label=f"Abrir visão de {tab}",
                    description="Troca somente a visão do painel de rastreamento.",
                    kind="ui_update",
                    risk="low",
                    permission="relatorios:view",
                    payload={"type": "tracking.select_tab", "tab": tab},
                ))
                break
        period = re.search(r"\b(7|14|30|60|90|180|365)\s*dias?\b", instruction)
        if period:
            actions.append(_action(
                label=f"Comparar últimos {period.group(1)} dias",
                description="Atualiza o período do painel sem enviar dados a provedores.",
                kind="ui_update",
                risk="low",
                permission="relatorios:view",
                payload={"type": "tracking.set_period", "period_days": int(period.group(1))},
            ))
        if "feedback" in instruction and any(
            term in instruction for term in ("prepar", "convers", "venda")
        ):
            actions.append(_action(
                label="Preparar feedback de conversão",
                description="Gera a prévia local; não despacha eventos para Meta ou Google.",
                kind="local_write",
                risk="medium",
                permission="integrations:manage",
                payload={"type": "tracking.prepare_feedback"},
            ))
        if not actions or any(term in instruction for term in ("atualiz", "recarreg", "refresh")):
            actions.append(_action(
                label="Atualizar leitura do rastreamento",
                description="Reconsulta os dados locais do painel.",
                kind="local_read",
                risk="low",
                permission="relatorios:view",
                payload={"type": "tracking.refresh"},
            ))
        return actions[:4]

    @staticmethod
    def _connection_actions(
        instruction: str,
        _context: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if any(term in instruction for term in ("conectar whatsapp", "novo número", "novo numero")):
            return [_action(
                label="Abrir assistente do WhatsApp",
                description="Abre o passo a passo; nenhuma credencial é preenchida ou enviada.",
                kind="ui_update",
                risk="medium",
                permission="integrations:manage",
                payload={"type": "connections.start_whatsapp_wizard"},
            )]
        section = "overview"
        if "instagram" in instruction:
            section = "instagram"
        elif "facebook" in instruction or "messenger" in instruction:
            section = "facebook"
        elif "whatsapp" in instruction:
            section = "whatsapp"
        if any(term in instruction for term in ("atualiz", "recarreg", "status")):
            return [_action(
                label="Atualizar conexões",
                description="Reconsulta o status local das conexões existentes.",
                kind="local_read",
                risk="low",
                permission="integrations:view",
                payload={"type": "connections.refresh"},
            )]
        return [_action(
            label="Ir para a conexão relevante",
            description="Leva ao bloco certo sem sair da página.",
            kind="ui_update",
            risk="low",
            permission="integrations:view",
            payload={"type": "connections.focus_section", "section": section},
        )]

    @staticmethod
    def _social_actions(
        instruction: str,
        context: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if any(
            term in instruction
            for term in ("regra", "automação", "automacao", "palavra-chave")
        ):
            keyword_match = re.search(
                r"(?:palavra(?:-chave)?|coment[aá]rio)\s+[\"']?([^\"',.;]{2,80})",
                instruction,
            )
            return [_action(
                label="Preparar regra de comentários",
                description="Preenche a regra como rascunho; não ativa nem responde.",
                kind="local_draft",
                risk="medium",
                permission="automations:manage",
                payload={
                    "type": "social.prepare_rule",
                    "keyword": keyword_match.group(1).strip() if keyword_match else "",
                    "case_sensitive": "diferenciar maiúsc" in instruction,
                },
            )]
        if any(term in instruction for term in ("respon", "mensagem", "dm", "privad")):
            return [_action(
                label="Preparar resposta",
                description="Coloca uma sugestão no editor; nenhum conteúdo é enviado.",
                kind="local_draft",
                risk="medium",
                permission="mensagens:send_message",
                payload={
                    "type": "social.prepare_reply",
                    "mode": (
                        "private"
                        if any(term in instruction for term in ("dm", "privad"))
                        else "public"
                    ),
                    "body": str(context.get("suggested_reply") or "")[:2000],
                },
            )]
        view = (
            "hot_leads"
            if any(term in instruction for term in ("quente", "prioridade"))
            else "queue"
        )
        return [_action(
            label="Abrir fila relevante",
            description="Troca a visão sem executar ações nos canais sociais.",
            kind="ui_update",
            risk="low",
            permission="mensagens:view",
            payload={"type": "social.select_view", "view": view},
        )]

    @staticmethod
    def _agenda_actions(
        instruction: str,
        context: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if any(term in instruction for term in ("agend", "reuni", "compromisso", "crie")):
            selected_date: str | None = None
            context_date = context.get("selected_date")
            try:
                local_reference_date = (
                    date.fromisoformat(context_date)
                    if isinstance(context_date, str)
                    else datetime.now(UTC).date()
                )
            except ValueError:
                local_reference_date = datetime.now(UTC).date()
            date_match = re.search(r"\b(\d{2})/(\d{2})/(\d{4})\b", instruction)
            if date_match:
                selected_date = (
                    f"{date_match.group(3)}-{date_match.group(2)}-{date_match.group(1)}"
                )
            elif "amanhã" in instruction or "amanha" in instruction:
                selected_date = (local_reference_date + timedelta(days=1)).isoformat()
            elif "hoje" in instruction:
                selected_date = local_reference_date.isoformat()
            hour_match = re.search(r"\b(?:às|as)?\s*(\d{1,2})(?::(\d{2})|h)\b", instruction)
            return [_action(
                label="Preparar reunião",
                description="Abre um rascunho de reunião; nada é criado antes de salvar.",
                kind="local_draft",
                risk="medium",
                permission="kanban:edit",
                payload={
                    "type": "agenda.open_create",
                    "date": selected_date,
                    "hour": int(hour_match.group(1)) if hour_match else None,
                    "minute": int(hour_match.group(2) or 0) if hour_match else 0,
                },
            )]
        if "link" in instruction and any(
            term in instruction for term in ("agenda", "marcar", "booking")
        ):
            return [_action(
                label="Abrir gerador de link",
                description="Abre o formulário local do link de agendamento.",
                kind="ui_update",
                risk="low",
                permission="kanban:edit",
                payload={"type": "agenda.open_booking_link"},
            )]
        if any(term in instruction for term in ("config", "disponibilidade")):
            return [_action(
                label="Abrir configurações da agenda",
                description="Navega para disponibilidade e conexões da agenda.",
                kind="ui_update",
                risk="low",
                permission="lista:view",
                payload={"type": "agenda.open_settings"},
            )]
        if any(term in instruction for term in ("atualiz", "recarreg", "refresh")):
            return [_action(
                label="Atualizar agenda local",
                description="Reconsulta compromissos; nenhuma sincronização externa é presumida.",
                kind="local_read",
                risk="low",
                permission="lista:view",
                payload={"type": "agenda.refresh"},
            )]
        return [_action(
            label="Mostrar resumo do dia",
            description="Leva ao briefing diário sem alterar compromissos.",
            kind="ui_update",
            risk="low",
            permission="lista:view",
            payload={"type": "agenda.focus_daily_briefing"},
        )]
