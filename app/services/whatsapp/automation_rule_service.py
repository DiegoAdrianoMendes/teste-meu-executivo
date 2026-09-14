from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.klm_runtime import external_delivery_allowed
from app.models.flow import Flow
from app.models.klm_operations import KlmAuditEvent
from app.models.matter import KanbanColumnId, Matter
from app.models.whatsapp import (
    HumanControlState,
    WaQueueStatus,
    WhatsAppAutomationRuleDispatch,
    WhatsAppContact,
)
from app.models.workspace import Workspace
from app.schemas.whatsapp_automation import (
    WhatsAppAutomationSettingsRead,
    WhatsAppAutomationSettingsUpdate,
)

DEFAULT_AUTOMATION_SETTINGS = {
    "enabled": False,
    "onboardingCompleted": False,
    "timezone": "America/Sao_Paulo",
    "businessHoursStart": 8,
    "businessHoursEnd": 19,
    "rules": [
        {
            "id": "welcome-default",
            "label": "Boas-vindas imediatas",
            "enabled": False,
            "triggerType": "welcome",
            "replyText": (
                "Recebi sua mensagem por aqui. Vou organizar seu atendimento "
                "e já sigo com você."
            ),
            "escalateToHuman": False,
            "onlyFirstMessage": True,
            "silenceWindowHours": 0,
        },
        {
            "id": "off-hours-default",
            "label": "Resposta fora do horário",
            "enabled": False,
            "triggerType": "off_hours",
            "replyText": (
                "Estamos fora do horário comercial agora, mas seu atendimento já foi "
                "registrado e a equipe retoma no próximo expediente."
            ),
            "targetQueueStatus": "follow_up",
            "escalateToHuman": False,
            "onlyFirstMessage": False,
            "silenceWindowHours": 12,
        },
    ],
}


class WhatsAppAutomationRuleService:
    def __init__(
        self,
        db: AsyncSession,
        *,
        clock: Callable[[], datetime] | None = None,
        delivery_gate: Callable[[], bool] | None = None,
    ) -> None:
        self.db = db
        self._clock = clock or (lambda: datetime.now(UTC))
        self._delivery_gate = delivery_gate or external_delivery_allowed

    async def get_settings(self, workspace_id: UUID) -> WhatsAppAutomationSettingsRead:
        workspace = await self.db.get(Workspace, workspace_id)
        if workspace is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Workspace não encontrado."
            )
        settings_data = self._normalize(
            workspace.settings.get("whatsappAutomation") if workspace.settings else None
        )
        return WhatsAppAutomationSettingsRead.model_validate(settings_data)

    async def update_settings(
        self,
        workspace_id: UUID,
        payload: WhatsAppAutomationSettingsUpdate,
    ) -> WhatsAppAutomationSettingsRead:
        workspace = await self.db.get(Workspace, workspace_id)
        if workspace is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Workspace não encontrado."
            )
        normalized = self._normalize(payload.model_dump(mode="json", exclude_none=True))
        await self._validate_workspace_flows(workspace_id, normalized)
        settings = dict(workspace.settings or {})
        settings["whatsappAutomation"] = normalized
        workspace.settings = settings
        await self.db.commit()
        return WhatsAppAutomationSettingsRead.model_validate(settings["whatsappAutomation"])

    async def apply_inbound_rules(
        self,
        *,
        contact: WhatsAppContact,
        inbound_text: str,
    ) -> dict | None:
        workspace = await self.db.get(Workspace, contact.workspace_id)
        if workspace is None:
            return None
        settings = self._normalize(
            workspace.settings.get("whatsappAutomation") if workspace.settings else None
        )
        # Configurações antigas não possuem esta confirmação. Elas ficam
        # fail-closed até o titular revisar e salvar a escolha no onboarding.
        if not settings.get("onboardingCompleted", False) or not settings.get("enabled", False):
            return None
        if contact.queue_status == WaQueueStatus.human:
            # S1: humano na conversa, regra automática nenhuma responde por cima.
            # Auditado no mesmo formato da supressão por janela de silêncio; não
            # consome a janela nem cria dispatch.
            self.db.add(
                KlmAuditEvent(
                    workspace_id=contact.workspace_id,
                    actor_user_id=None,
                    actor_type="system",
                    actor_label="WhatsApp automation",
                    event_type="whatsapp_automation_suppressed_human_active",
                    entity_type="whatsapp_contact",
                    entity_id=contact.id,
                    summary="Resposta automática suprimida: conversa em atendimento humano.",
                    after_data={
                        "assignedUserId": (
                            str(contact.assigned_user_id) if contact.assigned_user_id else None
                        ),
                        "humanControlState": getattr(contact.human_control_state, "value", None),
                    },
                )
            )
            await self.db.commit()
            return None

        now_utc = self._clock().astimezone(UTC)
        now_local = now_utc.astimezone(ZoneInfo(settings["timezone"]))
        first_message = await self._is_first_inbound(contact.id)
        normalized = (inbound_text or "").strip().lower()

        for rule in settings["rules"]:
            if not rule.get("enabled", False):
                continue
            trigger = rule.get("triggerType")
            if trigger == "welcome" and first_message:
                return await self._apply_rule_with_silence(contact, rule, now_utc)
            if trigger == "off_hours" and self._is_off_hours(settings, now_local):
                return await self._apply_rule_with_silence(contact, rule, now_utc)
            keyword = (rule.get("keyword") or "").strip().lower()
            if trigger == "keyword" and keyword and keyword in normalized:
                return await self._apply_rule_with_silence(contact, rule, now_utc)
        return None

    async def _apply_rule_with_silence(
        self,
        contact: WhatsAppContact,
        rule: dict,
        now_utc: datetime,
    ) -> dict:
        silence_hours = int(rule.get("silenceWindowHours") or 0)
        if silence_hours > 0 and rule.get("replyText") and self._delivery_gate():
            suppressed = await self._claim_silence_window(
                contact=contact,
                rule=rule,
                now_utc=now_utc,
                silence_hours=silence_hours,
            )
            if suppressed:
                return suppressed
        outcome = await self._apply_rule(contact, rule)
        outcome["ruleId"] = str(rule.get("id") or "")
        return outcome

    async def _claim_silence_window(
        self,
        *,
        contact: WhatsAppContact,
        rule: dict,
        now_utc: datetime,
        silence_hours: int,
    ) -> dict | None:
        # O lock do contato serializa mensagens concorrentes do mesmo contato
        # no PostgreSQL antes da leitura/atualização da janela.
        await self.db.scalar(
            select(WhatsAppContact).where(WhatsAppContact.id == contact.id).with_for_update()
        )
        rule_id = str(rule.get("id") or "")
        dispatch = await self.db.scalar(
            select(WhatsAppAutomationRuleDispatch)
            .where(
                WhatsAppAutomationRuleDispatch.workspace_id == contact.workspace_id,
                WhatsAppAutomationRuleDispatch.contact_id == contact.id,
                WhatsAppAutomationRuleDispatch.rule_id == rule_id,
            )
            .with_for_update()
        )
        threshold = now_utc - timedelta(hours=silence_hours)
        if dispatch is not None:
            last_dispatched = dispatch.last_dispatched_at
            if last_dispatched.tzinfo is None:
                last_dispatched = last_dispatched.replace(tzinfo=UTC)
            if last_dispatched > threshold:
                audit = KlmAuditEvent(
                    workspace_id=contact.workspace_id,
                    actor_user_id=None,
                    actor_type="system",
                    actor_label="WhatsApp automation",
                    event_type="whatsapp_automation_reply_suppressed",
                    entity_type="whatsapp_contact",
                    entity_id=contact.id,
                    summary="Resposta automática suprimida pela janela de silêncio.",
                    after_data={
                        "ruleId": rule_id,
                        "ruleLabel": rule.get("label"),
                        "silenceWindowHours": silence_hours,
                        "lastDispatchedAt": last_dispatched.isoformat(),
                        "suppressedAt": now_utc.isoformat(),
                    },
                )
                self.db.add(audit)
                await self.db.commit()
                return {
                    "suppressed": True,
                    "suppressionReason": "silence_window",
                    "ruleId": rule_id,
                    "label": rule.get("label"),
                }
            dispatch.last_dispatched_at = now_utc
            dispatch.dispatch_count += 1
        else:
            self.db.add(
                WhatsAppAutomationRuleDispatch(
                    workspace_id=contact.workspace_id,
                    contact_id=contact.id,
                    rule_id=rule_id,
                    last_dispatched_at=now_utc,
                    dispatch_count=1,
                )
            )
        await self.db.flush()
        return None

    async def _apply_rule(self, contact: WhatsAppContact, rule: dict) -> dict:
        flow = None
        flow_template_id = rule.get("flowTemplateId")
        if flow_template_id:
            flow = await self._get_workspace_flow(contact.workspace_id, flow_template_id)
            if flow is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "code": "WA-AUTO-001",
                        "message": "A automação referencia um flow indisponível neste workspace.",
                    },
                )
        matter = None
        target_column = rule.get("targetColumnId")
        if target_column and contact.matter_id:
            matter = await self.db.scalar(
                select(Matter).where(
                    Matter.id == contact.matter_id,
                    Matter.workspace_id == contact.workspace_id,
                    Matter.deleted_at.is_(None),
                )
            )
            if matter is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "code": "WA-AUTO-002",
                        "message": "O card vinculado à conversa não pertence a este workspace.",
                    },
                )
        outcome = {
            "replyText": rule.get("replyText"),
            "label": rule.get("label"),
        }
        target_queue_status = rule.get("targetQueueStatus")
        if target_queue_status in {item.value for item in WaQueueStatus}:
            contact.queue_status = WaQueueStatus(target_queue_status)
            if contact.queue_status == WaQueueStatus.human:
                contact.human_control_state = HumanControlState.copilot
        if rule.get("escalateToHuman"):
            contact.queue_status = WaQueueStatus.human
            contact.human_control_state = HumanControlState.copilot
            outcome["escalateToHuman"] = True
        if flow:
            contact.active_flow_template_id = str(flow.id)
            contact.active_flow_label = flow.name
            outcome["flowTemplateId"] = str(flow.id)
        if target_column and matter:
            matter.column_id = KanbanColumnId(target_column)
            outcome["movedColumnId"] = target_column
        await self.db.commit()
        return outcome

    async def _validate_workspace_flows(
        self,
        workspace_id: UUID,
        settings: dict,
    ) -> None:
        raw_ids = {
            str(rule["flowTemplateId"])
            for rule in settings.get("rules", [])
            if rule.get("flowTemplateId")
        }
        if not raw_ids:
            return
        try:
            flow_ids = {UUID(raw_id) for raw_id in raw_ids}
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "WA-AUTO-001", "message": "Identificador de flow inválido."},
            ) from exc
        result = await self.db.scalars(
            select(Flow.id).where(
                Flow.id.in_(flow_ids),
                Flow.workspace_id == workspace_id,
                Flow.deleted_at.is_(None),
            )
        )
        found = set(result.all())
        if found != flow_ids:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "WA-AUTO-001",
                    "message": "A automação referencia um flow indisponível neste workspace.",
                },
            )

    async def _get_workspace_flow(
        self,
        workspace_id: UUID,
        flow_id: UUID | str,
    ) -> Flow | None:
        try:
            resolved_id = UUID(str(flow_id))
        except ValueError:
            return None
        return await self.db.scalar(
            select(Flow).where(
                Flow.id == resolved_id,
                Flow.workspace_id == workspace_id,
                Flow.deleted_at.is_(None),
            )
        )

    async def _is_first_inbound(self, contact_id: UUID) -> bool:
        from sqlalchemy import func, select

        from app.models.whatsapp import WaMessageDirection, WhatsAppMessage

        result = await self.db.execute(
            select(func.count(WhatsAppMessage.id)).where(
                WhatsAppMessage.contact_id == contact_id,
                WhatsAppMessage.direction == WaMessageDirection.inbound,
            )
        )
        return (result.scalar() or 0) <= 1

    def _is_off_hours(self, settings: dict, now_local: datetime) -> bool:
        start = int(settings.get("businessHoursStart", 8))
        end = int(settings.get("businessHoursEnd", 19))
        if start > end:
            return not (now_local.hour >= start or now_local.hour < end)
        return now_local.hour < start or now_local.hour >= end

    def _normalize(self, raw: dict | None) -> dict:
        source = deepcopy(DEFAULT_AUTOMATION_SETTINGS)
        incoming = deepcopy(raw or {})
        source["enabled"] = bool(incoming.get("enabled", source["enabled"]))
        source["onboardingCompleted"] = bool(
            incoming.get("onboardingCompleted", incoming.get("onboarding_completed", False))
        )
        source["timezone"] = incoming.get("timezone") or source["timezone"]
        source["businessHoursStart"] = int(
            incoming.get(
                "businessHoursStart",
                incoming.get("business_hours_start", source["businessHoursStart"]),
            )
        )
        source["businessHoursEnd"] = int(
            incoming.get(
                "businessHoursEnd", incoming.get("business_hours_end", source["businessHoursEnd"])
            )
        )
        source["rules"] = []
        for item in incoming.get("rules", DEFAULT_AUTOMATION_SETTINGS["rules"]):
            source["rules"].append(
                {
                    "id": item.get("id") or str(uuid4()),
                    "label": item.get("label") or "Regra automática",
                    "enabled": bool(item.get("enabled", False)),
                    "triggerType": item.get("triggerType") or item.get("trigger_type") or "keyword",
                    "keyword": item.get("keyword"),
                    "replyText": item.get("replyText") or item.get("reply_text"),
                    "flowTemplateId": item.get("flowTemplateId") or item.get("flow_template_id"),
                    "targetQueueStatus": item.get("targetQueueStatus")
                    or item.get("target_queue_status"),
                    "targetColumnId": item.get("targetColumnId") or item.get("target_column_id"),
                    "escalateToHuman": bool(
                        item.get("escalateToHuman", item.get("escalate_to_human", False))
                    ),
                    "onlyFirstMessage": bool(
                        item.get("onlyFirstMessage", item.get("only_first_message", False))
                    ),
                    "silenceWindowHours": int(
                        item.get(
                            "silenceWindowHours",
                            item.get(
                                "silence_window_hours",
                                12
                                if (item.get("triggerType") or item.get("trigger_type"))
                                == "off_hours"
                                else 0,
                            ),
                        )
                    ),
                }
            )
        return source
