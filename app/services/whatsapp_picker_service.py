"""Read and delivery orchestration for the approved WhatsApp template picker.

This module deliberately keeps the picker read-only.  Template classification is
never inferred here; a null intent remains a null intent and is sorted last.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import logger
from app.models.client import Client
from app.models.finance import FinanceEntry, FinanceEntryType
from app.models.klm_operations import OmnichannelThread
from app.models.matter import Matter
from app.models.meeting import Meeting, MeetingStatus
from app.models.omnichannel_v6 import WorkspaceTeam, WorkspaceTeamMember
from app.models.user import User
from app.models.whatsapp import WaMessageDirection, WhatsAppContact, WhatsAppMessage
from app.models.whatsapp_campaign import (
    CampaignRecipientStatus,
    WhatsAppCampaign,
    WhatsAppCampaignRecipient,
    WhatsAppCampaignTemplateSnapshot,
    WhatsAppMetaTemplate,
    WhatsAppTemplateIntent,
)
from app.schemas.whatsapp_campaign import (
    WhatsAppTemplateButtonRead,
    WhatsAppTemplateCardRead,
    WhatsAppTemplateHeaderRead,
    WhatsAppTemplatePickerRead,
    WhatsAppTemplateSuggestionRead,
    WhatsAppTemplateVariableBindingRead,
    WhatsAppTemplateVariableRead,
    WhatsAppVariableFieldRead,
    WhatsAppVariableGroupRead,
    WhatsAppVariablesRead,
)
from app.services.finance_predicates import (
    OPEN_RECEIVABLE_STATUSES,
    finance_entry_overdue_predicate,
)

_VARIABLE_RE = re.compile(r"\{\{\s*(\d+)\s*\}\}")
_SECTOR_BY_INTENT: dict[WhatsAppTemplateIntent, str] = {
    WhatsAppTemplateIntent.proposta: "comercial",
    WhatsAppTemplateIntent.follow_up: "comercial",
    WhatsAppTemplateIntent.reativacao: "comercial",
    WhatsAppTemplateIntent.documentos: "juridico",
    WhatsAppTemplateIntent.audiencia: "juridico",
    WhatsAppTemplateIntent.cobranca: "financeiro",
}
_SUGGESTION_INTENT = {
    "conversation_cold": WhatsAppTemplateIntent.reativacao,
    "matter_awaiting_documents": WhatsAppTemplateIntent.documentos,
    "receivable_open": WhatsAppTemplateIntent.cobranca,
    "hearing_upcoming": WhatsAppTemplateIntent.audiencia,
}
_SUGGESTION_RANK = {
    "conversation_cold": 1,
    "matter_awaiting_documents": 2,
    "receivable_open": 3,
    "hearing_upcoming": 4,
}
_FAILED_STATUSES = {
    CampaignRecipientStatus.failed,
    CampaignRecipientStatus.skipped,
}


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _component(template: WhatsAppMetaTemplate, kind: str) -> dict[str, Any] | None:
    wanted = kind.upper()
    for item in template.components or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("type", "")).upper() == wanted:
            return item
    return None


def _body_text(template: WhatsAppMetaTemplate) -> str:
    body = _component(template, "BODY")
    if body and body.get("text") is not None:
        return str(body["text"])
    raw = template.raw_payload or {}
    return str(raw.get("body") or raw.get("body_text") or "")


def _examples(component: dict[str, Any] | None, key: str) -> list[str]:
    if not component:
        return []
    example = component.get("example") or {}
    values = example.get(key) or []
    if values and isinstance(values[0], list):
        values = values[0]
    return [str(value) for value in values if value is not None]


def _variable_count(text: str) -> list[int]:
    return sorted({int(match.group(1)) for match in _VARIABLE_RE.finditer(text)})


def _mapped_field_key(template: WhatsAppMetaTemplate, scope: str, position: int) -> str | None:
    """Read an explicit variable-to-field mapping from locally stored metadata.

    Meta does not provide semantic field bindings.  Therefore this helper is
    intentionally conservative: it only promotes a variable to ``field`` when
    the synchronised/local payload explicitly declares a field key.  Unknown
    variables remain prompts and can still be filled at send time.
    """

    raw = template.raw_payload or {}
    containers = [
        raw.get(key)
        for key in (
            "variableBindings",
            "variable_bindings",
            "templateBindings",
            "template_bindings",
            "sequenceBindings",
            "sequence_bindings",
        )
        if raw.get(key) is not None
    ]

    def parse_position(value: Any) -> int | None:
        try:
            parsed = int(str(value))
        except (TypeError, ValueError):
            return None
        if parsed < 0:
            return None
        return parsed if scope == "button" or parsed > 0 else None

    def visit(
        value: Any, inherited_scope: str | None = None, inherited_position: int | None = None
    ) -> str | None:
        if isinstance(value, dict):
            local_scope = str(
                value.get("scope") or value.get("variableScope") or inherited_scope or ""
            ).casefold()
            raw_position = value.get("position")
            if raw_position is None:
                raw_position = value.get("index")
            if raw_position is None:
                raw_position = value.get("variablePosition")
            if raw_position is None:
                raw_position = inherited_position
            local_position = parse_position(raw_position)
            field_key = value.get("fieldKey") or value.get("field_key")
            if (
                local_scope == scope
                and local_position == position
                and isinstance(field_key, str)
                and field_key.strip()
            ):
                return field_key.strip()
            for key, child in value.items():
                key_text = str(key).casefold()
                child_scope = (
                    local_scope if local_scope in {"header", "body", "button"} else inherited_scope
                )
                child_position = local_position or inherited_position
                if key_text in {"header", "body", "button"}:
                    child_scope = key_text
                else:
                    match = re.fullmatch(r"(header|body|button)[._-]?(\d+)", key_text)
                    if match:
                        child_scope = match.group(1)
                        child_position = parse_position(match.group(2))
                    elif child_position is None:
                        child_position = parse_position(key)
                found = visit(child, child_scope, child_position)
                if found:
                    return found
        elif isinstance(value, list):
            for index, child in enumerate(value, start=1):
                found = visit(child, inherited_scope, inherited_position or index)
                if found:
                    return found
        elif (
            isinstance(value, str)
            and inherited_scope == scope
            and inherited_position == position
            and value.strip()
        ):
            return value.strip()
        return None

    for container in containers:
        mapped = visit(container)
        if mapped:
            return mapped
    return None


def _variable_binding(
    template: WhatsAppMetaTemplate, scope: str, position: int
) -> WhatsAppTemplateVariableBindingRead:
    field_key = _mapped_field_key(template, scope, position)
    if field_key:
        return WhatsAppTemplateVariableBindingRead(kind="field", field_key=field_key)
    return WhatsAppTemplateVariableBindingRead(kind="prompt")


def _sector(intent: WhatsAppTemplateIntent | None) -> str | None:
    return _SECTOR_BY_INTENT.get(intent) if intent else None


class WhatsAppPickerService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def picker(
        self,
        workspace_id: UUID,
        *,
        thread_id: UUID | None = None,
        matter_id: UUID | None = None,
        user_id: UUID | None = None,
        search: str | None = None,
        intent: WhatsAppTemplateIntent | None = None,
    ) -> WhatsAppTemplatePickerRead:
        contact, matter = await self._resolve_context(
            workspace_id, thread_id=thread_id, matter_id=matter_id
        )
        templates = await self._approved_templates(workspace_id, intent=intent)
        sequence_template_rows = list(
            (
                await self.db.scalars(
                    select(WhatsAppMetaTemplate).where(
                        WhatsAppMetaTemplate.workspace_id == workspace_id,
                        WhatsAppMetaTemplate.status.in_(("approved", "pending")),
                        WhatsAppMetaTemplate.removed_at_source_at.is_(None),
                    )
                )
            ).all()
        )
        stats = await self._usage_stats(workspace_id, contact_id=contact.id if contact else None)
        cards = [self._card(template, stats.get(template.id, {})) for template in templates]
        sequence_templates = [
            self._card(template, stats.get(template.id, {}))
            for template in sequence_template_rows
        ]

        if search:
            needle = search.strip().casefold()
            cards = [
                card
                for card in cards
                if needle in card.body_text.casefold()
                or needle in (card.name or "").casefold()
                or needle in (card.intent.value if card.intent else "").casefold()
            ]
            sequence_templates = [
                card
                for card in sequence_templates
                if needle in card.body_text.casefold()
                or needle in (card.name or "").casefold()
                or needle in (card.intent.value if card.intent else "").casefold()
            ]
        cards.sort(
            key=lambda card: (
                -(card.usage_count30d or 0),
                card.last_used_at is None,
                -(card.last_used_at.timestamp() if card.last_used_at else 0),
                card.intent is None,
                card.name.casefold(),
            )
        )
        sector_mode, user_sector = await self._sector_mode(workspace_id, user_id)
        suggestions = await self._suggestions(
            workspace_id,
            contact=contact,
            matter=matter,
            cards=cards,
        )
        return WhatsAppTemplatePickerRead(
            cards=cards,
            sequence_templates=sequence_templates,
            suggestions=suggestions,
            sector_mode=sector_mode,
            user_sector=user_sector,
            refreshed_at=datetime.now(UTC),
        )

    async def variables(
        self,
        workspace_id: UUID,
        *,
        contact_id: UUID | None = None,
        matter_id: UUID | None = None,
    ) -> WhatsAppVariablesRead:
        contact, matter = await self._resolve_context(
            workspace_id, contact_id=contact_id, matter_id=matter_id
        )
        client = None
        if matter is not None:
            client = await self.db.scalar(select(Client).where(Client.id == matter.client_id))

        first_name = (contact.name or "").split()[0] if contact and contact.name else None
        groups = [
            WhatsAppVariableGroupRead(
                key="client",
                label="Cliente",
                values=[
                    WhatsAppVariableFieldRead(
                        field_key="contact.first_name",
                        label="Primeiro nome",
                        resolved_value=first_name,
                    ),
                    WhatsAppVariableFieldRead(
                        field_key="contact.name",
                        label="Nome completo",
                        resolved_value=contact.name if contact else None,
                    ),
                    WhatsAppVariableFieldRead(
                        field_key="contact.phone",
                        label="Telefone",
                        resolved_value=contact.phone if contact else None,
                    ),
                    WhatsAppVariableFieldRead(
                        field_key="client.email",
                        label="E-mail",
                        resolved_value=client.email if client else None,
                    ),
                ],
            ),
            WhatsAppVariableGroupRead(
                key="case",
                label="Caso",
                values=[
                    WhatsAppVariableFieldRead(
                        field_key="matter.case_number",
                        label="Número do caso",
                        resolved_value=matter.case_number if matter else None,
                    ),
                    WhatsAppVariableFieldRead(
                        field_key="matter.demand_type",
                        label="Tipo de demanda",
                        resolved_value=matter.demand_type if matter else None,
                    ),
                    WhatsAppVariableFieldRead(
                        field_key="matter.practice_area",
                        label="Área",
                        resolved_value=(
                            getattr(matter.practice_area, "value", matter.practice_area)
                            if matter
                            else None
                        ),
                    ),
                ],
            ),
            WhatsAppVariableGroupRead(
                key="office",
                label="Escritório",
                values=[
                    WhatsAppVariableFieldRead(
                        field_key="workspace.name",
                        label="Escritório",
                        resolved_value=await self._workspace_name(workspace_id),
                    )
                ],
            ),
            WhatsAppVariableGroupRead(
                key="financial",
                label="Financeiro",
                values=[
                    WhatsAppVariableFieldRead(
                        field_key="matter.open_balance",
                        label="Saldo em aberto",
                        resolved_value=await self._open_balance(
                            workspace_id, matter.id if matter else None
                        ),
                    )
                ],
            ),
        ]
        return WhatsAppVariablesRead(groups=groups)

    async def _approved_templates(
        self, workspace_id: UUID, *, intent: WhatsAppTemplateIntent | None
    ) -> list[WhatsAppMetaTemplate]:
        statement = select(WhatsAppMetaTemplate).where(
            WhatsAppMetaTemplate.workspace_id == workspace_id,
            WhatsAppMetaTemplate.status == "approved",
            WhatsAppMetaTemplate.removed_at_source_at.is_(None),
        )
        if intent is not None:
            statement = statement.where(WhatsAppMetaTemplate.intent == intent)
        return list((await self.db.scalars(statement)).all())

    async def _usage_stats(
        self, workspace_id: UUID, *, contact_id: UUID | None
    ) -> dict[UUID, dict[str, Any]]:
        cutoff = datetime.now(UTC) - timedelta(days=30)
        statement = select(
            WhatsAppCampaignRecipient.template_id,
            WhatsAppCampaignRecipient.contact_id,
            WhatsAppCampaignRecipient.created_at,
            WhatsAppCampaignRecipient.sent_at,
            WhatsAppCampaignRecipient.status,
        ).where(
            WhatsAppCampaignRecipient.workspace_id == workspace_id,
            WhatsAppCampaignRecipient.created_at >= cutoff,
            WhatsAppCampaignRecipient.template_id.is_not(None),
        )
        rows = list((await self.db.execute(statement)).all())
        legacy_rows = list(
            (
                await self.db.execute(
                    select(
                        WhatsAppCampaignTemplateSnapshot.source_template_id,
                        WhatsAppCampaignRecipient.contact_id,
                        WhatsAppCampaignRecipient.created_at,
                        WhatsAppCampaignRecipient.sent_at,
                        WhatsAppCampaignRecipient.status,
                    )
                    .select_from(WhatsAppCampaignRecipient)
                    .join(
                        WhatsAppCampaignTemplateSnapshot,
                        WhatsAppCampaignTemplateSnapshot.campaign_id
                        == WhatsAppCampaignRecipient.campaign_id,
                    )
                    .join(
                        WhatsAppCampaign,
                        WhatsAppCampaign.id == WhatsAppCampaignRecipient.campaign_id,
                    )
                    .where(
                        WhatsAppCampaignRecipient.workspace_id == workspace_id,
                        WhatsAppCampaignRecipient.template_id.is_(None),
                        WhatsAppCampaignTemplateSnapshot.source_template_id.is_not(None),
                        WhatsAppCampaignRecipient.created_at >= cutoff,
                    )
                )
            ).all()
        )
        stats: dict[UUID, dict[str, Any]] = defaultdict(
            lambda: {"count": 0, "last_used": None, "last_contact": None}
        )
        for template_id, row_contact_id, created_at, sent_at, status in rows:
            if status in _FAILED_STATUSES:
                continue
            item = stats[template_id]
            item["count"] += 1
            used_at = sent_at or created_at
            if used_at and (item["last_used"] is None or used_at > item["last_used"]):
                item["last_used"] = used_at
            if contact_id and row_contact_id == contact_id and sent_at:
                if item["last_contact"] is None or sent_at > item["last_contact"]:
                    item["last_contact"] = sent_at
        for template_id, row_contact_id, created_at, sent_at, status in legacy_rows:
            if status in _FAILED_STATUSES or template_id is None:
                continue
            item = stats[template_id]
            item["count"] += 1
            used_at = sent_at or created_at
            if used_at and (item["last_used"] is None or used_at > item["last_used"]):
                item["last_used"] = used_at
            if contact_id and row_contact_id == contact_id and sent_at:
                if item["last_contact"] is None or sent_at > item["last_contact"]:
                    item["last_contact"] = sent_at
        return stats

    def _card(
        self, template: WhatsAppMetaTemplate, stats: dict[str, Any]
    ) -> WhatsAppTemplateCardRead:
        body = _component(template, "BODY")
        header_component = _component(template, "HEADER")
        footer = _component(template, "FOOTER")
        body_text = _body_text(template)
        body_examples = _examples(body, "body_text")
        header_text = str(header_component.get("text") or "") if header_component else ""
        header_examples = _examples(header_component, "header_text")
        variables = [
            WhatsAppTemplateVariableRead(
                position=position,
                scope="body",
                binding=_variable_binding(template, "body", position),
                example=body_examples[position - 1] if position <= len(body_examples) else None,
            )
            for position in _variable_count(body_text)
        ]
        variables = [
            WhatsAppTemplateVariableRead(
                position=position,
                scope="header",
                binding=_variable_binding(template, "header", position),
                example=header_examples[position - 1] if position <= len(header_examples) else None,
            )
            for position in _variable_count(header_text)
        ] + variables
        header = None
        if header_component:
            header = WhatsAppTemplateHeaderRead(
                type=str(
                    header_component.get("format") or header_component.get("type") or "TEXT"
                ).upper(),
                text=header_component.get("text"),
            )
        buttons: list[WhatsAppTemplateButtonRead] = []
        buttons_component = _component(template, "BUTTONS")
        button_items = (buttons_component or {}).get("buttons", []) if buttons_component else []
        for button_index, button in enumerate(button_items):
            if not isinstance(button, dict):
                continue
            url = button.get("url")
            buttons.append(
                WhatsAppTemplateButtonRead(
                    type=str(button.get("type") or "").upper(),
                    label=str(button.get("text") or button.get("label") or ""),
                    url=str(url) if url else None,
                    has_dynamic_suffix=bool(url and _VARIABLE_RE.search(str(url))),
                )
            )
            if url:
                button_examples = button.get("example") or []
                if _VARIABLE_RE.search(str(url)):
                    variables.append(
                        WhatsAppTemplateVariableRead(
                            # Meta addresses dynamic URL components by the
                            # real zero-based BUTTONS-array index, not by the
                            # {{1}} placeholder inside the URL.
                            position=button_index,
                            scope="button",
                            binding=_variable_binding(template, "button", button_index),
                            example=str(button_examples[0]) if button_examples else None,
                        )
                    )
        raw = template.raw_payload or {}
        return WhatsAppTemplateCardRead(
            id=template.id,
            meta_template_id=template.external_template_id,
            name=template.name,
            language=template.language,
            intent=template.intent,
            sector=_sector(template.intent),
            meta_category=template.category,
            status=str(getattr(template.status, "value", template.status) or "pending").lower(),
            header=header,
            body_text=body_text,
            footer_text=footer.get("text") if footer else None,
            buttons=buttons,
            variables=variables,
            is_sequence=bool(
                raw.get("isSequence") or raw.get("is_sequence") or raw.get("sequence")
            ),
            sequence_step_count=raw.get("sequenceStepCount") or raw.get("sequence_step_count"),
            usage_count30d=stats.get("count", 0),
            last_used_at=stats.get("last_used"),
            last_sent_to_contact_at=stats.get("last_contact"),
        )

    async def _suggestions(
        self,
        workspace_id: UUID,
        *,
        contact: WhatsAppContact | None,
        matter: Matter | None,
        cards: list[WhatsAppTemplateCardRead],
    ) -> list[WhatsAppTemplateSuggestionRead]:
        if not contact and not matter:
            return []
        now = datetime.now(UTC)
        rules: list[tuple[str, str]] = []
        if contact:
            last_inbound = await self.db.scalar(
                select(func.max(WhatsAppMessage.timestamp)).where(
                    WhatsAppMessage.contact_id == contact.id,
                    WhatsAppMessage.direction == WaMessageDirection.inbound,
                )
            )
            last_inbound = _utc(last_inbound)
            if last_inbound and now - last_inbound > timedelta(days=3):
                days = max(3, (now - last_inbound).days)
                rules.append(("conversation_cold", f"sem resposta há {days} dias"))
        if matter:
            state = str(getattr(matter.column_id, "value", matter.column_id) or "").casefold()
            micro = str(getattr(matter.micro_status, "value", matter.micro_status) or "").casefold()
            if "triag" in state or "aguardando" in state or "document" in micro:
                rules.append(("matter_awaiting_documents", "caso aguardando documentos"))
            receivable = await self.db.scalar(
                select(func.count(FinanceEntry.id)).where(
                    FinanceEntry.workspace_id == workspace_id,
                    FinanceEntry.matter_id == matter.id,
                    FinanceEntry.entry_type == FinanceEntryType.receivable,
                    finance_entry_overdue_predicate(now),
                )
            )
            if receivable:
                rules.append(("receivable_open", "há parcela vencida no financeiro"))
            hearing = await self.db.scalar(
                select(func.count(Meeting.id)).where(
                    Meeting.workspace_id == workspace_id,
                    Meeting.matter_id == matter.id,
                    Meeting.starts_at >= now,
                    Meeting.starts_at <= now + timedelta(days=7),
                    Meeting.status != MeetingStatus.cancelled,
                )
            )
            if hearing:
                rules.append(("hearing_upcoming", "audiência nos próximos 7 dias"))

        by_intent: dict[WhatsAppTemplateIntent, WhatsAppTemplateCardRead] = {}
        for card in cards:
            if card.intent is not None:
                by_intent.setdefault(card.intent, card)
        suggestions = []
        for reason_code, reason_text in sorted(rules, key=lambda item: _SUGGESTION_RANK[item[0]]):
            card = by_intent.get(_SUGGESTION_INTENT[reason_code])
            if card is None:
                continue
            suggestions.append(
                WhatsAppTemplateSuggestionRead(
                    template_id=card.id,
                    reason_code=reason_code,
                    reason_text=reason_text,
                    rank=len(suggestions) + 1,
                )
            )
        return suggestions[:3]

    async def _resolve_context(
        self,
        workspace_id: UUID,
        *,
        thread_id: UUID | None = None,
        contact_id: UUID | None = None,
        matter_id: UUID | None = None,
        require_unambiguous_contact: bool = False,
    ) -> tuple[WhatsAppContact | None, Matter | None]:
        contact = None
        matter = None
        if contact_id:
            contact = await self.db.scalar(
                select(WhatsAppContact).where(
                    WhatsAppContact.id == contact_id,
                    WhatsAppContact.workspace_id == workspace_id,
                )
            )
            if contact is not None:
                matter_id = matter_id or contact.matter_id
            else:
                # Conversation callers sometimes provide the CRM Client UUID
                # as contactId.  Keep resolving through that client's case
                # rather than treating the UUID mismatch as a hard miss.
                client = await self.db.scalar(
                    select(Client).where(
                        Client.id == contact_id,
                        Client.workspace_id == workspace_id,
                    )
                )
                if client is not None and matter_id is None:
                    matter_id = await self.db.scalar(
                        select(Matter.id)
                        .where(
                            Matter.client_id == client.id,
                            Matter.workspace_id == workspace_id,
                        )
                        .order_by(Matter.updated_at.desc())
                        .limit(1)
                    )
        elif thread_id:
            # The legacy conversation surface uses the contact UUID as its
            # thread reference.  This is also safe for omnichannel callers:
            # no cross-workspace fallback is attempted.
            contact = await self.db.scalar(
                select(WhatsAppContact).where(
                    WhatsAppContact.id == thread_id,
                    WhatsAppContact.workspace_id == workspace_id,
                )
            )
            if contact:
                matter_id = matter_id or contact.matter_id
            else:
                thread = await self.db.scalar(
                    select(OmnichannelThread).where(
                        OmnichannelThread.id == thread_id,
                        OmnichannelThread.workspace_id == workspace_id,
                    )
                )
                if thread:
                    matter_id = matter_id or thread.matter_id
                    metadata = thread.thread_metadata or {}
                    raw_contact_id = metadata.get("contactId") or metadata.get("contact_id")
                    if raw_contact_id:
                        try:
                            contact = await self.db.scalar(
                                select(WhatsAppContact).where(
                                    WhatsAppContact.id == UUID(str(raw_contact_id)),
                                    WhatsAppContact.workspace_id == workspace_id,
                                )
                            )
                        except ValueError:
                            contact = None
        if matter_id:
            matter = await self.db.scalar(
                select(Matter).where(Matter.id == matter_id, Matter.workspace_id == workspace_id)
            )
            if contact is None:
                contact = await self._contact_for_matter(
                    workspace_id, matter, matter_id, strict=require_unambiguous_contact
                )
        return contact, matter

    async def _contact_for_matter(
        self,
        workspace_id: UUID,
        matter: Matter | None,
        matter_id: UUID,
        *,
        strict: bool,
    ) -> WhatsAppContact | None:
        """S9: um caso pode ter vários contatos (cônjuge, procurador, telefone
        antigo, campanhas). Prefere o contato cujo telefone é o do cliente do
        caso; em modo estrito (envio oficial), ambiguidade devolve None em vez de
        um contato arbitrário; fora dele (variáveis, sugestões) mantém o
        comportamento antigo (o mais antigo)."""
        from app.services.whatsapp.helpers import phone_number_lookup_variants

        candidates = list(
            (
                await self.db.scalars(
                    select(WhatsAppContact)
                    .where(
                        WhatsAppContact.workspace_id == workspace_id,
                        WhatsAppContact.matter_id == matter_id,
                    )
                    .order_by(WhatsAppContact.created_at.asc())
                )
            ).all()
        )
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]
        client_phone = None
        if matter is not None and matter.client_id:
            client_phone = await self.db.scalar(
                select(Client.phone).where(
                    Client.id == matter.client_id, Client.workspace_id == workspace_id
                )
            )
        variants = phone_number_lookup_variants(client_phone) if client_phone else set()
        matches = [item for item in candidates if item.phone and item.phone in variants]
        if len(matches) == 1:
            return matches[0]
        if strict:
            logger.warning(
                "whatsapp_picker.contato_ambiguo",
                workspace_id=str(workspace_id),
                matter_id=str(matter_id),
                candidatos=len(candidates),
                casam_com_cliente=len(matches),
            )
            return None
        return candidates[0]

    async def _sector_mode(
        self, workspace_id: UUID, user_id: UUID | None
    ) -> tuple[str, str | None]:
        rows = (
            await self.db.execute(
                select(
                    WorkspaceTeam.id,
                    WorkspaceTeam.name,
                    WorkspaceTeam.team_type,
                    WorkspaceTeamMember.user_id,
                )
                .join(WorkspaceTeamMember, WorkspaceTeamMember.team_id == WorkspaceTeam.id)
                .join(User, User.id == WorkspaceTeamMember.user_id)
                .where(
                    WorkspaceTeam.workspace_id == workspace_id,
                    WorkspaceTeam.is_active.is_(True),
                    User.is_active.is_(True),
                )
            )
        ).all()
        team_ids = {row[0] for row in rows}
        user_sector = None
        if user_id:
            for _team_id, name, team_type, member_user_id in rows:
                if member_user_id == user_id:
                    user_sector = self._sector_label(name, team_type)
                    if user_sector:
                        break
        return ("by_sector" if len(team_ids) >= 2 else "condensed", user_sector)

    @staticmethod
    def _sector_label(name: str | None, team_type: str | None) -> str | None:
        value = f"{name or ''} {team_type or ''}".casefold()
        for sector in ("financeiro", "comercial", "juridico"):
            if sector in value or (sector == "juridico" and "legal" in value):
                return sector
        return None

    async def _workspace_name(self, workspace_id: UUID) -> str | None:
        from app.models.workspace import Workspace

        return await self.db.scalar(select(Workspace.name).where(Workspace.id == workspace_id))

    async def _open_balance(self, workspace_id: UUID, matter_id: UUID | None) -> str | None:
        if not matter_id:
            return None
        amount = await self.db.scalar(
            select(func.sum(FinanceEntry.amount)).where(
                FinanceEntry.workspace_id == workspace_id,
                FinanceEntry.matter_id == matter_id,
                FinanceEntry.entry_type == FinanceEntryType.receivable,
                FinanceEntry.status.in_(OPEN_RECEIVABLE_STATUSES),
            )
        )
        return f"{amount:.2f}" if amount is not None else None
