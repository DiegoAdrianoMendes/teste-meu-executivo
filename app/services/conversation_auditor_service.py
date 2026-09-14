"""Independent, deterministic auditor for canonical omnichannel conversations."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Iterable
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.klm_runtime import external_delivery_allowed
from app.db.post_commit import queue_realtime_event
from app.models.klm_operations import (
    MessageAuthorType,
    MessageDeliveryStatus,
    MessageDirection,
    OmnichannelChannel,
    OmnichannelMessage,
    OmnichannelThread,
    ThreadStatus,
)
from app.models.omnichannel_v6 import (
    OmnichannelAuditFinding,
    OmnichannelChannelConnection,
    OmnichannelEndpoint,
    OmnichannelMessageDeliveryEvent,
    OmnichannelScheduledMessage,
    OmnichannelThreadBinding,
)
from app.schemas.klm_operations import OmnichannelMessageCreate
from app.schemas.omnichannel_v6 import (
    OmnichannelAuditDashboardRead,
    OmnichannelAuditFindingAction,
    ScheduledMessageCreate,
)
from app.services.klm_operations_service import KlmOperationsService
from app.services.omnichannel_v6_service import OmnichannelV6Service

ACTIVE_FINDING_STATES = ("open", "retry_scheduled", "waiting_human")
AUDITED_FINDING_TYPES = (
    "unanswered_lead",
    "delivery_failed",
    "delivery_stalled",
    "projection_mismatch",
    "duplicate_outbound",
    "potential_internal_leak",
)
ACTIVE_THREAD_STATUSES = (
    ThreadStatus.open,
    ThreadStatus.waiting_documents,
    ThreadStatus.waiting_human,
    ThreadStatus.human_active,
)
MAX_MESSAGES_PER_SCAN = 5_000
DEFAULT_UNANSWERED_AFTER = timedelta(minutes=5)
DEFAULT_QUEUED_STALE_AFTER = timedelta(minutes=10)
_LEAK_MARKERS = (
    "system prompt",
    "prompt do sistema",
    "nota interna",
    "internal note",
    "contexto rag",
    "rag context",
    "chain of thought",
)


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime | None) -> datetime:
    if value is None:
        return _now()
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _percent(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 100.0
    return round(max(0.0, min(100.0, numerator * 100 / denominator)), 1)


def _normalized_body(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().casefold())


@dataclass(frozen=True, slots=True)
class AuditFindingSpec:
    thread_id: UUID
    matter_id: UUID | None
    message_id: UUID | None
    finding_type: str
    severity: str
    title: str
    summary: str
    dedupe_key: str
    evidence: dict
    remediation: dict
    preferred_state: str = "open"


class ConversationAuditVersionConflictError(ValueError):
    def __init__(self, current_version: int):
        super().__init__("O apontamento mudou desde a última leitura.")
        self.current_version = current_version


class ConversationAuditorService:
    """Audits delivery truth and response continuity without model calls."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.operations = KlmOperationsService(db)
        self.omnichannel = OmnichannelV6Service(db)

    async def scan_workspace(
        self,
        workspace_id: UUID,
        *,
        thread_id: UUID | None = None,
        limit: int = 250,
        now: datetime | None = None,
    ) -> OmnichannelAuditDashboardRead:
        scanned_at = _aware(now)
        limit = max(1, min(limit, 500))
        thread_filters = [
            OmnichannelThread.workspace_id == workspace_id,
            OmnichannelThread.status.in_(ACTIVE_THREAD_STATUSES),
        ]
        if thread_id is not None:
            thread_filters.append(OmnichannelThread.id == thread_id)
        threads = list(
            (
                await self.db.scalars(
                    select(OmnichannelThread)
                    .where(*thread_filters)
                    .order_by(
                        OmnichannelThread.last_message_at.desc().nullslast(),
                        OmnichannelThread.updated_at.desc(),
                    )
                    .limit(limit)
                )
            ).all()
        )
        if thread_id is not None and not threads:
            exists = await self.db.scalar(
                select(OmnichannelThread.id).where(
                    OmnichannelThread.workspace_id == workspace_id,
                    OmnichannelThread.id == thread_id,
                )
            )
            if exists is None:
                raise LookupError("Conversa não encontrada no workspace.")

        thread_ids = [thread.id for thread in threads]
        messages: list[OmnichannelMessage] = []
        if thread_ids:
            message_limit = min(MAX_MESSAGES_PER_SCAN, max(100, len(thread_ids) * 30))
            messages = list(
                (
                    await self.db.scalars(
                        select(OmnichannelMessage)
                        .where(
                            OmnichannelMessage.workspace_id == workspace_id,
                            OmnichannelMessage.thread_id.in_(thread_ids),
                        )
                        .order_by(OmnichannelMessage.occurred_at.desc())
                        .limit(message_limit)
                    )
                ).all()
            )
        messages_by_thread: dict[UUID, list[OmnichannelMessage]] = {
            current_id: [] for current_id in thread_ids
        }
        for message in messages:
            messages_by_thread.setdefault(message.thread_id, []).append(message)

        outbound_ids = [
            message.id
            for message in messages
            if message.direction == MessageDirection.outbound
        ]
        receipts_by_message: dict[UUID, list[OmnichannelMessageDeliveryEvent]] = {}
        if outbound_ids:
            receipts = list(
                (
                    await self.db.scalars(
                        select(OmnichannelMessageDeliveryEvent)
                        .where(
                            OmnichannelMessageDeliveryEvent.workspace_id == workspace_id,
                            OmnichannelMessageDeliveryEvent.message_id.in_(outbound_ids),
                        )
                        .order_by(OmnichannelMessageDeliveryEvent.received_at.desc())
                        .limit(MAX_MESSAGES_PER_SCAN)
                    )
                ).all()
            )
            for receipt in receipts:
                receipts_by_message.setdefault(receipt.message_id, []).append(receipt)

        existing_by_key: dict[str, OmnichannelAuditFinding] = {}
        if thread_ids:
            existing = list(
                (
                    await self.db.scalars(
                        select(OmnichannelAuditFinding).where(
                            OmnichannelAuditFinding.workspace_id == workspace_id,
                            OmnichannelAuditFinding.thread_id.in_(thread_ids),
                        )
                    )
                ).all()
            )
            existing_by_key = {finding.dedupe_key: finding for finding in existing}
            await self._reconcile_retries(workspace_id, existing, scanned_at)

        specs: list[AuditFindingSpec] = []
        for thread in threads:
            specs.extend(
                self._inspect_thread(
                    thread,
                    messages_by_thread.get(thread.id, []),
                    receipts_by_message,
                    scanned_at,
                )
            )

        detected_keys = {spec.dedupe_key for spec in specs}
        changed_findings: list[OmnichannelAuditFinding] = []
        for spec in specs:
            finding, should_note, changed = await self._upsert_finding(
                workspace_id,
                spec,
                existing_by_key.get(spec.dedupe_key),
                scanned_at,
            )
            existing_by_key[spec.dedupe_key] = finding
            if changed:
                changed_findings.append(finding)
            if should_note:
                await self._ensure_internal_note(workspace_id, finding)
            if bool(finding.remediation.get("automaticRetryEligible")):
                retry_changed = await self._attempt_retry(
                    workspace_id,
                    finding,
                    actor_user_id=None,
                    automatic=True,
                )
                if retry_changed and finding not in changed_findings:
                    changed_findings.append(finding)

        for finding in existing_by_key.values():
            if (
                finding.finding_type in AUDITED_FINDING_TYPES
                and finding.dedupe_key not in detected_keys
                and finding.state in ACTIVE_FINDING_STATES
                and finding.retry_schedule_id is None
            ):
                finding.state = "resolved"
                finding.resolved_at = scanned_at
                finding.version += 1
                finding.remediation = {
                    **dict(finding.remediation or {}),
                    "resolution": "A condição deixou de ser detectada na varredura canônica.",
                    "resolvedBy": "auditor",
                }
                changed_findings.append(finding)

        if changed_findings:
            await self.db.flush()
            affected_threads = sorted({str(item.thread_id) for item in changed_findings})
            queue_realtime_event(
                self.db,
                workspace_id=workspace_id,
                event_type="OMNICHANNEL_AUDIT_UPDATED",
                data={
                    "thread_ids": affected_threads,
                    "changed": len(changed_findings),
                    "inspected": len(threads),
                },
            )
        return await self.dashboard(
            workspace_id,
            thread_id=thread_id,
            inspected_threads=len(threads),
        )

    def _inspect_thread(
        self,
        thread: OmnichannelThread,
        messages: list[OmnichannelMessage],
        receipts_by_message: dict[UUID, list[OmnichannelMessageDeliveryEvent]],
        now: datetime,
    ) -> list[AuditFindingSpec]:
        specs: list[AuditFindingSpec] = []
        ordered = sorted(messages, key=lambda item: _aware(item.occurred_at))
        client_messages = [
            item
            for item in ordered
            if item.direction == MessageDirection.inbound
            and item.author_type == MessageAuthorType.client
        ]
        public_outbound = [
            item for item in ordered if item.direction == MessageDirection.outbound
        ]
        latest_inbound = client_messages[-1] if client_messages else None
        latest_outbound = public_outbound[-1] if public_outbound else None
        if latest_inbound and (
            latest_outbound is None
            or _aware(latest_outbound.occurred_at) < _aware(latest_inbound.occurred_at)
        ):
            due_at = _aware(thread.first_response_due_at) if thread.first_response_due_at else (
                _aware(latest_inbound.occurred_at) + DEFAULT_UNANSWERED_AFTER
            )
            if now >= due_at:
                age_minutes = max(
                    0,
                    int((now - _aware(latest_inbound.occurred_at)).total_seconds() // 60),
                )
                specs.append(
                    AuditFindingSpec(
                        thread_id=thread.id,
                        matter_id=thread.matter_id,
                        message_id=latest_inbound.id,
                        finding_type="unanswered_lead",
                        severity="critical" if age_minutes >= 30 else "warning",
                        title="Lead aguardando resposta",
                        summary=(
                            f"A última mensagem do lead está sem resposta há {age_minutes} min."
                        ),
                        dedupe_key=f"unanswered:{thread.id}:{latest_inbound.id}",
                        evidence={
                            "messageId": str(latest_inbound.id),
                            "occurredAt": _aware(latest_inbound.occurred_at).isoformat(),
                            "dueAt": due_at.isoformat(),
                            "ageMinutes": age_minutes,
                            "channel": latest_inbound.channel.value,
                        },
                        remediation={
                            "action": "respond",
                            "automaticRetryEligible": False,
                            "requiresHumanReview": True,
                        },
                    )
                )

        previous_public: OmnichannelMessage | None = None
        for message in public_outbound:
            if message.delivery_status == MessageDeliveryStatus.local_draft:
                previous_public = message
                continue
            receipts = receipts_by_message.get(message.id, [])
            external_ids = {
                value
                for value in (
                    message.external_message_id,
                    *(receipt.external_message_id for receipt in receipts),
                )
                if value
            }
            metadata = message.message_metadata if isinstance(message.message_metadata, dict) else {}
            uncertain = bool(
                metadata.get("deliveryUncertain")
                or metadata.get("delivery_uncertain")
                or any(
                    receipt.ignored_reason == "delivery_uncertain"
                    or str(receipt.provider_status).lower() == "delivery_uncertain"
                    for receipt in receipts
                )
            )
            definitive_failure = message.delivery_status == MessageDeliveryStatus.failed or any(
                receipt.applied and receipt.projected_status_after == MessageDeliveryStatus.failed.value
                for receipt in receipts
            )
            if definitive_failure:
                safe_retry = not uncertain and not external_ids
                specs.append(
                    AuditFindingSpec(
                        thread_id=thread.id,
                        matter_id=thread.matter_id,
                        message_id=message.id,
                        finding_type="delivery_failed",
                        severity="critical",
                        title="Falha confirmada de entrega",
                        summary="O canal registrou falha definitiva para uma mensagem de saída.",
                        dedupe_key=f"delivery-failed:{message.id}",
                        evidence={
                            "messageId": str(message.id),
                            "deliveryStatus": message.delivery_status.value,
                            "receiptCount": len(receipts),
                            "hasExternalMessageId": bool(external_ids),
                            "outcomeUncertain": uncertain,
                            "channel": message.channel.value,
                        },
                        remediation={
                            "action": "retry_delivery" if safe_retry else "review_delivery",
                            "automaticRetryEligible": safe_retry,
                            "requiresHumanReview": not safe_retry,
                            "reason": (
                                "resultado inequívoco e sem identificador externo"
                                if safe_retry
                                else "há evidência externa ou resultado incerto; reenvio automático bloqueado"
                            ),
                        },
                        preferred_state="open" if safe_retry else "waiting_human",
                    )
                )
            elif (
                message.delivery_status == MessageDeliveryStatus.queued
                and now - _aware(message.occurred_at) >= DEFAULT_QUEUED_STALE_AFTER
                and not external_ids
            ):
                specs.append(
                    AuditFindingSpec(
                        thread_id=thread.id,
                        matter_id=thread.matter_id,
                        message_id=message.id,
                        finding_type="delivery_stalled",
                        severity="critical",
                        title="Envio parado na fila",
                        summary="A mensagem permaneceu na fila além do limite operacional.",
                        dedupe_key=f"delivery-stalled:{message.id}",
                        evidence={
                            "messageId": str(message.id),
                            "queuedAt": _aware(message.occurred_at).isoformat(),
                            "channel": message.channel.value,
                            "outcomeUncertain": uncertain,
                        },
                        remediation={
                            "action": "retry_delivery" if not uncertain else "review_delivery",
                            "automaticRetryEligible": not uncertain,
                            "requiresHumanReview": uncertain,
                        },
                        preferred_state="open" if not uncertain else "waiting_human",
                    )
                )
            elif (
                message.delivery_status
                in {
                    MessageDeliveryStatus.sent,
                    MessageDeliveryStatus.delivered,
                    MessageDeliveryStatus.read,
                }
                and not external_ids
            ):
                specs.append(
                    AuditFindingSpec(
                        thread_id=thread.id,
                        matter_id=thread.matter_id,
                        message_id=message.id,
                        finding_type="projection_mismatch",
                        severity="critical",
                        title="Status sem comprovante externo",
                        summary=(
                            "A interface indica envio, mas não existe identificador externo canônico."
                        ),
                        dedupe_key=f"projection-mismatch:{message.id}",
                        evidence={
                            "messageId": str(message.id),
                            "deliveryStatus": message.delivery_status.value,
                            "receiptCount": len(receipts),
                            "channel": message.channel.value,
                        },
                        remediation={
                            "action": "reconcile_provider",
                            "automaticRetryEligible": False,
                            "requiresHumanReview": True,
                        },
                        preferred_state="waiting_human",
                    )
                )

            if previous_public:
                same_body = _normalized_body(previous_public.body) == _normalized_body(message.body)
                close_together = (
                    _aware(message.occurred_at) - _aware(previous_public.occurred_at)
                    <= timedelta(minutes=2)
                )
                inbound_between = any(
                    _aware(previous_public.occurred_at)
                    < _aware(inbound.occurred_at)
                    < _aware(message.occurred_at)
                    for inbound in client_messages
                )
                if same_body and close_together and not inbound_between:
                    specs.append(
                        AuditFindingSpec(
                            thread_id=thread.id,
                            matter_id=thread.matter_id,
                            message_id=message.id,
                            finding_type="duplicate_outbound",
                            severity="warning",
                            title="Possível resposta duplicada",
                            summary="Duas mensagens iguais foram registradas sem resposta do lead entre elas.",
                            dedupe_key=f"duplicate-outbound:{previous_public.id}:{message.id}",
                            evidence={
                                "firstMessageId": str(previous_public.id),
                                "secondMessageId": str(message.id),
                                "intervalSeconds": int(
                                    (
                                        _aware(message.occurred_at)
                                        - _aware(previous_public.occurred_at)
                                    ).total_seconds()
                                ),
                            },
                            remediation={
                                "action": "review_duplicate",
                                "automaticRetryEligible": False,
                                "requiresHumanReview": True,
                            },
                        )
                    )
            previous_public = message

            lowered = message.body.casefold()
            marker = next((item for item in _LEAK_MARKERS if item in lowered), None)
            if marker:
                specs.append(
                    AuditFindingSpec(
                        thread_id=thread.id,
                        matter_id=thread.matter_id,
                        message_id=message.id,
                        finding_type="potential_internal_leak",
                        severity="critical",
                        title="Possível conteúdo interno exposto",
                        summary="A mensagem ao cliente contém expressão reservada para revisão interna.",
                        dedupe_key=f"potential-internal-leak:{message.id}:{marker}",
                        evidence={
                            "messageId": str(message.id),
                            "matchedMarker": marker,
                            "channel": message.channel.value,
                        },
                        remediation={
                            "action": "review_content",
                            "automaticRetryEligible": False,
                            "requiresHumanReview": True,
                        },
                        preferred_state="waiting_human",
                    )
                )
        return specs

    async def _upsert_finding(
        self,
        workspace_id: UUID,
        spec: AuditFindingSpec,
        existing: OmnichannelAuditFinding | None,
        seen_at: datetime,
    ) -> tuple[OmnichannelAuditFinding, bool, bool]:
        if existing is None:
            finding = OmnichannelAuditFinding(
                workspace_id=workspace_id,
                thread_id=spec.thread_id,
                matter_id=spec.matter_id,
                message_id=spec.message_id,
                finding_type=spec.finding_type,
                severity=spec.severity,
                state=spec.preferred_state,
                title=spec.title,
                summary=spec.summary,
                dedupe_key=spec.dedupe_key,
                evidence=spec.evidence,
                remediation=spec.remediation,
                detected_at=seen_at,
                last_seen_at=seen_at,
            )
            self.db.add(finding)
            await self.db.flush()
            await self.operations.record_audit(
                workspace_id=workspace_id,
                actor_user_id=None,
                actor_type="agent",
                actor_label="Auditor KLM",
                event_type="omnichannel_audit_finding_opened",
                entity_type="omnichannel_audit_finding",
                entity_id=finding.id,
                summary=spec.title,
                correlation_id=spec.dedupe_key,
                after_data={
                    "findingType": spec.finding_type,
                    "severity": spec.severity,
                    "state": spec.preferred_state,
                    "threadId": str(spec.thread_id),
                },
            )
            return finding, True, True

        reopened = existing.state == "resolved"
        if existing.state != "dismissed":
            if reopened or (
                existing.state in {"open", "waiting_human"}
                and existing.retry_schedule_id is None
            ):
                existing.state = spec.preferred_state
            existing.severity = spec.severity
            existing.title = spec.title
            existing.summary = spec.summary
            existing.evidence = spec.evidence
            existing.remediation = {
                **dict(existing.remediation or {}),
                **spec.remediation,
            }
            existing.message_id = spec.message_id
            existing.matter_id = spec.matter_id
            existing.last_seen_at = seen_at
            if reopened:
                existing.resolved_at = None
                existing.version += 1
        return existing, reopened, reopened

    async def _ensure_internal_note(
        self,
        workspace_id: UUID,
        finding: OmnichannelAuditFinding,
    ) -> None:
        if finding.internal_note_message_id is not None:
            return
        thread = await self.db.scalar(
            select(OmnichannelThread).where(
                OmnichannelThread.workspace_id == workspace_id,
                OmnichannelThread.id == finding.thread_id,
            )
        )
        if thread is None:
            return
        preview_before = thread.last_message_preview
        moment_before = thread.last_message_at
        message, _ = await self.operations.create_message(
            workspace_id,
            finding.thread_id,
            None,
            OmnichannelMessageCreate(
                channel=OmnichannelChannel.internal,
                direction=MessageDirection.internal,
                author_type=MessageAuthorType.agent,
                author_label="Auditor KLM",
                body=f"[Auditoria interna] {finding.title}. {finding.summary}",
                audience="internal_only",
                transport="local",
                idempotency_key=f"auditor-note:{finding.id}",
                message_metadata={
                    "source": "klm_conversation_auditor",
                    "findingId": str(finding.id),
                    "findingType": finding.finding_type,
                    "severity": finding.severity,
                    "visibility": "internal_only",
                    "customerVisible": False,
                    "externalActionsExecuted": 0,
                },
            ),
        )
        thread.last_message_preview = preview_before
        thread.last_message_at = moment_before
        finding.internal_note_message_id = message.id
        finding.version += 1

    async def _attempt_retry(
        self,
        workspace_id: UUID,
        finding: OmnichannelAuditFinding,
        *,
        actor_user_id: UUID | None,
        automatic: bool,
    ) -> bool:
        if finding.retry_schedule_id is not None or finding.retry_count >= finding.max_retries:
            return False
        remediation = dict(finding.remediation or {})
        if not bool(remediation.get("automaticRetryEligible")):
            finding.state = "waiting_human"
            remediation["retryBlockedReason"] = "A evidência não permite reenvio sem risco de duplicação."
            finding.remediation = remediation
            return True
        if not external_delivery_allowed():
            if (
                finding.state == "waiting_human"
                and remediation.get("retryBlockedReason")
                == "Entrega externa está bloqueada no ambiente local; nenhuma mensagem foi enviada."
            ):
                return False
            finding.state = "waiting_human"
            remediation["retryBlockedReason"] = (
                "Entrega externa está bloqueada no ambiente local; nenhuma mensagem foi enviada."
            )
            remediation["externalActionsExecuted"] = 0
            finding.remediation = remediation
            finding.version += 1
            return True
        if finding.message_id is None:
            return False
        message = await self.db.scalar(
            select(OmnichannelMessage).where(
                OmnichannelMessage.workspace_id == workspace_id,
                OmnichannelMessage.id == finding.message_id,
                OmnichannelMessage.thread_id == finding.thread_id,
            )
        )
        if message is None or message.direction != MessageDirection.outbound:
            return False
        if message.external_message_id:
            finding.state = "waiting_human"
            remediation["retryBlockedReason"] = "A mensagem ganhou identificador externo; requer conciliação."
            finding.remediation = remediation
            finding.version += 1
            return True

        endpoint, connection_key = await self._resolve_retry_route(workspace_id, message)
        if endpoint is None or not connection_key:
            finding.state = "waiting_human"
            remediation["retryBlockedReason"] = "Não há uma rota única e verificável para o reenvio."
            finding.remediation = remediation
            finding.version += 1
            return True
        scheduled, _ = await self.omnichannel.create_scheduled_message(
            workspace_id,
            finding.thread_id,
            ScheduledMessageCreate(
                endpoint_id=endpoint.id,
                connection_key=connection_key,
                channel=message.channel,
                body=message.body,
                template_id=message.template_id,
                message_payload={
                    "source": "klm_conversation_auditor",
                    "auditFindingId": str(finding.id),
                    "originalMessageId": str(message.id),
                    "automatic": automatic,
                },
                scheduled_at=_now() + timedelta(seconds=10),
                idempotency_key=f"auditor-retry:{message.id}:{finding.id}",
            ),
            user_id=actor_user_id,
            author_type="agent" if automatic else "human",
            author_label="Auditor KLM" if automatic else None,
        )
        finding.retry_schedule_id = scheduled.id
        finding.retry_count += 1
        finding.next_retry_at = scheduled.scheduled_at
        finding.state = "retry_scheduled"
        remediation["retryScheduleId"] = str(scheduled.id)
        remediation["externalActionsExecuted"] = 0
        finding.remediation = remediation
        finding.version += 1
        await self.operations.record_audit(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            actor_type="agent" if automatic else "human",
            actor_label="Auditor KLM" if automatic else "Usuário KLM",
            event_type="omnichannel_audit_retry_scheduled",
            entity_type="omnichannel_audit_finding",
            entity_id=finding.id,
            summary="Reenvio seguro agendado pelo Auditor KLM.",
            correlation_id=scheduled.idempotency_key,
            after_data={"scheduledMessageId": str(scheduled.id)},
        )
        return True

    async def _resolve_retry_route(
        self,
        workspace_id: UUID,
        message: OmnichannelMessage,
    ) -> tuple[OmnichannelEndpoint | None, str | None]:
        endpoint = None
        if message.endpoint_id:
            endpoint = await self.db.scalar(
                select(OmnichannelEndpoint).where(
                    OmnichannelEndpoint.workspace_id == workspace_id,
                    OmnichannelEndpoint.id == message.endpoint_id,
                )
            )
        if endpoint is None and message.binding_id:
            binding = await self.db.scalar(
                select(OmnichannelThreadBinding).where(
                    OmnichannelThreadBinding.workspace_id == workspace_id,
                    OmnichannelThreadBinding.id == message.binding_id,
                    OmnichannelThreadBinding.thread_id == message.thread_id,
                )
            )
            if binding and binding.endpoint_id:
                endpoint = await self.db.scalar(
                    select(OmnichannelEndpoint).where(
                        OmnichannelEndpoint.workspace_id == workspace_id,
                        OmnichannelEndpoint.id == binding.endpoint_id,
                    )
                )
        if endpoint is None:
            return None, None
        if endpoint.blocked_at is not None:
            return None, None
        connection_key = endpoint.connection_key
        if message.connection_id:
            connection = await self.db.scalar(
                select(OmnichannelChannelConnection).where(
                    OmnichannelChannelConnection.workspace_id == workspace_id,
                    OmnichannelChannelConnection.id == message.connection_id,
                    OmnichannelChannelConnection.channel == message.channel.value,
                )
            )
            if connection is None or connection.connection_key != endpoint.connection_key:
                return None, None
            connection_key = connection.connection_key
        return endpoint, connection_key

    async def _reconcile_retries(
        self,
        workspace_id: UUID,
        findings: Iterable[OmnichannelAuditFinding],
        now: datetime,
    ) -> None:
        indexed = {item.retry_schedule_id: item for item in findings if item.retry_schedule_id}
        if not indexed:
            return
        schedules = list(
            (
                await self.db.scalars(
                    select(OmnichannelScheduledMessage).where(
                        OmnichannelScheduledMessage.workspace_id == workspace_id,
                        OmnichannelScheduledMessage.id.in_(list(indexed)),
                    )
                )
            ).all()
        )
        for schedule in schedules:
            finding = indexed[schedule.id]
            if schedule.state == "sent" and finding.state != "resolved":
                finding.state = "resolved"
                finding.resolved_at = schedule.completed_at or now
                finding.next_retry_at = None
                finding.version += 1
                finding.remediation = {
                    **dict(finding.remediation or {}),
                    "resolution": "Reenvio aceito pelo provedor e conciliado.",
                    "providerMessageId": schedule.provider_message_id,
                }
            elif schedule.state in {"failed", "delivery_uncertain"}:
                finding.state = "waiting_human"
                finding.next_retry_at = None
                finding.version += 1
                finding.remediation = {
                    **dict(finding.remediation or {}),
                    "requiresHumanReview": True,
                    "retryBlockedReason": (
                        "O reenvio falhou ou ficou incerto; não haverá nova tentativa automática."
                    ),
                }

    async def list_findings(
        self,
        workspace_id: UUID,
        *,
        thread_id: UUID | None = None,
        state: str | None = None,
        severity: str | None = None,
        finding_type: str | None = None,
        query: str | None = None,
        limit: int = 100,
    ) -> list[OmnichannelAuditFinding]:
        filters = [OmnichannelAuditFinding.workspace_id == workspace_id]
        if thread_id:
            filters.append(OmnichannelAuditFinding.thread_id == thread_id)
        if state:
            filters.append(OmnichannelAuditFinding.state == state)
        if severity:
            filters.append(OmnichannelAuditFinding.severity == severity)
        if finding_type:
            filters.append(OmnichannelAuditFinding.finding_type == finding_type)
        if query and query.strip():
            term = query.strip()
            filters.append(
                or_(
                    OmnichannelAuditFinding.title.contains(term, autoescape=True),
                    OmnichannelAuditFinding.summary.contains(term, autoescape=True),
                    OmnichannelAuditFinding.finding_type.contains(term, autoescape=True),
                )
            )
        result = await self.db.scalars(
            select(OmnichannelAuditFinding)
            .where(*filters)
            .order_by(
                OmnichannelAuditFinding.detected_at.desc(),
                OmnichannelAuditFinding.id.desc(),
            )
            .limit(max(1, min(limit, 500)))
        )
        return list(result.all())

    async def dashboard(
        self,
        workspace_id: UUID,
        *,
        thread_id: UUID | None = None,
        state: str | None = None,
        severity: str | None = None,
        finding_type: str | None = None,
        query: str | None = None,
        limit: int = 100,
        inspected_threads: int | None = None,
    ) -> OmnichannelAuditDashboardRead:
        findings = await self.list_findings(
            workspace_id,
            thread_id=thread_id,
            state=state,
            severity=severity,
            finding_type=finding_type,
            query=query,
            limit=limit,
        )
        scope_filters = [OmnichannelAuditFinding.workspace_id == workspace_id]
        thread_filters = [
            OmnichannelThread.workspace_id == workspace_id,
            OmnichannelThread.status.in_(ACTIVE_THREAD_STATUSES),
        ]
        if thread_id:
            scope_filters.append(OmnichannelAuditFinding.thread_id == thread_id)
            thread_filters.append(OmnichannelThread.id == thread_id)
        active_filters = [*scope_filters, OmnichannelAuditFinding.state.in_(ACTIVE_FINDING_STATES)]
        counts = dict(
            (
                await self.db.execute(
                    select(
                        OmnichannelAuditFinding.state,
                        func.count(OmnichannelAuditFinding.id),
                    )
                    .where(*scope_filters)
                    .group_by(OmnichannelAuditFinding.state)
                )
            ).all()
        )
        active_thread_count = int(
            await self.db.scalar(
                select(func.count(func.distinct(OmnichannelAuditFinding.thread_id))).where(
                    *active_filters
                )
            )
            or 0
        )
        response_issue_threads = int(
            await self.db.scalar(
                select(func.count(func.distinct(OmnichannelAuditFinding.thread_id))).where(
                    *active_filters,
                    OmnichannelAuditFinding.finding_type == "unanswered_lead",
                )
            )
            or 0
        )
        delivery_issue_threads = int(
            await self.db.scalar(
                select(func.count(func.distinct(OmnichannelAuditFinding.thread_id))).where(
                    *active_filters,
                    OmnichannelAuditFinding.finding_type.in_(
                        ("delivery_failed", "delivery_stalled", "projection_mismatch")
                    ),
                )
            )
            or 0
        )
        total_threads = inspected_threads
        if total_threads is None:
            total_threads = int(
                await self.db.scalar(
                    select(func.count(OmnichannelThread.id)).where(*thread_filters)
                )
                or 0
            )
        critical_count = int(
            await self.db.scalar(
                select(func.count(OmnichannelAuditFinding.id)).where(
                    *active_filters,
                    OmnichannelAuditFinding.severity == "critical",
                )
            )
            or 0
        )
        healthy_threads = max(0, total_threads - active_thread_count)
        return OmnichannelAuditDashboardRead(
            findings=findings,
            total=sum(int(value) for value in counts.values()),
            open_count=int(counts.get("open", 0)),
            critical_count=critical_count,
            retry_scheduled_count=int(counts.get("retry_scheduled", 0)),
            waiting_human_count=int(counts.get("waiting_human", 0)),
            resolved_count=int(counts.get("resolved", 0)),
            inspected_threads=total_threads,
            healthy_threads=healthy_threads,
            coverage_percent=_percent(healthy_threads, total_threads),
            response_coverage_percent=_percent(
                max(0, total_threads - response_issue_threads), total_threads
            ),
            delivery_integrity_percent=_percent(
                max(0, total_threads - delivery_issue_threads), total_threads
            ),
            generated_at=_now(),
        )

    async def act_on_finding(
        self,
        workspace_id: UUID,
        finding_id: UUID,
        payload: OmnichannelAuditFindingAction,
        *,
        user_id: UUID,
    ) -> OmnichannelAuditFinding:
        finding = await self.db.scalar(
            select(OmnichannelAuditFinding)
            .where(
                OmnichannelAuditFinding.workspace_id == workspace_id,
                OmnichannelAuditFinding.id == finding_id,
            )
            .with_for_update()
        )
        if finding is None:
            raise LookupError("Apontamento do Auditor não encontrado.")
        if finding.version != payload.expected_version:
            raise ConversationAuditVersionConflictError(finding.version)
        before = {"state": finding.state, "version": finding.version}
        if payload.action in {"resolve", "dismiss"}:
            finding.state = "resolved" if payload.action == "resolve" else "dismissed"
            finding.resolved_at = _now()
            finding.remediation = {
                **dict(finding.remediation or {}),
                "resolution": payload.reason or (
                    "Resolvido manualmente."
                    if payload.action == "resolve"
                    else "Descartado após revisão humana."
                ),
                "resolvedByUserId": str(user_id),
            }
            finding.version += 1
        else:
            changed = await self._attempt_retry(
                workspace_id,
                finding,
                actor_user_id=user_id,
                automatic=False,
            )
            if not changed:
                raise ValueError("Este apontamento não admite uma nova tentativa segura.")
        await self.operations.record_audit(
            workspace_id=workspace_id,
            actor_user_id=user_id,
            actor_type="human",
            actor_label="Usuário KLM",
            event_type=f"omnichannel_audit_finding_{payload.action}",
            entity_type="omnichannel_audit_finding",
            entity_id=finding.id,
            summary=f"Ação '{payload.action}' aplicada ao apontamento do Auditor.",
            correlation_id=f"audit-action:{finding.id}:{finding.version}",
            before_data=before,
            after_data={"state": finding.state, "version": finding.version},
        )
        await self.db.flush()
        await self.db.refresh(finding)
        queue_realtime_event(
            self.db,
            workspace_id=workspace_id,
            event_type="OMNICHANNEL_AUDIT_UPDATED",
            data={
                "thread_ids": [str(finding.thread_id)],
                "finding_id": str(finding.id),
                "state": finding.state,
            },
        )
        return finding


__all__ = [
    "ACTIVE_FINDING_STATES",
    "ConversationAuditVersionConflictError",
    "ConversationAuditorService",
]
