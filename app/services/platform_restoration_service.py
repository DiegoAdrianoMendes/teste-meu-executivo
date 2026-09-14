"""Preview-first, conflict-safe restoration for reversible control-plane bundles."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.platform_rbac import has_platform_permission, is_platform_root
from app.core.workspace_access_packages import get_workspace_access_package
from app.models.billing import Plan, Subscription
from app.models.commercial_catalog import CommercialOffer, WorkspaceCommercialContract
from app.models.configuration import PlatformConfigDocument, PlatformConfigDocumentVersion
from app.models.identity import UserSession
from app.models.platform_audit_event import PlatformAuditEvent
from app.models.platform_restoration import PlatformRestorationJob, PlatformRestorationPreview
from app.models.user import User, UserRole
from app.models.workspace import Workspace, WorkspaceStatus
from app.models.workspace_membership import (
    MembershipStatus,
    WorkspaceMembership,
    WorkspaceRole,
)
from app.schemas.platform_audit_event import PlatformAuditEventCreate
from app.services.configuration_service import ConfigurationService
from app.services.platform_audit_event_service import (
    PlatformAuditEventService,
    semantic_field_diff,
)


class PlatformRestorationError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 422):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


RESOURCE_PERMISSION = {
    "workspace": ("customers", "manage"),
    "staff": ("staff", "manage"),
    "user": ("customers", "manage"),
    "plan": ("plans", "manage"),
    "subscription": ("billing", "manage"),
    "platform_configuration": ("settings", "manage"),
    "customer_membership": ("customers", "manage"),
}


def _hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def _field_diff(current: dict, target: dict) -> list[dict[str, Any]]:
    # A prévia e o Audit Center compartilham a mesma semântica e a mesma
    # sanitização. Isso evita expor caminhos técnicos ou reintroduzir segredo
    # na etapa imediatamente anterior à restauração.
    return semantic_field_diff(current, target)


class PlatformRestorationService:
    TERMINAL = {"succeeded", "failed", "cancelled", "compensated"}

    def __init__(self, db: AsyncSession):
        self.db = db

    async def _canonical_contract_id(self, workspace_id: UUID) -> UUID | None:
        return await self.db.scalar(
            select(WorkspaceCommercialContract.id).where(
                WorkspaceCommercialContract.workspace_id == workspace_id
            )
        )

    async def _reject_legacy_commercial_restore(
        self,
        *,
        resource_type: str,
        resource: Any,
        current: dict[str, Any],
        target: dict[str, Any],
        ineligible: list[str],
    ) -> None:
        """Keep the generic restore adapter from splitting commercial truth.

        Canonical offers and contracts already have preview-first, versioned
        restoration endpoints.  The generic adapter may continue serving
        genuinely legacy records, but it must never write a legacy projection
        underneath an authoritative canonical aggregate.
        """

        plan_changed = current.get("planId") != target.get("planId")
        if resource_type == "workspace" and plan_changed:
            contract_id = await self._canonical_contract_id(resource.id)
            if contract_id is not None:
                ineligible.append(
                    "O plano desta conta é canônico. Restaure o contrato pelo "
                    f"Estúdio de Planos (contrato {contract_id})."
                )
        elif resource_type == "subscription" and plan_changed:
            contract_id = await self._canonical_contract_id(resource.workspace_id)
            if contract_id is not None:
                ineligible.append(
                    "A assinatura é projeção de um contrato canônico. Restaure "
                    f"o contrato {contract_id} pelo Estúdio de Planos."
                )
        elif resource_type == "plan":
            features = resource.features or {}
            canonical_sku = features.get("canonicalSku")
            if features.get("canonicalManaged") or canonical_sku:
                ineligible.append(
                    "Este plano é uma projeção canônica. Restaure a revisão da "
                    f"oferta {canonical_sku or resource.tier.value} no Estúdio de Planos."
                )
            elif await self.db.scalar(select(CommercialOffer.id).limit(1)) is not None:
                ineligible.append(
                    "O catálogo canônico está ativo. Planos legados são somente "
                    "leitura e não podem ser restaurados por este adapter."
                )

    @staticmethod
    def authorize(actor: User | None, resource_type: str) -> None:
        permission = RESOURCE_PERMISSION.get(resource_type)
        if actor is None or permission is None or not has_platform_permission(actor, *permission):
            raise PlatformRestorationError(
                "PLATFORM_RESTORE_FORBIDDEN", "Restauração não autorizada.", status_code=403
            )

    async def _event_version(self, resource_type: str, resource_id: str) -> int:
        return (
            int(
                (
                    await self.db.scalar(
                        select(func.count(PlatformAuditEvent.id)).where(
                            PlatformAuditEvent.resource_type == resource_type,
                            PlatformAuditEvent.resource_id == resource_id,
                            PlatformAuditEvent.result.in_(["success", "partial", "compensated"]),
                        )
                    )
                )
                or 0
            )
            + 1
        )

    async def _current(self, resource_type: str, resource_id: str) -> tuple[Any, dict, int]:
        if resource_type == "customer_membership":
            try:
                workspace_raw, user_raw = resource_id.split(":", 1)
                workspace_id = UUID(workspace_raw)
                user_id = UUID(user_raw)
            except (ValueError, AttributeError) as exc:
                raise PlatformRestorationError(
                    "PLATFORM_RESTORE_RESOURCE_INVALID",
                    "Vínculo de conta inválido.",
                ) from exc
            object_id = None
        else:
            try:
                object_id = UUID(resource_id)
            except ValueError as exc:
                if resource_type != "platform_configuration":
                    raise PlatformRestorationError(
                        "PLATFORM_RESTORE_RESOURCE_INVALID", "Recurso inválido."
                    ) from exc
                object_id = None
        if resource_type == "workspace":
            resource = await self.db.scalar(
                select(Workspace)
                .options(selectinload(Workspace.subscription))
                .where(Workspace.id == object_id)
                .with_for_update()
            )
            if resource is None:
                raise PlatformRestorationError(
                    "PLATFORM_RESTORE_NOT_FOUND", "Conta não encontrada.", status_code=404
                )
            snapshot = {
                "name": resource.name,
                "status": resource.status.value,
                "isActive": resource.is_active,
                "onboardingCompleted": resource.onboarding_completed,
                "planId": str(resource.subscription.plan_id) if resource.subscription else None,
            }
        elif resource_type in {"staff", "user"}:
            resource = await self.db.get(User, object_id, with_for_update=True)
            if resource is None:
                raise PlatformRestorationError(
                    "PLATFORM_RESTORE_NOT_FOUND", "Usuário não encontrado.", status_code=404
                )
            snapshot = (
                {
                    "rolePackage": resource.platform_role_package,
                    "grants": resource.platform_permission_grants,
                    "denials": resource.platform_permission_denials,
                    "platformAccessActive": resource.platform_access_active,
                    "isActive": resource.is_active,
                }
                if resource_type == "staff"
                else {
                    "role": resource.role.value,
                    "isActive": resource.is_active,
                }
            )
        elif resource_type == "plan":
            resource = await self.db.get(Plan, object_id, with_for_update=True)
            if resource is None:
                raise PlatformRestorationError(
                    "PLATFORM_RESTORE_NOT_FOUND", "Plano não encontrado.", status_code=404
                )
            snapshot = {
                "name": resource.name,
                "description": resource.description,
                "priceMonthlyCents": resource.price_monthly_cents,
                "priceAnnuallyCents": resource.price_annually_cents,
                "maxUsers": resource.max_users,
                "maxMatters": resource.max_matters,
                "maxTokensPerMonth": resource.max_tokens_per_month,
                "maxAiAgents": resource.max_ai_agents,
                "features": resource.features,
                "isActive": resource.is_active,
                "isPublic": resource.is_public,
                "sortOrder": resource.sort_order,
            }
        elif resource_type == "subscription":
            resource = await self.db.get(Subscription, object_id, with_for_update=True)
            if resource is None:
                raise PlatformRestorationError(
                    "PLATFORM_RESTORE_NOT_FOUND", "Assinatura não encontrada.", status_code=404
                )
            snapshot = {
                "planId": str(resource.plan_id),
                "trialEnd": resource.trial_end,
                "amountCents": resource.amount_cents,
                "status": resource.status.value,
            }
        elif resource_type == "platform_configuration":
            resource = await self.db.scalar(
                select(PlatformConfigDocument)
                .where(PlatformConfigDocument.namespace == resource_id)
                .with_for_update()
            )
            if resource is None:
                raise PlatformRestorationError(
                    "PLATFORM_RESTORE_NOT_FOUND", "Configuração não encontrada.", status_code=404
                )
            snapshot = {
                "namespace": resource.namespace,
                "payload": resource.payload,
                "schemaVersion": resource.schema_version,
            }
            return resource, snapshot, resource.current_version
        elif resource_type == "customer_membership":
            resource = await self.db.scalar(
                select(WorkspaceMembership)
                .where(
                    WorkspaceMembership.workspace_id == workspace_id,
                    WorkspaceMembership.user_id == user_id,
                )
                .with_for_update()
            )
            if resource is None:
                raise PlatformRestorationError(
                    "PLATFORM_RESTORE_NOT_FOUND",
                    "Vínculo de usuário com a conta não encontrado.",
                    status_code=404,
                )
            user = await self.db.get(User, user_id)
            if user is None:
                raise PlatformRestorationError(
                    "PLATFORM_RESTORE_NOT_FOUND",
                    "Usuário do vínculo não encontrado.",
                    status_code=404,
                )
            snapshot = {
                "workspaceId": str(resource.workspace_id),
                "userId": str(resource.user_id),
                "status": resource.status.value,
                "role": resource.role.value,
                "accessPackage": resource.access_package,
                "grants": resource.permission_grants or [],
                "denials": resource.permission_denials or [],
                "isDefault": resource.is_default,
                "userActive": user.is_active,
                "authEpoch": user.auth_epoch,
            }
        else:
            raise PlatformRestorationError(
                "PLATFORM_RESTORE_UNSUPPORTED",
                "Este tipo de recurso não possui adapter reversível.",
            )
        return resource, snapshot, await self._event_version(resource_type, resource_id)

    @staticmethod
    def _target_bundle(resource_type: str, snapshot: dict) -> tuple[dict, list[str]]:
        warnings: list[str] = []
        if resource_type == "workspace":
            allowed = {"name", "status", "isActive", "onboardingCompleted", "planId"}
        elif resource_type == "staff":
            allowed = {"rolePackage", "grants", "denials", "platformAccessActive", "isActive"}
        elif resource_type == "user":
            allowed = {"role", "isActive"}
        elif resource_type == "plan":
            allowed = {
                "name",
                "description",
                "priceMonthlyCents",
                "priceAnnuallyCents",
                "maxUsers",
                "maxMatters",
                "maxTokensPerMonth",
                "maxAiAgents",
                "features",
                "isActive",
                "isPublic",
                "sortOrder",
            }
        elif resource_type == "subscription":
            allowed = {"planId", "trialEnd", "amountCents"}
            if "status" in snapshot:
                warnings.append(
                    "O estado do provedor de cobrança não será restaurado; requer reconciliação."
                )
        elif resource_type == "platform_configuration":
            allowed = {"namespace", "payload", "schemaVersion"}
        elif resource_type == "customer_membership":
            allowed = {"status", "role", "accessPackage", "grants", "denials"}
        else:
            return {}, warnings
        # Accept canonical camelCase and the older snake_case audit snapshots.
        aliases = {
            "is_active": "isActive",
            "onboarding_completed": "onboardingCompleted",
            "plan_id": "planId",
            "role_package": "rolePackage",
            "platform_permission_grants": "grants",
            "platform_permission_denials": "denials",
            "platform_access_active": "platformAccessActive",
            "price_monthly_cents": "priceMonthlyCents",
            "price_annually_cents": "priceAnnuallyCents",
            "max_users": "maxUsers",
            "max_matters": "maxMatters",
            "max_tokens_per_month": "maxTokensPerMonth",
            "max_ai_agents": "maxAiAgents",
            "is_public": "isPublic",
            "sort_order": "sortOrder",
            "trial_end": "trialEnd",
            "amount_cents": "amountCents",
            "schema_version": "schemaVersion",
            "access_package": "accessPackage",
            "permission_grants": "grants",
            "permission_denials": "denials",
        }
        normalized = {aliases.get(key, key): value for key, value in snapshot.items()}
        return {key: normalized[key] for key in allowed if key in normalized}, warnings

    async def create_preview(
        self,
        *,
        actor: User,
        source_audit_event_id: UUID | None,
        resource_type: str | None,
        resource_id: str | None,
        target_version: int | None,
    ) -> PlatformRestorationPreview:
        event = (
            await self.db.get(PlatformAuditEvent, source_audit_event_id)
            if source_audit_event_id
            else None
        )
        if source_audit_event_id and event is None:
            raise PlatformRestorationError(
                "PLATFORM_RESTORE_EVENT_NOT_FOUND", "Evento não encontrado.", status_code=404
            )
        if event:
            resource_type = event.resource_type
            resource_id = event.resource_id
        if not resource_type or not resource_id:
            raise PlatformRestorationError(
                "PLATFORM_RESTORE_RESOURCE_REQUIRED", "Recurso não identificado."
            )
        self.authorize(actor, resource_type)
        resource, current, current_version = await self._current(
            resource_type,
            resource_id,
        )
        ineligible: list[str] = []
        warnings: list[str] = []
        if event:
            if event.result not in {"success", "partial", "compensated"}:
                ineligible.append("Somente mutações concluídas podem ser restauradas.")
            if not isinstance(event.before, dict):
                ineligible.append("O evento não possui snapshot anterior completo.")
                raw_target = {}
            else:
                raw_target = event.before
            target_version = event.resource_version_before or target_version
        elif resource_type == "platform_configuration" and target_version:
            document = await self.db.scalar(
                select(PlatformConfigDocument).where(
                    PlatformConfigDocument.namespace == resource_id
                )
            )
            version = (
                await self.db.scalar(
                    select(PlatformConfigDocumentVersion).where(
                        PlatformConfigDocumentVersion.document_id == document.id,
                        PlatformConfigDocumentVersion.version == target_version,
                    )
                )
                if document
                else None
            )
            if version is None:
                ineligible.append("A versão alvo não existe.")
                raw_target = {}
            else:
                raw_target = {
                    "namespace": resource_id,
                    "payload": version.payload,
                    "schemaVersion": version.schema_version,
                }
        else:
            raw_target = {}
            ineligible.append("Informe um evento auditado ou versão de configuração.")
        target_delta, adapter_warnings = self._target_bundle(resource_type, raw_target)
        target = {**current, **target_delta}
        warnings.extend(adapter_warnings)
        await self._reject_legacy_commercial_restore(
            resource_type=resource_type,
            resource=resource,
            current=current,
            target=target,
            ineligible=ineligible,
        )
        if not target_delta:
            ineligible.append("Nenhum campo reversível foi encontrado no snapshot.")
        if resource_type == "staff":
            target_user = await self.db.get(User, UUID(resource_id))
            if target_user and target_user.platform_role_package == "platform_owner":
                ineligible.append("O titular da plataforma nunca pode ser restaurado pelo painel.")
            if target.get("rolePackage") == "platform_owner":
                ineligible.append("Nenhuma restauração pode conceder autoridade root.")
            if target.get("rolePackage") == "platform_admin" and not is_platform_root(actor):
                ineligible.append("Somente o titular pode restaurar autoridade de platform_admin.")
            if actor.id == UUID(resource_id) and target.get("rolePackage") != current.get(
                "rolePackage"
            ):
                ineligible.append(
                    "Funcionário não pode alterar a própria autoridade por restauração."
                )
        elif resource_type == "customer_membership":
            workspace_id = UUID(str(target.get("workspaceId")))
            user_id = UUID(str(target.get("userId")))
            package_key = target.get("accessPackage")
            package = get_workspace_access_package(str(package_key)) if package_key else None
            if package_key and package is None:
                ineligible.append("O pacote de acesso alvo não existe mais.")
            if package and str(target.get("role")) != package.workspace_role.value:
                ineligible.append("O papel alvo diverge do pacote de acesso selecionado.")
            loses_owner = (
                current.get("role") == WorkspaceRole.workspace_owner.value
                and current.get("status") == MembershipStatus.active.value
                and (
                    target.get("role") != WorkspaceRole.workspace_owner.value
                    or target.get("status") != MembershipStatus.active.value
                )
            )
            if loses_owner:
                other_owner_count = int(
                    (
                        await self.db.scalar(
                            select(func.count(WorkspaceMembership.id)).where(
                                WorkspaceMembership.workspace_id == workspace_id,
                                WorkspaceMembership.user_id != user_id,
                                WorkspaceMembership.role == WorkspaceRole.workspace_owner,
                                WorkspaceMembership.status == MembershipStatus.active,
                            )
                        )
                    )
                    or 0
                )
                if other_owner_count == 0:
                    ineligible.append(
                        "A restauração deixaria a conta sem titular ativo; transfira a titularidade primeiro."  # noqa: E501
                    )
        preview = PlatformRestorationPreview(
            actor_user_id=actor.id,
            source_audit_event_id=source_audit_event_id,
            resource_type=resource_type,
            resource_id=resource_id,
            current_version=current_version,
            current_hash=_hash(current),
            target_version=target_version,
            target_hash=_hash(target),
            target_snapshot=target,
            diff=_field_diff(current, target),
            impact={
                "changedFields": len(_field_diff(current, target)),
                "sessionsWillBeRevoked": resource_type in {"staff", "user", "customer_membership"},
                "externalEffects": False,
            },
            warnings=warnings,
            eligible=not ineligible and current != target,
            ineligible_reasons=ineligible
            + (["O recurso já está no estado alvo."] if current == target else []),
            expires_at=datetime.now(UTC) + timedelta(minutes=15),
        )
        self.db.add(preview)
        await self.db.flush()
        return preview

    async def create_job(
        self,
        *,
        actor: User,
        preview_id: UUID,
        expected_resource_version: int,
        reason: str,
        confirmation: str,
        idempotency_key: str,
    ) -> PlatformRestorationJob:
        existing = await self.db.scalar(
            select(PlatformRestorationJob).where(
                PlatformRestorationJob.actor_user_id == actor.id,
                PlatformRestorationJob.idempotency_key == idempotency_key,
            )
        )
        if existing:
            existing._created_for_request = False
            return existing
        preview = await self.db.scalar(
            select(PlatformRestorationPreview)
            .where(PlatformRestorationPreview.id == preview_id)
            .with_for_update()
        )
        if preview is None:
            raise PlatformRestorationError(
                "PLATFORM_RESTORE_PREVIEW_NOT_FOUND", "Prévia não encontrada.", status_code=404
            )
        self.authorize(actor, preview.resource_type)
        expires = (
            preview.expires_at
            if preview.expires_at.tzinfo
            else preview.expires_at.replace(tzinfo=UTC)
        )
        if preview.actor_user_id != actor.id and not is_platform_root(actor):
            raise PlatformRestorationError(
                "PLATFORM_RESTORE_FORBIDDEN", "Prévia pertence a outro operador.", status_code=403
            )
        if preview.consumed_at or expires <= datetime.now(UTC):
            raise PlatformRestorationError(
                "PLATFORM_RESTORE_PREVIEW_STALE", "Prévia expirada ou consumida.", status_code=409
            )
        if not preview.eligible:
            raise PlatformRestorationError("PLATFORM_RESTORE_INELIGIBLE", "Restauração inelegível.")
        if expected_resource_version != preview.current_version:
            raise PlatformRestorationError(
                "PLATFORM_RESTORE_PREVIEW_STALE", "Versão esperada divergiu.", status_code=409
            )
        _, current, _ = await self._current(preview.resource_type, preview.resource_id)
        if _hash(current) != preview.current_hash:
            raise PlatformRestorationError(
                "PLATFORM_RESTORE_PREVIEW_STALE", "O recurso mudou após a prévia.", status_code=409
            )
        expected_phrase = f"RESTAURAR {preview.resource_id}"
        if confirmation.strip() != expected_phrase:
            raise PlatformRestorationError(
                "PLATFORM_RESTORE_CONFIRMATION_INVALID", f'Digite "{expected_phrase}".'
            )
        preview.consumed_at = datetime.now(UTC)
        job = PlatformRestorationJob(
            preview_id=preview.id,
            actor_user_id=actor.id,
            status="queued",
            reason=reason,
            idempotency_key=idempotency_key,
            correlation_id=str(uuid4()),
        )
        self.db.add(job)
        await self.db.flush()
        job._created_for_request = True
        return job

    async def get_job(self, job_id: UUID) -> PlatformRestorationJob:
        job = await self.db.get(PlatformRestorationJob, job_id)
        if job is None:
            raise PlatformRestorationError(
                "PLATFORM_RESTORE_JOB_NOT_FOUND", "Restauração não encontrada.", status_code=404
            )
        return job

    async def request_cancel(self, job_id: UUID) -> PlatformRestorationJob:
        job = await self.db.scalar(
            select(PlatformRestorationJob)
            .where(PlatformRestorationJob.id == job_id)
            .with_for_update()
        )
        if job is None:
            raise PlatformRestorationError(
                "PLATFORM_RESTORE_JOB_NOT_FOUND", "Restauração não encontrada.", status_code=404
            )
        if job.status == "queued":
            job.status = "cancelled"
            job.completed_at = datetime.now(UTC)
        elif job.status == "running":
            raise PlatformRestorationError(
                "PLATFORM_RESTORE_CANCEL_UNSAFE",
                "A aplicação atômica já iniciou e não pode ser interrompida com segurança.",
                status_code=409,
            )
        await self.db.flush()
        return job

    async def _revoke_user_sessions(self, user_id: UUID, *, reason: str) -> None:
        sessions = list(
            (
                await self.db.scalars(
                    select(UserSession)
                    .where(
                        UserSession.user_id == user_id,
                        UserSession.revoked_at.is_(None),
                    )
                    .with_for_update()
                )
            ).all()
        )
        now = datetime.now(UTC)
        for session in sessions:
            session.revoked_at = now
            session.revocation_reason = reason

    async def _apply(self, actor: User, preview: PlatformRestorationPreview, resource: Any) -> None:
        target = preview.target_snapshot
        changed = {
            str(item.get("path"))
            for item in (preview.diff or [])
            if isinstance(item, dict) and item.get("path")
        }
        if preview.resource_type == "workspace":
            if "name" in changed:
                resource.name = str(target["name"])
            if "status" in changed:
                resource.status = WorkspaceStatus(str(target["status"]))
            if "isActive" in changed:
                resource.is_active = bool(target["isActive"])
            if "onboardingCompleted" in changed:
                resource.onboarding_completed = bool(target["onboardingCompleted"])
            if "planId" in changed and target.get("planId"):
                if await self._canonical_contract_id(resource.id) is not None:
                    raise PlatformRestorationError(
                        "PLATFORM_RESTORE_CANONICAL_REQUIRED",
                        "Plano canônico deve ser restaurado pelo Estúdio de Planos.",
                        status_code=409,
                    )
                plan = await self.db.get(Plan, UUID(str(target["planId"])))
                if plan is None or not plan.is_active or resource.subscription is None:
                    raise PlatformRestorationError(
                        "PLATFORM_RESTORE_PLAN_INVALID", "Plano alvo indisponível."
                    )
                resource.subscription.plan_id = plan.id
        elif preview.resource_type == "staff":
            if (
                resource.platform_role_package == "platform_owner"
                or target.get("rolePackage") == "platform_owner"
            ):
                raise PlatformRestorationError(
                    "PLATFORM_RESTORE_ROOT_FORBIDDEN",
                    "Autoridade root não é restaurável.",
                    status_code=403,
                )
            if target.get("rolePackage") == "platform_admin" and not is_platform_root(actor):
                raise PlatformRestorationError(
                    "PLATFORM_RESTORE_ADMIN_FORBIDDEN",
                    "Somente o titular altera platform_admin.",
                    status_code=403,
                )
            if (
                actor.id == resource.id
                and target.get("rolePackage") != resource.platform_role_package
            ):
                raise PlatformRestorationError(
                    "PLATFORM_RESTORE_SELF_AUTHORITY",
                    "Autoridade própria não pode ser restaurada.",
                    status_code=403,
                )
            for key, attribute in {
                "rolePackage": "platform_role_package",
                "grants": "platform_permission_grants",
                "denials": "platform_permission_denials",
                "platformAccessActive": "platform_access_active",
                "isActive": "is_active",
            }.items():
                if key in target:
                    setattr(resource, attribute, target[key])
            resource.auth_epoch += 1
            await self._revoke_user_sessions(
                resource.id,
                reason="platform_staff_authority_restored",
            )
        elif preview.resource_type == "user":
            if "role" in target:
                resource.role = UserRole(str(target["role"]))
            if "isActive" in target:
                resource.is_active = bool(target["isActive"])
            resource.auth_epoch += 1
            await self._revoke_user_sessions(
                resource.id,
                reason="platform_user_authority_restored",
            )
        elif preview.resource_type == "plan":
            if (resource.features or {}).get("canonicalManaged") or await self.db.scalar(
                select(CommercialOffer.id).limit(1)
            ) is not None:
                raise PlatformRestorationError(
                    "PLATFORM_RESTORE_CANONICAL_REQUIRED",
                    "Oferta canônica deve ser restaurada pelo Estúdio de Planos.",
                    status_code=409,
                )
            mapping = {
                "name": "name",
                "description": "description",
                "priceMonthlyCents": "price_monthly_cents",
                "priceAnnuallyCents": "price_annually_cents",
                "maxUsers": "max_users",
                "maxMatters": "max_matters",
                "maxTokensPerMonth": "max_tokens_per_month",
                "maxAiAgents": "max_ai_agents",
                "features": "features",
                "isActive": "is_active",
                "isPublic": "is_public",
                "sortOrder": "sort_order",
            }
            for key, attribute in mapping.items():
                if key in target:
                    setattr(resource, attribute, target[key])
        elif preview.resource_type == "subscription":
            if await self._canonical_contract_id(resource.workspace_id) is not None:
                raise PlatformRestorationError(
                    "PLATFORM_RESTORE_CANONICAL_REQUIRED",
                    "Assinatura canônica deve ser restaurada pelo Estúdio de Planos.",
                    status_code=409,
                )
            if target.get("planId"):
                plan = await self.db.get(Plan, UUID(str(target["planId"])))
                if plan is None or not plan.is_active:
                    raise PlatformRestorationError(
                        "PLATFORM_RESTORE_PLAN_INVALID", "Plano alvo indisponível."
                    )
                resource.plan_id = plan.id
            if "trialEnd" in target:
                trial_end = target["trialEnd"]
                if isinstance(trial_end, str):
                    trial_end = datetime.fromisoformat(trial_end.replace("Z", "+00:00"))
                resource.trial_end = trial_end
            if "amountCents" in target:
                resource.amount_cents = int(target["amountCents"])
            resource.notes = (
                (resource.notes or "")
                + "\nRestauração local pendente de reconciliação com o provedor."
            ).strip()
        elif preview.resource_type == "platform_configuration":
            await ConfigurationService(self.db).write_platform(
                namespace=resource.namespace,
                payload=target["payload"],
                actor_user_id=actor.id,
                schema_version=target.get("schemaVersion", "1.0"),
                expected_etag=resource.etag,
                reason="Contextual restoration",
            )
        elif preview.resource_type == "customer_membership":
            package_key = target.get("accessPackage")
            package = get_workspace_access_package(str(package_key)) if package_key else None
            if package_key and package is None:
                raise PlatformRestorationError(
                    "PLATFORM_RESTORE_ACCESS_PACKAGE_INVALID",
                    "Pacote de acesso alvo indisponível.",
                )
            if package and str(target.get("role")) != package.workspace_role.value:
                raise PlatformRestorationError(
                    "PLATFORM_RESTORE_ACCESS_PACKAGE_CONFLICT",
                    "Papel e pacote de acesso alvo são incompatíveis.",
                )
            next_role = WorkspaceRole(str(target["role"])) if "role" in target else resource.role
            next_status = (
                MembershipStatus(str(target["status"])) if "status" in target else resource.status
            )
            if (
                resource.role == WorkspaceRole.workspace_owner
                and resource.status == MembershipStatus.active
                and (
                    next_role != WorkspaceRole.workspace_owner
                    or next_status != MembershipStatus.active
                )
            ):
                other_owner_count = int(
                    (
                        await self.db.scalar(
                            select(func.count(WorkspaceMembership.id)).where(
                                WorkspaceMembership.workspace_id == resource.workspace_id,
                                WorkspaceMembership.user_id != resource.user_id,
                                WorkspaceMembership.role == WorkspaceRole.workspace_owner,
                                WorkspaceMembership.status == MembershipStatus.active,
                            )
                        )
                    )
                    or 0
                )
                if other_owner_count == 0:
                    raise PlatformRestorationError(
                        "PLATFORM_RESTORE_LAST_OWNER",
                        "A conta precisa manter ao menos um titular ativo.",
                    )
            resource.role = next_role
            resource.status = next_status
            if "accessPackage" in target:
                resource.access_package = target["accessPackage"]
            if "grants" in target:
                resource.permission_grants = list(target["grants"] or [])
            if "denials" in target:
                resource.permission_denials = list(target["denials"] or [])
            user = await self.db.get(User, resource.user_id, with_for_update=True)
            if user is None:
                raise PlatformRestorationError(
                    "PLATFORM_RESTORE_NOT_FOUND",
                    "Usuário do vínculo não encontrado.",
                    status_code=404,
                )
            user.auth_epoch += 1
            sessions = list(
                (
                    await self.db.scalars(
                        select(UserSession)
                        .where(
                            UserSession.user_id == resource.user_id,
                            UserSession.last_workspace_id == resource.workspace_id,
                            UserSession.revoked_at.is_(None),
                        )
                        .with_for_update()
                    )
                ).all()
            )
            now = datetime.now(UTC)
            for session in sessions:
                session.revoked_at = now
                session.revocation_reason = "platform_membership_restored"
        await self.db.flush()

    async def run_job(self, job_id: UUID) -> PlatformRestorationJob:
        job = await self.get_job(job_id)
        if job.status in self.TERMINAL:
            return job
        job.status = "running"
        job.started_at = datetime.now(UTC)
        await self.db.commit()
        try:
            preview = await self.db.get(PlatformRestorationPreview, job.preview_id)
            actor = await self.db.get(User, job.actor_user_id)
            if preview is None or actor is None:
                raise PlatformRestorationError(
                    "PLATFORM_RESTORE_CONTEXT_MISSING", "Contexto da restauração indisponível."
                )
            self.authorize(actor, preview.resource_type)
            resource, before, before_version = await self._current(
                preview.resource_type, preview.resource_id
            )
            if _hash(before) != preview.current_hash:
                raise PlatformRestorationError(
                    "PLATFORM_RESTORE_PREVIEW_STALE",
                    "O recurso mudou após a prévia.",
                    status_code=409,
                )
            await self._apply(actor, preview, resource)
            _, after, after_version = await self._current(
                preview.resource_type, preview.resource_id
            )
            if preview.resource_type != "platform_configuration":
                after_version = before_version + 1
            await PlatformAuditEventService(self.db).record(
                PlatformAuditEventCreate(
                    actor_id=actor.id,
                    actor_role_snapshot=actor.platform_role_package or "unknown",
                    is_platform_root=is_platform_root(actor),
                    session_type="normal",
                    workspace_id=(
                        UUID(preview.resource_id)
                        if preview.resource_type == "workspace"
                        else UUID(preview.resource_id.split(":", 1)[0])
                        if preview.resource_type == "customer_membership"
                        else None
                    ),
                    module=RESOURCE_PERMISSION[preview.resource_type][0],
                    action="restored",
                    resource_type=preview.resource_type,
                    resource_id=preview.resource_id,
                    resource_label_snapshot=preview.resource_id,
                    result="success",
                    before=before,
                    after=after,
                    reason=job.reason,
                    reason_source="user",
                    correlation_id=job.correlation_id,
                    operation_id=str(uuid4()),
                    restoration_of_event_id=preview.source_audit_event_id,
                    resource_version_before=before_version,
                    resource_version_after=after_version,
                    route="platform_restoration_tasks.run",
                    method="WORKER",
                )
            )
            job.status = "succeeded"
            job.completed_at = datetime.now(UTC)
            await self.db.commit()
        except Exception as exc:
            await self.db.rollback()
            job = await self.get_job(job_id)
            job.status = "failed"
            job.error_code = getattr(exc, "code", type(exc).__name__)[:120]
            job.error_detail = str(exc)[:500]
            job.completed_at = datetime.now(UTC)
            await self.db.commit()
        return job
