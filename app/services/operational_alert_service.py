"""
Alertas operacionais do workspace via email e WhatsApp institucional.
"""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.klm_runtime import external_delivery_allowed
from app.core.logging import logger
from app.db.tenant import get_workspace_context
from app.models.klm_operations import FollowUpTask
from app.models.legal_demand import LegalDemand
from app.models.matter import Matter
from app.models.meeting import Meeting, MeetingStatus
from app.models.whatsapp import WhatsAppContact
from app.models.workspace import Workspace
from app.schemas.control_plane import WorkspaceNotificationRecipientRead
from app.services.email_service import send_daily_digest_email, send_operational_alert_email
from app.services.meeting_service import MeetingService
from app.services.platform_whatsapp_alert_service import PlatformWhatsAppAlertService
from app.services.reports_service import ReportsService
from app.services.workspace_policy_service import _normalize_notification_settings_shape
from app.services.workspace_service import WorkspaceService


class OperationalAlertService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.workspace_service = WorkspaceService(db)
        self.meeting_service = MeetingService(db)
        self.whatsapp_service = PlatformWhatsAppAlertService(db)

    async def notify_contract_sent(self, matter_id: UUID) -> None:
        matter = await self._get_matter(matter_id)
        if not matter:
            return
        await self._dispatch_event(
            workspace=matter.workspace,
            event_key="contractSent",
            operation_id=f"contract-sent:{matter.id}",
            recipients=await self._recipients_for_matter(matter),
            title="Contrato enviado para assinatura",
            email_subject=f"Contrato enviado — {matter.client.name}",
            whatsapp_body=(
                f"Meu Executivo: o contrato do lead {matter.client.name} foi enviado para assinatura. "
                f"Demanda: {matter.demand_type}."
            ),
            email_body=(
                f"O contrato de {matter.client.name} acabou de ser enviado para assinatura. "
                f"Acompanhe o status e avance o follow-up com o cliente."
            ),
            meta_lines=[
                f"Cliente: {matter.client.name}",
                f"Demanda: {matter.demand_type}",
                f"Status atual: {matter.micro_status.value}",
            ],
        )

    async def notify_contract_signed(self, matter_id: UUID) -> None:
        matter = await self._get_matter(matter_id)
        if not matter:
            return
        await self._dispatch_event(
            workspace=matter.workspace,
            event_key="contractSigned",
            operation_id=f"contract-signed:{matter.id}",
            recipients=await self._recipients_for_matter(matter),
            title="Contrato assinado",
            email_subject=f"Contrato assinado — {matter.client.name}",
            whatsapp_body=(
                f"Meu Executivo: contrato assinado por {matter.client.name}. "
                f"O caso está pronto para o próximo passo operacional."
            ),
            email_body=(
                f"{matter.client.name} concluiu a assinatura do contrato. "
                f"Revise o card, confirme os próximos passos e siga para a etapa operacional seguinte."
            ),
            meta_lines=[
                f"Cliente: {matter.client.name}",
                f"Demanda: {matter.demand_type}",
                "Evento: assinatura confirmada pelo webhook",
            ],
        )

    async def notify_meeting_scheduled(
        self,
        meeting_id: UUID,
        *,
        commit_whatsapp: bool = True,
    ) -> bool:
        meeting = await self._get_meeting(meeting_id)
        if not meeting:
            return False
        return await self._dispatch_event(
            workspace=meeting.workspace,
            event_key="meetingScheduled",
            operation_id=(
                f"meeting-scheduled:{meeting.id}:{meeting.starts_at.isoformat()}"
            ),
            recipients=await self._recipients_for_meeting(meeting),
            title="Reunião agendada",
            email_subject=f"Reunião agendada — {meeting.title}",
            whatsapp_body=(
                f"Meu Executivo: reunião agendada para {self._format_dt(meeting.starts_at)}. "
                f"Título: {meeting.title}."
            ),
            email_body=(
                f"A reunião '{meeting.title}' foi agendada para {self._format_dt(meeting.starts_at)}. "
                "O time já pode se organizar com o contexto do matter."
            ),
            meta_lines=self._meeting_meta_lines(meeting),
            commit_whatsapp=commit_whatsapp,
        )

    async def notify_human_takeover(self, contact_id: UUID, *, reason: str) -> None:
        contact = await self._get_contact(contact_id)
        if not contact:
            return
        recipients = await self._recipients_for_contact(contact)
        if not recipients:
            return
        lead_name = contact.name or contact.phone
        matter_label = contact.matter.demand_type if contact.matter else "Atendimento via WhatsApp"
        await self._dispatch_event(
            workspace=contact.workspace,
            event_key="meetingReminder",
            operation_id=(
                f"human-takeover:{contact.id}:"
                f"{hashlib.sha256(reason.encode()).hexdigest()[:16]}"
            ),
            recipients=recipients,
            title="Conversa precisa de atendimento humano",
            email_subject=f"Ação humana necessária — {lead_name}",
            whatsapp_body=(
                f"Meu Executivo: a conversa de {lead_name} foi escalada para humano. "
                f"Motivo: {reason}. Confira o chat e assuma o atendimento."
            ),
            email_body=(
                f"A conversa de {lead_name} precisa de intervenção humana. "
                f"Motivo identificado: {reason}. Abra o inbox e siga com o atendimento."
            ),
            meta_lines=[
                f"Lead: {lead_name}",
                f"Contexto: {matter_label}",
                f"Fila atual: {contact.queue_status.value}",
            ],
        )

    async def notify_sla_escalation(
        self,
        legal_demand_id: UUID,
        *,
        level: str,
        layer: str | None,
        due_at: datetime,
    ) -> bool:
        """Escalonamento de SLA em camadas (Onda 4 — Controladoria).
        Ver app/services/legal_sla_service.py::LegalSlaService._escalate."""
        demand = await self._get_legal_demand(legal_demand_id)
        if not demand:
            return False
        recipients = await self._recipients_for_legal_demand(demand)
        due_label = self._format_dt(due_at)
        level_label = {
            "d7": "vence em até 7 dias",
            "d3": "vence em até 3 dias",
            "d1": "vence amanhã",
            "d0": "vence hoje",
            "atrasado": "está ATRASADO",
        }.get(level, level)
        return await self._dispatch_event(
            workspace=demand.workspace,
            event_key="slaEscalation",
            operation_id=(
                f"sla:{demand.id}:{level}:{due_at.isoformat()}"
            ),
            recipients=recipients,
            title="Prazo em escalonamento de SLA",
            email_subject=f"SLA — demanda {level_label}",
            whatsapp_body=(
                f"Meu Executivo: a demanda jurídica {demand.id} {level_label} "
                f"(prazo efetivo {due_label}, camada {layer or 'sem camada'}). Confirme a leitura no painel."
            ),
            email_body=(
                f"A demanda jurídica '{demand.area or demand.nature.value}' {level_label}. "
                f"Prazo efetivo calculado: {due_label} (camada mais restritiva: {layer or 'não identificada'}). "
                "Confirme a leitura no painel de controladoria."
            ),
            meta_lines=[
                f"Demanda: {demand.id}",
                f"Natureza: {demand.nature.value}",
                f"Prazo efetivo: {due_label}",
                f"Camada: {layer or 'não identificada'}",
            ],
        )

    async def send_due_meeting_reminders(self, now: datetime | None = None) -> int:
        now = now or datetime.now(UTC)
        due = await self.meeting_service.list_due_reminders(now)
        sent = 0
        for meeting in due:
            if not await self._meeting_requires_reminder(meeting, now):
                continue
            dispatched = await self._dispatch_event(
                workspace=meeting.workspace,
                event_key="meetingReminder",
                operation_id=(
                    f"meeting-reminder:{meeting.id}:{meeting.starts_at.isoformat()}"
                ),
                recipients=await self._recipients_for_meeting(meeting),
                title="Lembrete de reunião",
                email_subject=f"Lembrete de reunião — {meeting.title}",
                whatsapp_body=(
                    f"Meu Executivo: lembrete da reunião '{meeting.title}' em {self._format_dt(meeting.starts_at)}."
                ),
                email_body=(
                    f"Este é um lembrete da reunião '{meeting.title}', marcada para {self._format_dt(meeting.starts_at)}."
                ),
                meta_lines=self._meeting_meta_lines(meeting),
            )
            if dispatched:
                await self.meeting_service.mark_reminder_sent(meeting, now)
                sent += 1
        await self.db.commit()
        return sent

    async def send_due_follow_up_reminders(self, now: datetime | None = None) -> int:
        """Varre `klm_follow_up_tasks` vencidos e dispara notificação uma vez por ciclo.

        Mesmo padrão de `send_due_meeting_reminders`: `_dispatch_event` já é
        fail-closed (`external_delivery_allowed()`); só marcamos
        `last_notified_at` (via `mark_follow_up_notified`) quando o disparo
        realmente aconteceu, então rodar o tick de novo — ou com o gate
        fechado — nunca duplica nem finge que notificou.
        """
        from app.services.klm_operations_service import KlmOperationsService

        now = now or datetime.now(UTC)
        ops_service = KlmOperationsService(self.db)
        due = await ops_service.list_due_follow_ups_for_notification(now)
        sent = 0
        for follow_up in due:
            workspace, recipients = await self._follow_up_workspace_and_recipients(follow_up)
            if not workspace:
                continue
            due_label = self._format_dt(follow_up.due_at)
            reason = follow_up.reason or "Follow-up programado sem motivo registrado"
            dispatched = await self._dispatch_event(
                workspace=workspace,
                event_key="followUpDue",
                operation_id=(
                    f"follow-up:{follow_up.id}:{follow_up.attempt + 1}:"
                    f"{follow_up.due_at.isoformat()}"
                ),
                recipients=recipients,
                title="Follow-up vencido",
                email_subject="Follow-up vencido — ação necessária",
                whatsapp_body=(
                    f"Meu Executivo: o follow-up marcado para {due_label} venceu. "
                    f"Motivo: {reason}. Retome o contato pelo canal {follow_up.channel.value}."
                ),
                email_body=(
                    f"O follow-up marcado para {due_label} venceu sem conclusão. "
                    f"Motivo registrado: {reason}. Abra a fila e retome o contato pelo canal "
                    f"{follow_up.channel.value}."
                ),
                meta_lines=[
                    f"Canal: {follow_up.channel.value}",
                    f"Vencimento: {due_label}",
                    f"Tentativa: {follow_up.attempt + 1}/{follow_up.max_attempts}",
                ],
            )
            if dispatched:
                await ops_service.mark_follow_up_notified(follow_up, notified_at=now)
                sent += 1
        await self.db.commit()
        return sent

    async def _follow_up_workspace_and_recipients(
        self, follow_up: FollowUpTask
    ) -> tuple[Workspace | None, list[WorkspaceNotificationRecipientRead]]:
        if follow_up.matter_id:
            matter = await self._get_matter(follow_up.matter_id)
            if matter:
                return matter.workspace, await self._recipients_for_matter(matter)
        workspace = await self.workspace_service.get_workspace(follow_up.workspace_id)
        if not workspace:
            return None, []
        return workspace, await self.workspace_service._resolve_notification_recipients(workspace)

    async def send_daily_digests(
        self,
        now: datetime | None = None,
    ) -> int:
        workspace_id = get_workspace_context(self.db)
        if workspace_id is None:
            raise RuntimeError(
                "send_daily_digests exige contexto de workspace; fan-out pertence ao worker"
            )
        sent = await self.send_daily_digest_for_workspace(
            UUID(str(workspace_id)),
            now=now,
        )
        return int(sent)

    async def send_daily_digest_for_workspace(
        self,
        workspace_id: UUID,
        *,
        now: datetime | None = None,
    ) -> bool:
        now = now or datetime.now(UTC)
        workspace = await self.db.get(Workspace, workspace_id)
        if not workspace:
            return False
        notifications = _normalize_notification_settings_shape((workspace.settings or {}).get("notifications", {}))
        rule = self._rule_for_event(notifications, "dailyDigest")
        if not rule.get("enabled", True):
            return False

        recipients = await self.workspace_service._resolve_notification_recipients(workspace)
        email_targets = [item.email for item in recipients if item.email]
        phone_targets = [item.phone for item in recipients if item.phone]
        email_enabled = notifications.get("emailEnabled", True) and rule.get("emailEnabled", True)
        whatsapp_enabled = notifications.get("whatsappEnabled", True) and rule.get("whatsappEnabled", True)
        if not email_enabled and not whatsapp_enabled:
            return False
        if not email_targets and not phone_targets:
            return False

        deduped = self._dedupe_recipients(recipients)
        email_targets = [item.email for item in deduped if item.email]
        phone_targets = [item.phone for item in deduped if item.phone]
        if not email_targets:
            email_targets = []

        reports = await ReportsService(self.db).get_summary(workspace.id)
        highlights = [item.title for item in reports.insights[:3]]
        metrics = [
            (metric.label, f"{metric.value:g}{metric.unit or ''}")
            for metric in reports.kpis[:6]
        ]
        agenda = await self._agenda_lines_for_day(workspace.id, now)
        risks = [item.summary for item in reports.insights[:3]]
        pending_lines = self._pending_lines_from_reports(reports)
        whatsapp_body = self._build_daily_digest_whatsapp_body(
            workspace_name=workspace.name,
            digest_date=now.astimezone().strftime("%d/%m/%Y"),
            pending_lines=pending_lines,
            agenda=agenda,
            highlights=highlights,
        )

        sent_any = False
        if email_enabled:
            for email in sorted(set(email_targets)):
                success = await send_daily_digest_email(
                    to_email=email,
                    workspace_name=workspace.name,
                    digest_date=now.astimezone().strftime("%d/%m/%Y"),
                    highlights=highlights,
                    metrics=metrics,
                    agenda=agenda,
                    risks=risks,
                )
                sent_any = sent_any or success
        if whatsapp_enabled:
            for phone in sorted(set(phone_targets)):
                recipient_ref = hashlib.sha256(phone.encode()).hexdigest()[:16]
                success = await self.whatsapp_service.send_text(
                    phone,
                    whatsapp_body,
                    idempotency_key=(
                        f"daily-digest:{workspace.id}:{now.date().isoformat()}:"
                        f"{recipient_ref}"
                    ),
                    source_id=f"daily-digest:{workspace.id}:{now.date().isoformat()}",
                )
                sent_any = sent_any or success
        return sent_any

    async def _dispatch_event(
        self,
        *,
        workspace: Workspace,
        event_key: str,
        operation_id: str,
        recipients: list[WorkspaceNotificationRecipientRead],
        title: str,
        email_subject: str,
        whatsapp_body: str,
        email_body: str,
        meta_lines: list[str],
        commit_whatsapp: bool = True,
    ) -> bool:
        if not external_delivery_allowed():
            logger.info(
                "operational_alert.blocked_by_klm_gate",
                workspace_id=str(workspace.id),
                event_key=event_key,
            )
            return False
        notifications = _normalize_notification_settings_shape((workspace.settings or {}).get("notifications", {}))
        rule = self._rule_for_event(notifications, event_key)
        if not rule.get("enabled", True):
            return False

        sent_any = False
        deduped = self._dedupe_recipients(recipients)

        if notifications.get("emailEnabled", True) and rule.get("emailEnabled", True):
            for recipient in deduped:
                if not recipient.email:
                    continue
                success = await send_operational_alert_email(
                    to_email=recipient.email,
                    subject=email_subject,
                    workspace_name=workspace.name,
                    body=email_body,
                    cta_label="Abrir workspace",
                    cta_url=f"{settings.FRONTEND_URL}/app",
                    meta_lines=meta_lines,
                )
                sent_any = sent_any or success

        if notifications.get("whatsappEnabled", True) and rule.get("whatsappEnabled", True):
            for recipient in deduped:
                if not recipient.phone:
                    continue
                delivery_digest = hashlib.sha256(
                    f"{workspace.id}:{operation_id}:{recipient.phone}".encode()
                ).hexdigest()
                delivery_key = f"operational-alert:{delivery_digest}"
                delivery_source = f"{workspace.id}:{operation_id}"
                if commit_whatsapp:
                    success = await self.whatsapp_service.send_text(
                        recipient.phone,
                        whatsapp_body,
                        idempotency_key=delivery_key,
                        source_id=delivery_source[:120],
                    )
                else:
                    success = await self.whatsapp_service.send_text(
                        recipient.phone,
                        whatsapp_body,
                        idempotency_key=delivery_key,
                        source_id=delivery_source[:120],
                        commit=False,
                    )
                sent_any = sent_any or success

        logger.info(
            "operational_alert.dispatched",
            workspace_id=str(workspace.id),
            event_key=event_key,
            recipients=len(deduped),
            sent_any=sent_any,
        )
        return sent_any

    async def _get_matter(self, matter_id: UUID) -> Matter | None:
        result = await self.db.execute(
            select(Matter)
            .options(
                selectinload(Matter.workspace),
                selectinload(Matter.client),
                selectinload(Matter.responsible_human),
            )
            .where(Matter.id == matter_id, Matter.deleted_at.is_(None))
        )
        return result.scalar_one_or_none()

    async def _get_legal_demand(self, legal_demand_id: UUID) -> LegalDemand | None:
        result = await self.db.execute(
            select(LegalDemand)
            .options(
                selectinload(LegalDemand.workspace),
                selectinload(LegalDemand.responsible_user),
            )
            .where(LegalDemand.id == legal_demand_id)
        )
        return result.scalar_one_or_none()

    async def _recipients_for_legal_demand(
        self, demand: LegalDemand
    ) -> list[WorkspaceNotificationRecipientRead]:
        recipients = await self.workspace_service._resolve_notification_recipients(demand.workspace)
        if demand.responsible_user:
            recipients.append(
                WorkspaceNotificationRecipientRead(
                    id=f"legal-demand:{demand.responsible_user.id}",
                    name=demand.responsible_user.name,
                    email=demand.responsible_user.email,
                    phone=demand.responsible_user.phone,
                    role="responsible_human",
                    user_id=demand.responsible_user.id,
                    locked=False,
                )
            )
        return recipients

    async def _get_contact(self, contact_id: UUID) -> WhatsAppContact | None:
        result = await self.db.execute(
            select(WhatsAppContact)
            .options(
                selectinload(WhatsAppContact.workspace),
                selectinload(WhatsAppContact.assigned_user),
                selectinload(WhatsAppContact.matter).selectinload(Matter.client),
                selectinload(WhatsAppContact.matter).selectinload(Matter.responsible_human),
            )
            .where(WhatsAppContact.id == contact_id)
        )
        return result.scalar_one_or_none()

    async def _get_meeting(self, meeting_id: UUID) -> Meeting | None:
        result = await self.db.execute(
            select(Meeting)
            .options(
                selectinload(Meeting.workspace),
                selectinload(Meeting.matter).selectinload(Matter.client),
                selectinload(Meeting.responsible_user),
            )
            .where(Meeting.id == meeting_id)
        )
        return result.scalar_one_or_none()

    async def _recipients_for_matter(self, matter: Matter) -> list[WorkspaceNotificationRecipientRead]:
        recipients = await self.workspace_service._resolve_notification_recipients(matter.workspace)
        if matter.responsible_human:
            recipients.append(
                WorkspaceNotificationRecipientRead(
                    id=f"matter:{matter.responsible_human.id}",
                    name=matter.responsible_human.name,
                    email=matter.responsible_human.email,
                    phone=matter.responsible_human.phone,
                    role="responsible_human",
                    user_id=matter.responsible_human.id,
                    locked=False,
                )
            )
        return recipients

    async def _recipients_for_meeting(self, meeting: Meeting) -> list[WorkspaceNotificationRecipientRead]:
        recipients = await self.workspace_service._resolve_notification_recipients(meeting.workspace)
        if meeting.responsible_user:
            recipients.append(
                WorkspaceNotificationRecipientRead(
                    id=f"meeting:{meeting.responsible_user.id}",
                    name=meeting.responsible_user.name,
                    email=meeting.responsible_user.email,
                    phone=meeting.responsible_user.phone,
                    role="responsible_human",
                    user_id=meeting.responsible_user.id,
                    locked=False,
                )
            )
        for item in meeting.recipient_overrides or []:
            recipients.append(
                WorkspaceNotificationRecipientRead(
                    id=str(item.get("id") or f"meeting-override:{meeting.id}:{item.get('email') or item.get('phone') or item.get('name')}"),
                    name=item.get("name") or "Contato da reunião",
                    email=item.get("email"),
                    phone=item.get("phone"),
                    role=item.get("role") or "custom",
                    user_id=item.get("user_id"),
                    locked=False,
                )
            )
        return recipients

    async def _recipients_for_contact(self, contact: WhatsAppContact) -> list[WorkspaceNotificationRecipientRead]:
        recipients = await self.workspace_service._resolve_notification_recipients(contact.workspace)
        if contact.assigned_user:
            recipients.append(
                WorkspaceNotificationRecipientRead(
                    id=f"contact:{contact.assigned_user.id}",
                    name=contact.assigned_user.name,
                    email=contact.assigned_user.email,
                    phone=contact.assigned_user.phone,
                    role="responsible_human",
                    user_id=contact.assigned_user.id,
                    locked=False,
                )
            )
        elif contact.matter and contact.matter.responsible_human:
            recipients.append(
                WorkspaceNotificationRecipientRead(
                    id=f"matter:{contact.matter.responsible_human.id}",
                    name=contact.matter.responsible_human.name,
                    email=contact.matter.responsible_human.email,
                    phone=contact.matter.responsible_human.phone,
                    role="responsible_human",
                    user_id=contact.matter.responsible_human.id,
                    locked=False,
                )
            )
        return recipients

    async def _meeting_requires_reminder(self, meeting: Meeting, now: datetime) -> bool:
        notifications = _normalize_notification_settings_shape((meeting.workspace.settings or {}).get("notifications", {}))
        rule = self._rule_for_event(notifications, "meetingReminder")
        if not rule.get("enabled", True):
            return False
        reminder_minutes = int(rule.get("reminderMinutesBefore") or 120)
        reminder_at = meeting.starts_at - timedelta(minutes=reminder_minutes)
        if meeting.reminder_sent_at is not None:
            return False
        return reminder_at <= now <= meeting.starts_at

    async def _agenda_lines_for_day(self, workspace_id: UUID, now: datetime) -> list[str]:
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_of_day = start_of_day + timedelta(days=1)
        result = await self.db.execute(
            select(Meeting)
            .where(
                Meeting.workspace_id == workspace_id,
                Meeting.status == MeetingStatus.scheduled,
                Meeting.starts_at >= start_of_day,
                Meeting.starts_at < end_of_day,
            )
            .order_by(Meeting.starts_at.asc())
        )
        meetings = result.scalars().all()
        return [f"{self._format_dt(item.starts_at)} — {item.title}" for item in meetings]

    def _rule_for_event(self, notifications: dict, event_key: str) -> dict:
        for item in notifications.get("rules", []):
            if item.get("key") == event_key:
                return item
        return {"key": event_key, "enabled": True, "emailEnabled": True, "whatsappEnabled": True}

    def _dedupe_recipients(
        self,
        recipients: list[WorkspaceNotificationRecipientRead],
    ) -> list[WorkspaceNotificationRecipientRead]:
        unique: dict[str, WorkspaceNotificationRecipientRead] = {}
        for item in recipients:
            key = f"{item.email or ''}|{item.phone or ''}|{item.role}"
            unique[key] = item
        return list(unique.values())

    def _meeting_meta_lines(self, meeting: Meeting) -> list[str]:
        rows = [f"Início: {self._format_dt(meeting.starts_at)}"]
        if meeting.ends_at:
            rows.append(f"Fim: {self._format_dt(meeting.ends_at)}")
        if meeting.matter and meeting.matter.client:
            rows.append(f"Lead: {meeting.matter.client.name}")
        if meeting.location:
            rows.append(f"Local: {meeting.location}")
        if meeting.meeting_url:
            rows.append(f"Link: {meeting.meeting_url}")
        return rows

    def _pending_lines_from_reports(self, reports) -> list[str]:
        metric_map = {metric.id: metric for metric in reports.kpis}
        lines: list[str] = []
        if metric_map.get("open_tasks"):
            lines.append(f"{int(metric_map['open_tasks'].value)} pendências abertas")
        if metric_map.get("pending_approvals"):
            lines.append(f"{int(metric_map['pending_approvals'].value)} aprovações pendentes")
        if metric_map.get("blocked_matters"):
            lines.append(f"{int(metric_map['blocked_matters'].value)} leads bloqueados")
        return [line for line in lines if not line.startswith("0 ")]

    def _build_daily_digest_whatsapp_body(
        self,
        *,
        workspace_name: str,
        digest_date: str,
        pending_lines: list[str],
        agenda: list[str],
        highlights: list[str],
    ) -> str:
        parts = [f"Bom dia. Resumo da manhã do workspace {workspace_name} ({digest_date})."]
        parts.append(
            "Pendências principais: "
            + ("; ".join(pending_lines[:3]) if pending_lines else "sem pendências críticas registradas")
            + "."
        )
        parts.append(
            "Reuniões de hoje: "
            + (" | ".join(agenda[:3]) if agenda else "nenhuma reunião agendada")
            + "."
        )
        if highlights:
            parts.append("Insights: " + " | ".join(highlights[:2]) + ".")
        parts.append(f"Acesse {settings.FRONTEND_URL}/app para acompanhar.")
        return " ".join(parts)

    @staticmethod
    def _format_dt(value: datetime) -> str:
        return value.astimezone().strftime("%d/%m/%Y às %H:%M")
