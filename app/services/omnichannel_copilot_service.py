"""Deterministic local copilot for the canonical legal inbox."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.klm_operations import OmnichannelMessage, OmnichannelThread
from app.models.matter import Matter
from app.schemas.omnichannel_v6 import OmnichannelCopilotRequest


def _now() -> datetime:
    return datetime.now(UTC)


def _clean(value: str | None, *, limit: int = 1200) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())[:limit]


def _sentence(value: str) -> str:
    clean = _clean(value)
    if not clean:
        return ""
    clean = clean[0].upper() + clean[1:]
    if clean[-1] not in ".!?":
        clean += "."
    return clean


class OmnichannelCopilotService:
    """Produces reviewable local drafts without pretending an LLM was called."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def run(
        self,
        workspace_id: UUID,
        thread_id: UUID,
        payload: OmnichannelCopilotRequest,
    ) -> dict[str, Any]:
        thread = await self.db.scalar(
            select(OmnichannelThread).where(
                OmnichannelThread.workspace_id == workspace_id,
                OmnichannelThread.id == thread_id,
            )
        )
        if not thread:
            raise LookupError("Conversa canônica não encontrada.")
        messages_result = await self.db.execute(
            select(OmnichannelMessage)
            .where(
                OmnichannelMessage.workspace_id == workspace_id,
                OmnichannelMessage.thread_id == thread_id,
            )
            .order_by(OmnichannelMessage.occurred_at.desc())
            .limit(80)
        )
        messages = list(reversed(messages_result.scalars().all()))
        matter = None
        if thread.matter_id:
            matter = await self.db.scalar(
                select(Matter).where(
                    Matter.workspace_id == workspace_id,
                    Matter.id == thread.matter_id,
                )
            )
        client_messages = [row for row in messages if row.direction.value == "inbound"]
        evidence = [row.id for row in client_messages[-12:]]
        unanswered = self._unanswered(messages)
        tasks = self._tasks(messages, matter)

        if payload.action == "rewrite":
            content = self._rewrite(payload.draft or "", payload.tone)
            suggestions = ["Revise nomes, datas e compromissos antes de enviar."]
            confidence = 0.9
        elif payload.action == "suggest_reply":
            content = self._suggest_reply(client_messages, matter, payload.tone)
            suggestions = self._next_step_suggestions(matter, tasks)
            confidence = 0.78 if client_messages else 0.55
        elif payload.action == "unanswered":
            content = (
                "Perguntas ou solicitações ainda sem resposta:\n"
                + "\n".join(f"- {item}" for item in unanswered)
                if unanswered
                else "Não encontrei pergunta recente do cliente sem resposta posterior."
            )
            suggestions = []
            confidence = 0.82
        elif payload.action == "extract_tasks":
            content = (
                "Próximas ações identificadas:\n"
                + "\n".join(f"- {item['title']}" for item in tasks)
                if tasks
                else "Nenhuma tarefa objetiva foi extraída da conversa recente."
            )
            suggestions = []
            confidence = 0.76
        elif payload.action == "meeting_brief":
            content = self._meeting_brief(client_messages, matter, unanswered, tasks)
            suggestions = self._next_step_suggestions(matter, tasks)
            confidence = 0.81 if matter else 0.62
        else:
            content = self._summary(client_messages, matter, unanswered)
            suggestions = self._next_step_suggestions(matter, tasks)
            confidence = 0.8 if client_messages else 0.5

        return {
            "action": payload.action,
            "engine": "klm_local_rules_v1",
            "content": content,
            "suggestions": suggestions,
            "unanswered_questions": unanswered,
            "tasks": tasks,
            "evidence_message_ids": evidence,
            "confidence": confidence,
            "requires_model": False,
            "external_requests": 0,
            "generated_at": _now(),
        }

    @staticmethod
    def _summary(
        client_messages: list[OmnichannelMessage],
        matter: Matter | None,
        unanswered: list[str],
    ) -> str:
        facts = []
        if matter:
            facts.extend(
                item
                for item in [
                    f"Demanda: {_clean(matter.demand_type)}" if matter.demand_type else None,
                    f"Objeto: {_clean(matter.health_object_name)}"
                    if matter.health_object_name
                    else None,
                    f"Operadora: {_clean(matter.health_insurer)}"
                    if matter.health_insurer
                    else None,
                    f"Negativa: {_clean(matter.health_denial_reason)}"
                    if matter.health_denial_reason
                    else None,
                    f"Urgência clínica registrada: {matter.medical_urgency_hours} horas"
                    if matter.medical_urgency_hours
                    else None,
                ]
                if item
            )
        recent = [_clean(row.body, limit=260) for row in client_messages[-4:] if _clean(row.body)]
        if recent:
            facts.append("Relato recente: " + " | ".join(recent))
        if unanswered:
            facts.append(f"Pendências de resposta: {len(unanswered)}")
        return "\n".join(facts) if facts else "Ainda não há conteúdo suficiente para resumir."

    @staticmethod
    def _suggest_reply(
        client_messages: list[OmnichannelMessage],
        matter: Matter | None,
        tone: str,
    ) -> str:
        latest = _clean(client_messages[-1].body, limit=360) if client_messages else ""
        object_name = _clean(matter.health_object_name) if matter else ""
        acknowledgement = (
            f"Entendi sua mensagem sobre {object_name}."
            if object_name
            else "Entendi o que aconteceu e vou organizar as informações com você."
        )
        if latest:
            acknowledgement += f" Você informou: “{latest}”"
        request = (
            "Para avaliarmos o caso com segurança, envie a negativa do plano, o pedido ou "
            "relatório médico e os documentos que já tiver."
        )
        closing = (
            "Assim que recebermos, o jurídico revisa a urgência e informa o próximo passo. "
            "Nosso WhatsApp continuará disponível durante o acompanhamento."
        )
        if tone == "objetivo":
            return f"{acknowledgement} {request} {closing}"
        if tone == "formal":
            return (
                f"Olá. {acknowledgement} {request} Após a análise, retornaremos "
                "com a orientação aplicável."
            )
        if tone == "simples":
            return (
                f"Olá. {acknowledgement} {request} Depois disso, explicamos o "
                "próximo passo por aqui."
            )
        return (
            f"Olá. Sinto muito que você esteja passando por isso. {acknowledgement} "
            f"{request} {closing}"
        )

    @staticmethod
    def _rewrite(draft: str, tone: str) -> str:
        result = _clean(draft, limit=5000)
        replacements = {
            r"\bvc\b": "você",
            r"\bpls\b": "por favor",
            r"\bmsg\b": "mensagem",
            r"\bdoc(s)?\b": "documento",
        }
        for pattern, replacement in replacements.items():
            result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)
        result = _sentence(result)
        if tone == "formal" and not result.lower().startswith(("olá", "prezado", "prezada")):
            result = "Olá. " + result
        if tone == "acolhedor" and "sinto muito" not in result.casefold():
            result = "Sinto muito por essa situação. " + result
        return result

    @staticmethod
    def _unanswered(messages: list[OmnichannelMessage]) -> list[str]:
        last_outbound_index = max(
            (index for index, row in enumerate(messages) if row.direction.value == "outbound"),
            default=-1,
        )
        pending = []
        for row in messages[last_outbound_index + 1 :]:
            if row.direction.value != "inbound":
                continue
            body = _clean(row.body, limit=400)
            if not body:
                continue
            if "?" in body or re.search(
                r"\b(como|quando|onde|qual|quais|pode|posso|preciso|valor|quanto|agendar)\b",
                body,
                flags=re.IGNORECASE,
            ):
                pending.append(body)
        return list(dict.fromkeys(pending))[-8:]

    @staticmethod
    def _tasks(messages: list[OmnichannelMessage], matter: Matter | None) -> list[dict[str, Any]]:
        body = " ".join(_clean(row.body, limit=500) for row in messages[-20:]).casefold()
        tasks: list[dict[str, Any]] = []
        patterns = [
            (
                r"document|negativa|receita|relat[oó]rio|laudo",
                "Conferir e solicitar documentos clínicos e a negativa",
                "documents",
            ),
            (r"agend|reuni[aã]o|hor[aá]rio|meet|teams", "Definir reunião on-line", "meeting"),
            (r"urgente|liminar|internad|tratamento", "Revisar urgência jurídica", "legal_review"),
            (
                r"valor|honor[aá]rio|proposta|contrato",
                "Preparar condições comerciais",
                "commercial",
            ),
        ]
        for pattern, title, kind in patterns:
            if re.search(pattern, body):
                tasks.append({"title": title, "kind": kind, "source": "conversation"})
        if matter and matter.next_action:
            tasks.insert(
                0,
                {
                    "title": _clean(matter.next_action, limit=300),
                    "kind": "matter_next_action",
                    "source": "matter",
                },
            )
        unique: dict[str, dict[str, Any]] = {}
        for task in tasks:
            unique[task["title"]] = task
        return list(unique.values())[:10]

    @classmethod
    def _meeting_brief(
        cls,
        client_messages: list[OmnichannelMessage],
        matter: Matter | None,
        unanswered: list[str],
        tasks: list[dict[str, Any]],
    ) -> str:
        parts = ["Resumo para a reunião:", cls._summary(client_messages, matter, unanswered)]
        if tasks:
            parts.append(
                "Pontos para conduzir:\n" + "\n".join(f"- {row['title']}" for row in tasks)
            )
        parts.append(
            "Confirme na reunião: cronologia da negativa, urgência médica, documentos disponíveis, "
            "tratamento prescrito, expectativa do cliente e condições de contratação."
        )
        return "\n\n".join(parts)

    @staticmethod
    def _next_step_suggestions(matter: Matter | None, tasks: list[dict[str, Any]]) -> list[str]:
        suggestions = [row["title"] for row in tasks[:4]]
        if matter and matter.medical_urgency_hours and matter.medical_urgency_hours <= 72:
            suggestions.insert(0, "Priorizar revisão jurídica por urgência clínica registrada.")
        if not suggestions:
            suggestions = [
                "Confirmar o objeto negado e a data da negativa.",
                "Solicitar documentação médica e comprovante da recusa.",
            ]
        return suggestions[:5]
