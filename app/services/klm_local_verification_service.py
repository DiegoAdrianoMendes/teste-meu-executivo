"""Remove somente artefatos transitórios produzidos pelo hardening local."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import Base
from app.models.agent_platform import AgentDeployment
from app.models.agent_run import AgentRun, AgentRunStatus
from app.models.approval import ApprovalQueueItem, ApprovalStatus
from app.models.auth_security import AuthRateLimitEvent, AuthRateLimitScope, AuthSecurityEvent
from app.models.booking_link import BookingLink
from app.models.client import Client
from app.models.contract_billing import ContractBillingProposal
from app.models.conversation import Conversation
from app.models.document import Document
from app.models.finance import FinanceEntry, FinanceEntryStatus, FinanceEntryType
from app.models.finance_dimensional import FinanceAllocation, FinanceCostCenter
from app.models.flow import Flow
from app.models.identity import (
    AuthRateLimitBucket,
    IdentityIdempotencyRecord,
    IdentityOutboxMessage,
    WorkspaceInvitation,
)
from app.models.klm_operations import (
    AcquisitionEvent,
    EmailIngestReceipt,
    ExternalActionIntent,
    FollowUpTask,
    KlmAuditEvent,
    OmnichannelMessage,
    OmnichannelThread,
    ThreadStatus,
)
from app.models.knowledge_chunk import KnowledgeChunk
from app.models.matter import Matter
from app.models.meeting import (
    Meeting,
    MeetingCreationReceipt,
    MeetingParticipant,
    MeetingStatus,
)
from app.models.omnichannel_v6 import OmnichannelMessageEvidenceEvent
from app.models.timeline_event import TimelineEvent
from app.models.user import User
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership
from app.services.auth_rate_limit_service import opaque_auth_digest
from app.services.meeting_service import MeetingService


class KlmLocalVerificationService:
    """Clean a fixed QA namespace without accepting user-supplied selectors."""

    def __init__(self, db: AsyncSession):
        self.db = db

    @staticmethod
    def _finance_seed_prefix(run_id: str) -> str:
        return f"E2E Finance {run_id}"

    async def seed_agent_budget_e2e(
        self,
        workspace_id: UUID,
        *,
        deployment_id: UUID,
        run_id: str,
    ) -> dict[str, Any]:
        """Consume one deployment budget deterministically in local browser QA."""
        deployment = await self.db.scalar(
            select(AgentDeployment).where(
                AgentDeployment.id == deployment_id,
                AgentDeployment.workspace_id == workspace_id,
            )
        )
        if deployment is None:
            raise LookupError("deployment not found")
        limit = int((deployment.budget_policy or {}).get("monthlyTokenLimit") or 0)
        if limit <= 0:
            raise ValueError("deployment has no finite token budget")
        dedupe_key = f"local-e2e-agent-budget:{run_id}"
        existing = await self.db.scalar(
            select(AgentRun).where(
                AgentRun.workspace_id == workspace_id,
                AgentRun.dedupe_key == dedupe_key,
            )
        )
        if existing is not None and existing.deployment_id != deployment.id:
            raise ValueError("run id already belongs to another deployment")
        if existing is None:
            existing = AgentRun(
                workspace_id=workspace_id,
                context_type="workspace",
                deployment_id=deployment.id,
                agent_name=deployment.agent_key,
                trigger_source="local_e2e_budget",
                dedupe_key=dedupe_key,
                status=AgentRunStatus.completed,
                input_tokens=limit,
                output_tokens=0,
                attempt_count=1,
                input_payload={"localE2ERunId": run_id},
                output_payload={"summary": "Consumo determinístico de orçamento do QA local."},
            )
            self.db.add(existing)
            await self.db.flush()
        return {
            "run_id": run_id,
            "deployment_id": deployment.id,
            "agent_run_id": existing.id,
            "consumed_tokens": int(existing.input_tokens + existing.output_tokens),
        }

    async def seed_finance_e2e(
        self,
        workspace_id: UUID,
        *,
        run_id: str,
        record_count: int,
    ) -> dict[str, Any]:
        """Create a coherent, isolated finance hierarchy for real browser QA.

        The caller is a local-only authenticated endpoint. Idempotency is
        enforced by the external reference and cost-center code rather than by
        trusting a browser retry.
        """
        prefix = self._finance_seed_prefix(run_id)
        existing_entry = await self.db.scalar(
            select(FinanceEntry).where(
                FinanceEntry.workspace_id == workspace_id,
                FinanceEntry.external_ref == f"e2e-finance:{run_id}:000",
            )
        )
        if existing_entry is not None:
            allocation = await self.db.scalar(
                select(FinanceAllocation).where(
                    FinanceAllocation.workspace_id == workspace_id,
                    FinanceAllocation.finance_entry_id == existing_entry.id,
                )
            )
            contract = await self.db.get(ContractBillingProposal, allocation.contract_id)
            return {
                "run_id": run_id,
                "record_count": int(
                    await self.db.scalar(
                        select(func.count(FinanceEntry.id)).where(
                            FinanceEntry.workspace_id == workspace_id,
                            FinanceEntry.external_ref.like(f"e2e-finance:{run_id}:%"),
                        )
                    )
                    or 0
                ),
                "cost_center_id": allocation.cost_center_id,
                "client_id": allocation.client_id,
                "contract_id": contract.id,
                "matter_id": allocation.matter_id,
                "finance_entry_id": existing_entry.id,
                "search_prefix": prefix,
            }

        center = FinanceCostCenter(
            workspace_id=workspace_id,
            code=f"E2E-{run_id}",
            name=f"Centro {prefix}",
            description="Massa isolada do browser E2E local.",
        )
        self.db.add(center)
        await self.db.flush()

        first: dict[str, UUID] | None = None
        for index in range(record_count):
            suffix = f"{index:03d}"
            client = Client(
                workspace_id=workspace_id,
                name=f"{prefix} Cliente {suffix}",
                email=f"finance-{run_id}-{suffix}@local.invalid",
            )
            self.db.add(client)
            await self.db.flush()
            matter = Matter(
                workspace_id=workspace_id,
                client_id=client.id,
                demand_type=f"Caso financeiro {prefix} {suffix}",
            )
            self.db.add(matter)
            await self.db.flush()
            contract = ContractBillingProposal(
                workspace_id=workspace_id,
                matter_id=matter.id,
                source_document_name=f"Contrato {prefix} {suffix}",
                items=[],
            )
            self.db.add(contract)
            await self.db.flush()
            entry = FinanceEntry(
                workspace_id=workspace_id,
                matter_id=matter.id,
                title=f"Lançamento {prefix} {suffix}",
                entry_type=FinanceEntryType.receivable,
                status=FinanceEntryStatus.paid,
                amount=Decimal("100.00") + index,
                external_ref=f"e2e-finance:{run_id}:{suffix}",
                entry_metadata={"localE2ERunId": run_id},
            )
            self.db.add(entry)
            await self.db.flush()
            allocation = FinanceAllocation(
                workspace_id=workspace_id,
                finance_entry_id=entry.id,
                cost_center_id=center.id,
                client_id=client.id,
                contract_id=contract.id,
                matter_id=matter.id,
                percentage=Decimal("100.0000"),
                amount=entry.amount,
                source="local_e2e",
                audit_metadata={"runId": run_id},
            )
            self.db.add(allocation)
            if first is None:
                first = {
                    "client_id": client.id,
                    "contract_id": contract.id,
                    "matter_id": matter.id,
                    "finance_entry_id": entry.id,
                }
        await self.db.flush()
        assert first is not None
        return {
            "run_id": run_id,
            "record_count": record_count,
            "cost_center_id": center.id,
            **first,
            "search_prefix": prefix,
        }

    async def cleanup_finance_e2e(self, workspace_id: UUID, *, run_id: str) -> int:
        entry_ids = list(
            (
                await self.db.scalars(
                    select(FinanceEntry.id).where(
                        FinanceEntry.workspace_id == workspace_id,
                        FinanceEntry.external_ref.like(f"e2e-finance:{run_id}:%"),
                    )
                )
            ).all()
        )
        if not entry_ids:
            return 0
        allocations = list(
            (
                await self.db.scalars(
                    select(FinanceAllocation).where(
                        FinanceAllocation.workspace_id == workspace_id,
                        FinanceAllocation.finance_entry_id.in_(entry_ids),
                    )
                )
            ).all()
        )
        contract_ids = {item.contract_id for item in allocations if item.contract_id}
        matter_ids = {item.matter_id for item in allocations if item.matter_id}
        client_ids = {item.client_id for item in allocations if item.client_id}
        center_ids = {item.cost_center_id for item in allocations}
        await self.db.execute(
            delete(FinanceAllocation).where(
                FinanceAllocation.id.in_([item.id for item in allocations])
            )
        )
        await self.db.execute(delete(FinanceEntry).where(FinanceEntry.id.in_(entry_ids)))
        if contract_ids:
            await self.db.execute(
                delete(ContractBillingProposal).where(ContractBillingProposal.id.in_(contract_ids))
            )
        if matter_ids:
            await self.db.execute(delete(Matter).where(Matter.id.in_(matter_ids)))
        if client_ids:
            await self.db.execute(delete(Client).where(Client.id.in_(client_ids)))
        if center_ids:
            await self.db.execute(
                delete(FinanceCostCenter).where(FinanceCostCenter.id.in_(center_ids))
            )
        await self.db.flush()
        return len(entry_ids)

    async def cleanup(
        self,
        workspace_id: UUID,
        *,
        actor_user_id: UUID,
    ) -> dict[str, int]:
        counts: defaultdict[str, int] = defaultdict(int)

        qa_memberships = list(
            (
                await self.db.scalars(
                    select(WorkspaceMembership)
                    .join(User, User.id == WorkspaceMembership.user_id)
                    .where(
                        WorkspaceMembership.workspace_id == workspace_id,
                        User.email.like("gestora-qa-e2e-%@example.com"),
                        User.deleted_at.is_(None),
                    )
                )
            ).all()
        )
        qa_membership_ids = {membership.id for membership in qa_memberships}
        qa_user_ids = {membership.user_id for membership in qa_memberships}
        qa_users = (
            list((await self.db.scalars(select(User).where(User.id.in_(qa_user_ids)))).all())
            if qa_user_ids
            else []
        )
        qa_invitations = (
            list(
                (
                    await self.db.scalars(
                        select(WorkspaceInvitation).where(
                            WorkspaceInvitation.workspace_id == workspace_id,
                            WorkspaceInvitation.membership_id.in_(qa_membership_ids),
                        )
                    )
                ).all()
            )
            if qa_membership_ids
            else []
        )
        qa_invitation_ids = {invitation.id for invitation in qa_invitations}
        qa_delivery_ids = {
            invitation.last_delivery_id
            for invitation in qa_invitations
            if invitation.last_delivery_id is not None
        }

        qa_client_ids = set(
            (
                await self.db.scalars(
                    select(Client.id).where(
                        Client.workspace_id == workspace_id,
                        Client.name == "Lead de verificação",
                        Client.email.like("%@local.invalid"),
                    )
                )
            ).all()
        )
        qa_matter_ids = (
            set(
                (
                    await self.db.scalars(
                        select(Matter.id).where(
                            Matter.workspace_id == workspace_id,
                            Matter.client_id.in_(qa_client_ids),
                        )
                    )
                ).all()
            )
            if qa_client_ids
            else set()
        )
        qa_thread_ids = (
            set(
                (
                    await self.db.scalars(
                        select(OmnichannelThread.id).where(
                            OmnichannelThread.workspace_id == workspace_id,
                            or_(
                                OmnichannelThread.client_id.in_(qa_client_ids),
                                OmnichannelThread.matter_id.in_(qa_matter_ids),
                            ),
                        )
                    )
                ).all()
            )
            if qa_client_ids or qa_matter_ids
            else set()
        )

        run_conditions = [
            AgentRun.dedupe_key.like("%:verify-agent-%"),
            AgentRun.dedupe_key.like("%:verify-simulation-%"),
            AgentRun.dedupe_key.like("local-e2e-agent-budget:%"),
            ~AgentRun.matter_id.in_(select(Matter.id).where(Matter.workspace_id == workspace_id)),
        ]
        if qa_matter_ids:
            run_conditions.append(AgentRun.matter_id.in_(qa_matter_ids))
        verification_runs = list(
            (
                await self.db.scalars(
                    select(AgentRun).where(
                        AgentRun.workspace_id == workspace_id,
                        or_(*run_conditions),
                    )
                )
            ).all()
        )
        run_ids = {run.id for run in verification_runs}
        affected_matter_ids = {run.matter_id for run in verification_runs}

        crm_events = await self._crm_events_for_runs(workspace_id, verification_runs)
        restored_matter_ids: set[UUID] = set()
        for event in sorted(crm_events, key=lambda item: item.occurred_at, reverse=True):
            if not event.entity_id or event.entity_id in qa_matter_ids:
                continue
            matter = await self.db.scalar(
                select(Matter).where(
                    Matter.id == event.entity_id,
                    Matter.workspace_id == workspace_id,
                    Matter.deleted_at.is_(None),
                )
            )
            before = event.before_data or {}
            if not matter or not {"leadScore", "nextAction", "qualification"}.issubset(before):
                continue
            matter.lead_score = int(before["leadScore"])
            matter.next_action = before["nextAction"]
            matter.sdr_qualification = before["qualification"]
            restored_matter_ids.add(matter.id)

        conversation_ids = await self._conversation_ids_for_runs(verification_runs)
        if qa_matter_ids:
            conversation_ids.update(
                (
                    await self.db.scalars(
                        select(Conversation.id).where(Conversation.matter_id.in_(qa_matter_ids))
                    )
                ).all()
            )
        timeline_ids = set()
        if affected_matter_ids:
            timeline_rows = (
                await self.db.scalars(
                    select(TimelineEvent).where(TimelineEvent.matter_id.in_(affected_matter_ids))
                )
            ).all()
            timeline_ids = {
                row.id
                for row in timeline_rows
                if str((row.event_data or {}).get("agent_run_id") or "")
                in {str(run_id) for run_id in run_ids}
            }
        if qa_matter_ids:
            timeline_ids.update(
                (
                    await self.db.scalars(
                        select(TimelineEvent.id).where(TimelineEvent.matter_id.in_(qa_matter_ids))
                    )
                ).all()
            )
        document_conditions = [
            and_(
                Document.name == "negativa-local.pdf",
                ~Document.matter_id.in_(select(Matter.id)),
            )
        ]
        if qa_matter_ids:
            document_conditions.append(Document.matter_id.in_(qa_matter_ids))
        document_ids = set(
            (await self.db.scalars(select(Document.id).where(or_(*document_conditions)))).all()
        )

        message_ids = (
            set(
                (
                    await self.db.scalars(
                        select(OmnichannelMessage.id).where(
                            OmnichannelMessage.workspace_id == workspace_id,
                            or_(
                                OmnichannelMessage.idempotency_key.like("verify-message-%"),
                                OmnichannelMessage.idempotency_key.like("email:verify-email-%"),
                                OmnichannelMessage.idempotency_key.in_(
                                    [f"agent-run:{run_id}" for run_id in run_ids]
                                ),
                                OmnichannelMessage.thread_id.in_(qa_thread_ids),
                            ),
                        )
                    )
                ).all()
            )
            if run_ids or qa_thread_ids
            else set(
                (
                    await self.db.scalars(
                        select(OmnichannelMessage.id).where(
                            OmnichannelMessage.workspace_id == workspace_id,
                            or_(
                                OmnichannelMessage.idempotency_key.like("verify-message-%"),
                                OmnichannelMessage.idempotency_key.like("email:verify-email-%"),
                            ),
                        )
                    )
                ).all()
            )
        )

        acquisition_ids = set(
            (
                await self.db.scalars(
                    select(AcquisitionEvent.id).where(
                        AcquisitionEvent.workspace_id == workspace_id,
                        AcquisitionEvent.dedupe_key.like("verify-track-%"),
                    )
                )
            ).all()
        )
        intent_ids = set(
            (
                await self.db.scalars(
                    select(ExternalActionIntent.id).where(
                        ExternalActionIntent.workspace_id == workspace_id,
                        or_(
                            ExternalActionIntent.idempotency_key.like("verify-intent-%"),
                            ExternalActionIntent.idempotency_key.like("message:verify-message-%"),
                        ),
                    )
                )
            ).all()
        )
        receipt_ids = set(
            (
                await self.db.scalars(
                    select(EmailIngestReceipt.id).where(
                        EmailIngestReceipt.workspace_id == workspace_id,
                        EmailIngestReceipt.provider_message_id.like("verify-email-%"),
                    )
                )
            ).all()
        )
        approval_ids = (
            set(
                (
                    await self.db.scalars(
                        select(ApprovalQueueItem.id).where(
                            or_(
                                ApprovalQueueItem.matter_id.in_(qa_matter_ids),
                                ApprovalQueueItem.document_version_url.in_(
                                    [f"local://agent-run/{run_id}" for run_id in run_ids]
                                ),
                            )
                        )
                    )
                ).all()
            )
            if qa_matter_ids or run_ids
            else set()
        )
        booking_link_ids = set(
            (
                await self.db.scalars(
                    select(BookingLink.id).where(
                        BookingLink.workspace_id == workspace_id,
                        BookingLink.title.like("QA descartável %"),
                    )
                )
            ).all()
        )
        meeting_conditions = [Meeting.title.like("QA descartável %")]
        if qa_matter_ids:
            meeting_conditions.append(Meeting.matter_id.in_(qa_matter_ids))
        meeting_ids = set(
            (
                await self.db.scalars(
                    select(Meeting.id).where(
                        Meeting.workspace_id == workspace_id,
                        or_(*meeting_conditions),
                    )
                )
            ).all()
        )
        if booking_link_ids:
            qa_booking_recipients = {
                f"booking-{booking_link_id}" for booking_link_id in booking_link_ids
            }
            meeting_rows = (
                await self.db.scalars(select(Meeting).where(Meeting.workspace_id == workspace_id))
            ).all()
            meeting_ids.update(
                meeting.id
                for meeting in meeting_rows
                if any(
                    str(recipient.get("id") or "") in qa_booking_recipients
                    for recipient in (meeting.recipient_overrides or [])
                    if isinstance(recipient, dict)
                )
            )

        meeting_rows = (
            list(
                (
                    await self.db.scalars(
                        select(Meeting).where(
                            Meeting.workspace_id == workspace_id,
                            Meeting.id.in_(meeting_ids),
                        )
                    )
                ).all()
            )
            if meeting_ids
            else []
        )

        # POST /meetings produces an immutable creation receipt.  The meeting
        # and every parent/identity reached by that retained evidence must not
        # be physically removed by the local QA cleanup.
        retained_matter_ids = {
            meeting.matter_id
            for meeting in meeting_rows
            if meeting.matter_id is not None and meeting.matter_id in qa_matter_ids
        }
        retained_client_ids = (
            set(
                (
                    await self.db.scalars(
                        select(Matter.client_id).where(
                            Matter.workspace_id == workspace_id,
                            Matter.id.in_(retained_matter_ids),
                        )
                    )
                ).all()
            )
            & qa_client_ids
            if retained_matter_ids
            else set()
        )

        evidence_reference_rows = (
            (
                await self.db.execute(
                    select(
                        OmnichannelMessageEvidenceEvent.message_id,
                        OmnichannelMessageEvidenceEvent.thread_id,
                    ).where(
                        OmnichannelMessageEvidenceEvent.workspace_id == workspace_id,
                        or_(
                            OmnichannelMessageEvidenceEvent.message_id.in_(message_ids),
                            OmnichannelMessageEvidenceEvent.thread_id.in_(qa_thread_ids),
                        ),
                    )
                )
            ).all()
            if message_ids or qa_thread_ids
            else []
        )
        retained_message_ids = {
            message_id for message_id, _ in evidence_reference_rows if message_id in message_ids
        }
        retained_thread_ids = {
            thread_id for _, thread_id in evidence_reference_rows if thread_id in qa_thread_ids
        }
        retained_message_rows = (
            list(
                (
                    await self.db.scalars(
                        select(OmnichannelMessage).where(
                            OmnichannelMessage.workspace_id == workspace_id,
                            OmnichannelMessage.id.in_(retained_message_ids),
                        )
                    )
                ).all()
            )
            if retained_message_ids
            else []
        )
        retained_thread_ids.update(
            message.thread_id
            for message in retained_message_rows
            if message.thread_id in qa_thread_ids
        )
        retained_thread_rows = (
            list(
                (
                    await self.db.scalars(
                        select(OmnichannelThread).where(
                            OmnichannelThread.workspace_id == workspace_id,
                            OmnichannelThread.id.in_(retained_thread_ids),
                        )
                    )
                ).all()
            )
            if retained_thread_ids
            else []
        )
        retained_matter_ids.update(
            message.matter_id
            for message in retained_message_rows
            if message.matter_id in qa_matter_ids
        )
        retained_matter_ids.update(
            thread.matter_id for thread in retained_thread_rows if thread.matter_id in qa_matter_ids
        )
        retained_client_ids.update(
            thread.client_id for thread in retained_thread_rows if thread.client_id in qa_client_ids
        )
        if retained_matter_ids:
            retained_client_ids.update(
                set(
                    (
                        await self.db.scalars(
                            select(Matter.client_id).where(
                                Matter.workspace_id == workspace_id,
                                Matter.id.in_(retained_matter_ids),
                            )
                        )
                    ).all()
                )
                & qa_client_ids
            )

        # Identity deletion is fail-safe across the whole workspace. A QA user
        # may be referenced by a non-QA meeting and therefore must be retained
        # even when that meeting is outside this cleanup's selector.
        participant_identity_rows = (
            (
                await self.db.execute(
                    select(MeetingParticipant.membership_id, MeetingParticipant.user_id).where(
                        MeetingParticipant.workspace_id == workspace_id,
                        or_(
                            MeetingParticipant.membership_id.in_(qa_membership_ids),
                            MeetingParticipant.user_id.in_(qa_user_ids),
                        ),
                    )
                )
            ).all()
            if qa_membership_ids or qa_user_ids
            else []
        )
        retained_membership_ids = {
            membership_id
            for membership_id, _ in participant_identity_rows
            if membership_id in qa_membership_ids
        }
        retained_user_ids = {
            user_id for _, user_id in participant_identity_rows if user_id in qa_user_ids
        }
        retained_user_ids.update(
            meeting.responsible_user_id
            for meeting in meeting_rows
            if meeting.responsible_user_id in qa_user_ids
        )
        receipt_actor_ids = (
            set(
                (
                    await self.db.scalars(
                        select(MeetingCreationReceipt.actor_user_id).where(
                            MeetingCreationReceipt.workspace_id == workspace_id,
                            MeetingCreationReceipt.actor_user_id.in_(qa_user_ids),
                        )
                    )
                ).all()
            )
            if qa_user_ids
            else set()
        )
        retained_user_ids.update(receipt_actor_ids & qa_user_ids)

        flow_ids = set(
            (
                await self.db.scalars(
                    select(Flow.id).where(
                        Flow.workspace_id == workspace_id,
                        Flow.deleted_at.is_(None),
                        Flow.name.like("QA descartável %"),
                    )
                )
            ).all()
        )

        source_refs = {str(run_id) for run_id in run_ids} | {
            str(conversation_id) for conversation_id in conversation_ids
        }
        knowledge_ids = (
            set(
                (
                    await self.db.scalars(
                        select(KnowledgeChunk.id).where(
                            KnowledgeChunk.workspace_id == workspace_id,
                            or_(
                                KnowledgeChunk.matter_id.in_(qa_matter_ids),
                                KnowledgeChunk.source_ref.in_(source_refs),
                            ),
                        )
                    )
                ).all()
            )
            if qa_matter_ids or source_refs
            else set()
        )

        counts["knowledge_chunks"] = await self._delete_ids(KnowledgeChunk, knowledge_ids)
        counts["documents"] = await self._delete_ids(Document, document_ids)
        counts["conversations"] = await self._delete_ids(Conversation, conversation_ids)
        counts["timeline_events"] = await self._delete_ids(TimelineEvent, timeline_ids)
        counts["approvals"] = await self._delete_ids(ApprovalQueueItem, approval_ids)
        counts["audit_events"] = 0
        (
            counts["omnichannel_messages"],
            additionally_retained_message_ids,
        ) = await self._delete_ids_restrict_safe(
            OmnichannelMessage,
            message_ids - retained_message_ids,
        )
        retained_message_ids.update(additionally_retained_message_ids)
        if additionally_retained_message_ids:
            additionally_retained_messages = list(
                (
                    await self.db.scalars(
                        select(OmnichannelMessage).where(
                            OmnichannelMessage.workspace_id == workspace_id,
                            OmnichannelMessage.id.in_(additionally_retained_message_ids),
                        )
                    )
                ).all()
            )
            retained_thread_ids.update(
                message.thread_id
                for message in additionally_retained_messages
                if message.thread_id in qa_thread_ids
            )
            retained_matter_ids.update(
                message.matter_id
                for message in additionally_retained_messages
                if message.matter_id in qa_matter_ids
            )
        counts["external_intents"] = await self._delete_ids(ExternalActionIntent, intent_ids)
        counts["acquisition_events"] = await self._delete_ids(AcquisitionEvent, acquisition_ids)
        counts["email_receipts"] = await self._delete_ids(EmailIngestReceipt, receipt_ids)
        counts["agent_runs"] = await self._delete_ids(AgentRun, run_ids)
        counts["booking_links"] = await self._delete_ids(BookingLink, booking_link_ids)
        meeting_service = MeetingService(self.db)
        cancelled_meetings = 0
        for meeting in sorted(meeting_rows, key=lambda item: str(item.id)):
            if meeting.status == MeetingStatus.cancelled:
                continue
            await meeting_service.delete_for_workspace(
                workspace_id,
                meeting.id,
                expected_version=meeting.version,
                actor_user_id=actor_user_id,
                correlation_id=f"local-verification-cleanup:{meeting.id}",
            )
            cancelled_meetings += 1
        counts["meetings"] = cancelled_meetings
        counts["flows"] = await self._soft_delete_ids(Flow, flow_ids)

        if qa_matter_ids or qa_thread_ids:
            follow_up_result = await self.db.execute(
                delete(FollowUpTask).where(
                    FollowUpTask.workspace_id == workspace_id,
                    or_(
                        FollowUpTask.matter_id.in_(qa_matter_ids),
                        FollowUpTask.thread_id.in_(qa_thread_ids),
                    ),
                )
            )
            counts["follow_ups"] = int(follow_up_result.rowcount or 0)
        (
            counts["threads"],
            additionally_retained_thread_ids,
        ) = await self._delete_ids_restrict_safe(
            OmnichannelThread,
            qa_thread_ids - retained_thread_ids,
        )
        retained_thread_ids.update(additionally_retained_thread_ids)
        counts["threads"] += await self._close_thread_ids(retained_thread_ids)
        if additionally_retained_thread_ids:
            additionally_retained_threads = list(
                (
                    await self.db.scalars(
                        select(OmnichannelThread).where(
                            OmnichannelThread.workspace_id == workspace_id,
                            OmnichannelThread.id.in_(additionally_retained_thread_ids),
                        )
                    )
                ).all()
            )
            retained_matter_ids.update(
                thread.matter_id
                for thread in additionally_retained_threads
                if thread.matter_id in qa_matter_ids
            )
            retained_client_ids.update(
                thread.client_id
                for thread in additionally_retained_threads
                if thread.client_id in qa_client_ids
            )

        (
            counts["matters"],
            additionally_retained_matter_ids,
        ) = await self._delete_ids_restrict_safe(
            Matter,
            qa_matter_ids - retained_matter_ids,
        )
        retained_matter_ids.update(additionally_retained_matter_ids)
        counts["matters"] += await self._soft_delete_ids(Matter, retained_matter_ids)
        if retained_matter_ids:
            retained_client_ids.update(
                set(
                    (
                        await self.db.scalars(
                            select(Matter.client_id).where(
                                Matter.workspace_id == workspace_id,
                                Matter.id.in_(retained_matter_ids),
                            )
                        )
                    ).all()
                )
                & qa_client_ids
            )

        (
            counts["clients"],
            additionally_retained_client_ids,
        ) = await self._delete_ids_restrict_safe(
            Client,
            qa_client_ids - retained_client_ids,
        )
        retained_client_ids.update(additionally_retained_client_ids)
        counts["clients"] += await self._soft_delete_ids(Client, retained_client_ids)

        identity_record_ids = await self._identity_record_ids(
            workspace_id,
            qa_invitation_ids,
        )
        counts["identity_idempotency_records"] = await self._delete_ids(
            IdentityIdempotencyRecord,
            identity_record_ids,
        )
        if qa_user_ids:
            security_result = await self.db.execute(
                delete(AuthSecurityEvent).where(
                    AuthSecurityEvent.subject_user_id.in_(qa_user_ids),
                    AuthSecurityEvent.event_type == "identity_outbox_opened",
                )
            )
            counts["auth_security_events"] = int(security_result.rowcount or 0)
        rate_limit_digests = self._qa_rate_limit_digests(
            workspace_id,
            qa_users,
            qa_invitation_ids,
        )
        if rate_limit_digests:
            event_result = await self.db.execute(
                delete(AuthRateLimitEvent).where(
                    AuthRateLimitEvent.identifier_digest.in_(rate_limit_digests)
                )
            )
            counts["auth_rate_limit_events"] = int(event_result.rowcount or 0)
            bucket_result = await self.db.execute(
                delete(AuthRateLimitBucket).where(
                    AuthRateLimitBucket.key_type == "identifier",
                    AuthRateLimitBucket.key_digest.in_(rate_limit_digests),
                )
            )
            counts["auth_rate_limit_buckets"] = int(bucket_result.rowcount or 0)
        counts["workspace_invitations"] = await self._delete_ids(
            WorkspaceInvitation,
            qa_invitation_ids,
        )
        # Identities are never physically erased by QA cleanup. User and
        # membership rows are authorship anchors in many tenant and platform
        # histories, including references outside the selector visible here.
        retained_membership_ids.update(qa_membership_ids)
        counts["workspace_memberships"] += await self._suspend_membership_ids(
            retained_membership_ids
        )
        await self.db.flush()
        retained_user_ids.update(qa_user_ids)
        counts["qa_users"] += await self._deactivate_user_ids(retained_user_ids)
        counts["identity_outbox_messages"] = await self._delete_ids(
            IdentityOutboxMessage,
            qa_delivery_ids,
        )

        await self.db.flush()
        for matter_id in affected_matter_ids - qa_matter_ids:
            matter = await self.db.scalar(
                select(Matter).where(
                    Matter.id == matter_id,
                    Matter.workspace_id == workspace_id,
                    Matter.deleted_at.is_(None),
                )
            )
            if not matter:
                continue
            pending = await self.db.scalar(
                select(ApprovalQueueItem.id)
                .where(
                    ApprovalQueueItem.matter_id == matter_id,
                    ApprovalQueueItem.status == ApprovalStatus.pending,
                )
                .limit(1)
            )
            matter.needs_approval = pending is not None

        counts["restored_matters"] = len(restored_matter_ids)
        counts["artifacts_removed"] = sum(
            value for key, value in counts.items() if key != "restored_matters"
        )
        return dict(counts)

    async def _crm_events_for_runs(
        self, workspace_id: UUID, runs: list[AgentRun]
    ) -> list[KlmAuditEvent]:
        conditions = []
        for run in runs:
            if run.agent_name != "crm":
                continue
            start = run.created_at - timedelta(seconds=2)
            end = (run.completed_at or run.updated_at) + timedelta(seconds=2)
            conditions.append(
                and_(
                    KlmAuditEvent.entity_id == run.matter_id,
                    KlmAuditEvent.event_type == "agent_crm_fields_updated",
                    KlmAuditEvent.occurred_at >= start,
                    KlmAuditEvent.occurred_at <= end,
                )
            )
        if not conditions:
            return []
        return list(
            (
                await self.db.scalars(
                    select(KlmAuditEvent).where(
                        KlmAuditEvent.workspace_id == workspace_id,
                        or_(*conditions),
                    )
                )
            ).all()
        )

    async def _conversation_ids_for_runs(self, runs: list[AgentRun]) -> set[UUID]:
        conditions = []
        for run in runs:
            start = run.created_at - timedelta(seconds=2)
            end = (run.completed_at or run.updated_at) + timedelta(seconds=2)
            conditions.append(
                and_(
                    Conversation.matter_id == run.matter_id,
                    Conversation.timestamp >= start,
                    Conversation.timestamp <= end,
                )
            )
        if not conditions:
            return set()
        return set((await self.db.scalars(select(Conversation.id).where(or_(*conditions)))).all())

    async def _identity_record_ids(
        self,
        workspace_id: UUID,
        invitation_ids: set[UUID],
    ) -> set[UUID]:
        if not invitation_ids:
            return set()
        invitation_values = {str(invitation_id) for invitation_id in invitation_ids}
        operations = [
            f"workspace-invitation-accept:{invitation_id}" for invitation_id in invitation_values
        ]
        operations.extend(
            f"workspace-invitation-resend:{workspace_id}:{invitation_id}"
            for invitation_id in invitation_values
        )
        records = list(
            (
                await self.db.scalars(
                    select(IdentityIdempotencyRecord).where(
                        or_(
                            IdentityIdempotencyRecord.operation
                            == f"workspace-invitation:{workspace_id}",
                            IdentityIdempotencyRecord.operation.in_(operations),
                        )
                    )
                )
            ).all()
        )
        return {
            record.id
            for record in records
            if str(
                (record.response_data or {}).get("invitation_id")
                or (record.response_data or {}).get("invitationId")
                or ""
            )
            in invitation_values
        }

    @staticmethod
    def _qa_rate_limit_digests(
        workspace_id: UUID,
        users: list[User],
        invitation_ids: set[UUID],
    ) -> set[str]:
        values: set[tuple[AuthRateLimitScope, str]] = set()
        for user in users:
            if not user.email:
                continue
            values.add((AuthRateLimitScope.invitation, f"{workspace_id}:{user.email}"))
            values.add((AuthRateLimitScope.login, user.email))
        for invitation_id in invitation_ids:
            values.add(
                (
                    AuthRateLimitScope.accept_invitation,
                    f"invitation:{invitation_id}:delivery:token",
                )
            )
        return {
            opaque_auth_digest(
                f"rate-limit:{scope.value}:identifier",
                identifier,
            )
            for scope, identifier in values
        }

    async def _delete_ids(self, model: Any, ids: set[UUID]) -> int:
        if not ids:
            return 0
        result = await self.db.execute(delete(model).where(model.id.in_(ids)))
        return int(result.rowcount or 0)

    async def _delete_ids_restrict_safe(
        self,
        model: Any,
        ids: set[UUID],
    ) -> tuple[int, set[UUID]]:
        """Delete only rows proven unreferenced by the database.

        The QA graph evolves quickly and several immutable tables use
        ``RESTRICT`` references. A hand-maintained reference list will
        eventually become incomplete. One savepoint per local-only candidate
        lets PostgreSQL/SQLite enforce the actual FK closure without aborting
        the cleanup transaction; blocked rows are retired by the caller.
        """

        retained = await self._referenced_ids(model, ids)
        deleted = 0
        for resource_id in sorted(ids - retained, key=str):
            try:
                async with self.db.begin_nested():
                    result = await self.db.execute(
                        delete(model)
                        .where(model.id == resource_id)
                        .execution_options(synchronize_session=False)
                    )
            except IntegrityError:
                retained.add(resource_id)
            else:
                deleted += int(result.rowcount or 0)
        return deleted, retained

    async def _referenced_ids(self, model: Any, ids: set[UUID]) -> set[UUID]:
        """Return candidates referenced by any currently mapped FK.

        This deliberately treats CASCADE and SET NULL as references too:
        cleanup must not erase or detach operational history merely because
        the schema would technically permit it.
        """

        if not ids:
            return set()
        target_table = model.__table__
        referenced: set[UUID] = set()
        for table in Base.metadata.sorted_tables:
            for foreign_key in table.foreign_keys:
                if foreign_key.column.table is not target_table or foreign_key.column.name != "id":
                    continue
                parent_column = foreign_key.parent
                statement = select(parent_column).where(parent_column.in_(ids))
                if table is target_table and "id" in table.c:
                    statement = statement.where(table.c.id.not_in(ids))
                referenced.update(
                    value for value in (await self.db.scalars(statement)).all() if value in ids
                )
        return referenced

    async def _soft_delete_ids(self, model: Any, ids: set[UUID]) -> int:
        """Marca registros como excluídos sem violar FKs restritivas (ex.: Flow)."""
        if not ids:
            return 0
        result = await self.db.execute(
            update(model)
            .where(model.id.in_(ids), model.deleted_at.is_(None))
            .values(deleted_at=datetime.now(UTC))
        )
        return int(result.rowcount or 0)

    async def _suspend_membership_ids(self, ids: set[UUID]) -> int:
        if not ids:
            return 0
        result = await self.db.execute(
            update(WorkspaceMembership)
            .where(
                WorkspaceMembership.id.in_(ids),
                WorkspaceMembership.status != MembershipStatus.suspended,
            )
            .values(status=MembershipStatus.suspended, is_default=False)
        )
        return int(result.rowcount or 0)

    async def _close_thread_ids(self, ids: set[UUID]) -> int:
        if not ids:
            return 0
        result = await self.db.execute(
            update(OmnichannelThread)
            .where(
                OmnichannelThread.id.in_(ids),
                OmnichannelThread.status != ThreadStatus.closed,
            )
            .values(status=ThreadStatus.closed, assigned_human_id=None)
        )
        return int(result.rowcount or 0)

    async def _deactivate_user_ids(self, ids: set[UUID]) -> int:
        if not ids:
            return 0
        result = await self.db.execute(
            update(User)
            .where(
                User.id.in_(ids),
                or_(User.deleted_at.is_(None), User.is_active.is_(True)),
            )
            .values(
                deleted_at=datetime.now(UTC),
                is_active=False,
                platform_access_active=False,
                auth_epoch=User.auth_epoch + 1,
            )
        )
        return int(result.rowcount or 0)
