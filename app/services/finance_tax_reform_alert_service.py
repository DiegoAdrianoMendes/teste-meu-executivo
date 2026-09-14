"""Durable, source-versioned tax-reform review scheduling.

This service never sends a message itself.  It creates a canonical
``HumanDecision`` only after its durable schedule record exists; the common
human-routing service then creates in-app and phone delivery intents for the
qualified accountant and the office owner.  A provider receipt remains the
sole proof of a telephone delivery in the separate delivery worker.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging_privacy import safe_error_type
from app.models.agent_platform import HumanDecision
from app.models.finance_dimensional import FinanceTaxProfile, FinanceTaxReformAlert
from app.models.klm_operations import KlmAuditEvent
from app.services.agent_platform_service import HumanRoutingError, HumanRoutingService
from app.services.finance_tax_rules_service import due_tax_reform_reminders


class FinanceTaxReformAlertService:
    """Schedule each due D-180/D-90/D-30/D-7 accountant review exactly once."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def schedule_workspace(
        self, workspace_id: UUID, *, as_of: date | None = None
    ) -> dict[str, int]:
        as_of = as_of or datetime.now(UTC).date()
        profile = await self.db.scalar(
            select(FinanceTaxProfile).where(FinanceTaxProfile.workspace_id == workspace_id)
        )
        counts = {"called": 0, "existing": 0, "blocked": 0}
        for reminder in due_tax_reform_reminders(profile, as_of=as_of):
            outcome = await self._schedule_reminder(
                workspace_id=workspace_id,
                profile=profile,
                reminder=reminder,
            )
            counts[outcome] += 1
        return counts

    async def _schedule_reminder(
        self,
        *,
        workspace_id: UUID,
        profile: FinanceTaxProfile | None,
        reminder: dict[str, Any],
    ) -> str:
        profile_id = profile.id if profile else None
        schedule_key = str(reminder["scheduleKey"])
        existing_alert = await self.db.scalar(
            select(FinanceTaxReformAlert)
            .where(
                FinanceTaxReformAlert.workspace_id == workspace_id,
                FinanceTaxReformAlert.schedule_key == schedule_key,
            )
            .with_for_update()
        )
        if existing_alert is not None:
            return "existing"

        # A previous process may have created the canonical decision and died
        # before persisting the schedule projection. Re-link it rather than
        # calling people a second time.
        decision_key = self._decision_key(profile_id, reminder)
        existing_decision = await self.db.scalar(
            select(HumanDecision).where(
                HumanDecision.workspace_id == workspace_id,
                HumanDecision.decision_key == decision_key,
            )
        )
        try:
            async with self.db.begin_nested():
                alert = FinanceTaxReformAlert(
                    workspace_id=workspace_id,
                    tax_profile_id=profile_id,
                    human_decision_id=existing_decision.id if existing_decision else None,
                    schedule_key=schedule_key,
                    catalog_version=int(reminder["catalogVersion"]),
                    profile_version=int(reminder["profileVersion"]),
                    milestone_key=str(reminder["milestoneKey"]),
                    milestone_date=date.fromisoformat(str(reminder["milestoneDate"])),
                    offset_days=int(reminder["offsetDays"]),
                    trigger_date=date.fromisoformat(str(reminder["triggerDate"])),
                    status="called" if existing_decision else "pending_routing",
                )
                self.db.add(alert)
                await self.db.flush()
                if existing_decision is not None:
                    return "existing"
                try:
                    decision = await HumanRoutingService(self.db).create_decision(
                        workspace_id=workspace_id,
                        decision_key=decision_key,
                        options={
                            "purpose": "finance_tax_reform_scheduled_review",
                            "milestoneKey": reminder["milestoneKey"],
                            "milestoneDate": reminder["milestoneDate"],
                            "alertWindow": reminder["label"],
                            "catalogVersion": reminder["catalogVersion"],
                            "taxProfileVersion": reminder["profileVersion"],
                            "profileConfigured": reminder["profileConfigured"],
                            "sourceUrl": reminder["sourceUrl"],
                            "summary": reminder["summary"],
                            "allowedDecisions": [
                                "approve",
                                "request_changes",
                                "reject",
                            ],
                        },
                        required_roles=["accounting", "finance_manager"],
                        required_skills=["tributario"],
                        sla_minutes=24 * 60,
                        max_hops=4,
                    )
                except HumanRoutingError as error:
                    # Record a terminal, visible state rather than leaving a
                    # phantom wait or retrying a malformed organisation daily.
                    alert.status = "blocked"
                    alert.failure_code = safe_error_type(error)
                    self.db.add(
                        KlmAuditEvent(
                            workspace_id=workspace_id,
                            actor_type="system",
                            actor_label="Agendador da reforma tributária",
                            event_type="finance.tax_reform.alert_blocked",
                            entity_type="finance_tax_reform_alert",
                            entity_id=alert.id,
                            correlation_id=schedule_key,
                            summary=(
                                "Alerta tributário não criou espera humana: "
                                "a hierarquia não pôde ser roteada."
                            ),
                            after_data={
                                "status": "blocked",
                                "errorCode": alert.failure_code,
                                "catalogVersion": reminder["catalogVersion"],
                                "profileVersion": reminder["profileVersion"],
                            },
                        )
                    )
                    return "blocked"
                alert.human_decision_id = decision.id
                alert.status = "called"
                self.db.add(
                    KlmAuditEvent(
                        workspace_id=workspace_id,
                        actor_type="system",
                        actor_label="Agendador da reforma tributária",
                        event_type="finance.tax_reform.alert_called",
                        entity_type="finance_tax_reform_alert",
                        entity_id=alert.id,
                        correlation_id=schedule_key,
                        summary=(
                            f"{reminder['label']} da reforma tributária encaminhado "
                            "ao contador e acompanhado pelo dono do escritório."
                        ),
                        after_data={
                            "status": "called",
                            "humanDecisionId": str(decision.id),
                            "catalogVersion": reminder["catalogVersion"],
                            "profileVersion": reminder["profileVersion"],
                            "milestoneDate": reminder["milestoneDate"],
                            "alertWindow": reminder["label"],
                            "telephoneDelivery": "pending_or_not_configured",
                        },
                    )
                )
                return "called"
        except IntegrityError:
            # Concurrent scheduler ticks race only at the database uniqueness
            # boundary. The winning transaction owns the sole human call.
            return "existing"

    @staticmethod
    def _decision_key(profile_id: UUID | None, reminder: dict[str, Any]) -> str:
        profile_token = str(profile_id) if profile_id else "profile-missing"
        return (
            f"finance-tax-alert:v{reminder['catalogVersion']}:"
            f"{profile_token}:p{reminder['profileVersion']}:"
            f"{reminder['milestoneKey']}:d{reminder['offsetDays']}"
        )
