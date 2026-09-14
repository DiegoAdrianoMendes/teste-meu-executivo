"""Pure/runtime helpers for the agent platform V3 contracts."""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.workspace_access_packages import WORKSPACE_ACCESS_PACKAGES
from app.models.agent_platform import (
    AgentDeployment,
    DeploymentStatus,
    HumanDecision,
    HumanDecisionStatus,
    HumanDeliveryStatus,
    HumanNotificationDelivery,
)
from app.models.klm_operations import KlmAuditEvent
from app.models.notification import NotificationType
from app.models.omnichannel_v6 import WorkspaceTeam, WorkspaceTeamMember
from app.models.user import User
from app.models.workspace import Workspace
from app.models.workspace_membership import (
    MembershipStatus,
    WorkspaceMembership,
    WorkspaceRole,
)
from app.services.notification_service import NotificationService
from app.services.staff_availability_service import (
    AvailabilityDomainError,
    StaffAvailabilityService,
)


def validate_node_payload(schema: dict[str, Any], payload: Any) -> None:
    """Small fail-closed JSON-schema validator for runtime gates."""
    if schema.get("type") == "object" and not isinstance(payload, dict):
        raise ValueError("node payload must be an object")
    for key in schema.get("required", []):
        if not isinstance(payload, dict) or key not in payload:
            raise ValueError(f"missing required node field: {key}")
    for key, spec in schema.get("properties", {}).items():
        if not isinstance(payload, dict) or key not in payload:
            continue
        value = payload[key]
        typ = spec.get("type")
        if typ == "string" and not isinstance(value, str):
            raise ValueError(f"{key} must be string")
        if typ == "boolean" and not isinstance(value, bool):
            raise ValueError(f"{key} must be boolean")
        if typ == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
            raise ValueError(f"{key} must be integer")
        if "enum" in spec and value not in spec["enum"]:
            raise ValueError(f"{key} has invalid value")


def runtime_gate(
    gates: dict[str, Any], *, dry_run: bool = False, human_approved: bool = False
) -> None:
    if gates.get("require_human") and not human_approved:
        raise PermissionError("human approval required")
    if gates.get("external_side_effect") and dry_run:
        return
    if gates.get("blocked"):
        raise PermissionError("runtime gate blocked execution")


def _as_utc(value: datetime) -> datetime:
    """Normalize datetimes returned without tzinfo by SQLite test/runtime adapters."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


async def consume_human_decision(
    db: AsyncSession,
    decision_id: Any,
    *,
    workspace_id: Any,
    user_id: Any,
    choice: dict[str, Any],
) -> HumanDecision | None:
    """Lock and consume one tenant-scoped pending decision.

    The caller owns the transaction so a linked flow can be resumed in the
    same commit. Returning ``None`` is the replay/conflict signal.
    """
    now = datetime.now(UTC)
    decision = await db.scalar(
        select(HumanDecision)
        .where(
            HumanDecision.id == decision_id,
            HumanDecision.workspace_id == workspace_id,
        )
        .with_for_update()
    )
    if decision is None or decision.status != HumanDecisionStatus.pending:
        return None
    if decision.expires_at is not None and _as_utc(decision.expires_at) <= now:
        decision.status = HumanDecisionStatus.expired
        db.add(
            KlmAuditEvent(
                workspace_id=workspace_id,
                actor_type="system",
                actor_label="Human decision runtime",
                event_type="human_decision_expired",
                entity_type="human_decision",
                entity_id=decision.id,
                summary="Intervenção humana expirou antes de receber uma decisão.",
                correlation_id=_decision_correlation(decision.decision_key),
                after_data={"status": "expired", "reason": "decision_expired"},
            )
        )
        # The caller owns the transaction.  Expiry and any linked flow state
        # must be committed atomically, never from inside this helper.
        await db.flush()
        return None
    decision.status = HumanDecisionStatus.decided
    decision.decision = choice
    decision.decided_by_user_id = user_id
    decision.decided_at = now
    await db.flush()
    return decision


def should_escalate(*, created_at: datetime, now: datetime, sla_seconds: int) -> bool:
    return (now - created_at).total_seconds() >= sla_seconds


def finite_routing_path(candidates: list[Any], *, owner: Any, max_hops: int = 4) -> list[Any]:
    """Return a finite, cycle-free route ending at workspace owner."""
    max_hops = max(1, max_hops)
    path = []
    seen = set()
    for candidate in candidates:
        if candidate is None or candidate == owner or candidate in seen:
            continue
        if len(path) >= max_hops - (1 if owner is not None else 0):
            break
        seen.add(candidate)
        path.append(candidate)
    if owner is not None:
        path.append(owner)
    return path


def _decision_correlation(decision_key: str) -> str:
    """Stable audit correlation without persisting caller-controlled text."""
    return f"human:{sha256(decision_key.encode()).hexdigest()[:24]}"


def _usable_availability_resolution(resolution: Any) -> Any | None:
    """Return a genuinely available human from an availability resolution.

    ``StaffAvailabilityService`` intentionally returns no effective user for
    cycles, malformed redirects, exhausted hops, unavailable staff and team
    redirects.  Keep this guard close to the human-routing boundary so a
    future availability adapter cannot accidentally turn those terminal
    outcomes into an assignment merely by carrying an ID in its payload.
    """
    if getattr(resolution, "reason", None) not in {
        "responsible_available",
        "redirected_to_available_user",
    }:
        return None
    return getattr(resolution, "effective_user_id", None)


class HumanRoutingError(ValueError):
    pass


_WORKSPACE_ROLE_ESCALATION_RANK = {
    WorkspaceRole.viewer: 10,
    WorkspaceRole.operator: 20,
    WorkspaceRole.legal_manager: 30,
    WorkspaceRole.finance_manager: 30,
    WorkspaceRole.workspace_admin: 40,
    WorkspaceRole.workspace_owner: 50,
}

_HUMAN_SKILL_CATALOG = {
    "cobranca",
    "comercial",
    "contabilidade",
    "financeiro",
    "juridico",
    "onboarding",
    "revisao",
    "seguranca",
    "suporte",
    "tecnologia",
    "tributario",
}

HUMAN_ROUTING_POLICY_VERSION = "human-routing-policies-2026-08-04.1"

# Canonical deployment policies.  These are operational contracts, not UI
# suggestions: deployments may reference only one of these stable keys.
HUMAN_ROUTING_POLICIES: dict[str, dict[str, Any]] = {
    "role_skill_hierarchy_v1": {
        "label": "Função e competência → gestor → dono",
        "description": "Prioriza a função do deployment e encerra no dono do escritório.",
        "required_roles": [],
        "required_skills": [],
        "max_hops": 4,
    },
    "finance_review_v1": {
        "label": "Financeiro → gestor financeiro → dono",
        "description": "Encaminha revisão financeira pela hierarquia finita.",
        "required_roles": ["finance_manager", "workspace_admin"],
        "required_skills": ["financeiro"],
        "max_hops": 4,
    },
    "legal_review_v1": {
        "label": "Jurídico → gestor jurídico → dono",
        "description": "Encaminha revisão jurídica pela hierarquia finita.",
        "required_roles": ["legal_manager", "workspace_admin"],
        "required_skills": ["juridico", "revisao"],
        "max_hops": 4,
    },
    "support_escalation_v1": {
        "label": "Suporte → administração → dono",
        "description": "Encaminha suporte para operação e administração antes do dono.",
        "required_roles": ["operator", "workspace_admin"],
        "required_skills": ["suporte", "tecnologia"],
        "max_hops": 4,
    },
}


def human_routing_policy(key: Any) -> dict[str, Any]:
    normalized = str(key or "").strip()
    policy = HUMAN_ROUTING_POLICIES.get(normalized)
    if policy is None:
        raise ValueError("humanRoutingPolicy is not present in the published policy catalog")
    return {"key": normalized, "version": HUMAN_ROUTING_POLICY_VERSION, **policy}


def routing_policy_catalog(routing_catalog: dict[str, Any]) -> dict[str, Any]:
    """Combine versioned policies with the live workspace routing inventory."""
    available_roles = {item["key"] for item in routing_catalog.get("roles", [])}
    available_skills = {item["key"] for item in routing_catalog.get("skills", [])}
    owner_available = routing_catalog.get("ownerFallback", {}).get("available") is True
    policies = []
    for key in HUMAN_ROUTING_POLICIES:
        policy = human_routing_policy(key)
        matched_roles = [item for item in policy["required_roles"] if item in available_roles]
        matched_skills = [item for item in policy["required_skills"] if item in available_skills]
        specialist_available = bool(matched_roles or matched_skills)
        policies.append(
            {
                **policy,
                "available": bool(owner_available),
                "specialistAvailable": specialist_available,
                "matchedRoles": matched_roles,
                "matchedSkills": matched_skills,
                "fallbackMode": "owner" if not specialist_available else "finite_hierarchy",
            }
        )
    return {"version": HUMAN_ROUTING_POLICY_VERSION, "policies": policies}


def local_handoff_dry_run(
    policy: dict[str, Any],
    published_policy: dict[str, Any],
    live_routing: dict[str, Any],
) -> dict[str, Any]:
    """Prove a callable local handoff without creating a wait or side effect."""
    selected_lane = (
        published_policy["matchedRoles"][0]
        if published_policy["matchedRoles"]
        else published_policy["matchedSkills"][0]
        if published_policy["matchedSkills"]
        else WorkspaceRole.workspace_owner.value
    )
    return {
        "kind": "local_human_handoff_dry_run",
        "policyKey": policy["key"],
        "policyVersion": policy["version"],
        "selectedLane": selected_lane,
        "fallbackMode": published_policy["fallbackMode"],
        "ownerFallbackVerified": live_routing["ownerFallback"]["available"] is True,
        "decisionCreated": False,
        "notificationCreated": False,
        "externalEffect": False,
    }


def _membership_routing_rank(
    membership: WorkspaceMembership,
    required_roles: list[str],
) -> tuple[int, int, str]:
    """Order specialists first and then move upward through real authority.

    ``required_roles`` is an ordered functional route supplied by the node.
    The workspace role is the deterministic hierarchy fallback.  The owner is
    appended separately by ``finite_routing_path`` and can never be skipped.
    """
    role = (
        membership.role.value
        if isinstance(membership.role, WorkspaceRole)
        else str(membership.role)
    )
    package = (membership.access_package or "").strip()
    requested_rank = next(
        (index for index, value in enumerate(required_roles) if value in {role, package}),
        len(required_roles),
    )
    authority_rank = _WORKSPACE_ROLE_ESCALATION_RANK.get(membership.role, 20)
    return requested_rank, authority_rank, str(membership.user_id)


def validate_deployment_ready(config: dict[str, Any], gates: dict[str, Any]) -> None:
    required = [
        "definitionVersion",
        "rolePackage",
        "model",
        "tools",
        "knowledgeSources",
        "autonomyPolicy",
        "humanRoutingPolicy",
        "budgetPolicy",
        "testedAt",
    ]
    missing = [key for key in required if not config.get(key)]
    if missing:
        raise ValueError(f"deployment is missing required configuration: {', '.join(missing)}")
    if not isinstance(config.get("definitionVersion"), int) or config["definitionVersion"] < 1:
        raise ValueError("definitionVersion must be a positive integer")
    if not isinstance(config["tools"], list) or not config["tools"]:
        raise ValueError("deployment requires at least one allowlisted tool")
    if not isinstance(config["knowledgeSources"], list):
        raise ValueError("knowledgeSources must be a list")
    required_gates = [
        "definitionVerified",
        "roleVerified",
        "modelVerified",
        "knowledgeVerified",
        "autonomyVerified",
        "policyVerified",
        "entitlementVerified",
        "toolsVerified",
        "humanQueueVerified",
        "budgetVerified",
        "testPassed",
    ]
    failed = [key for key in required_gates if gates.get(key) is not True]
    if failed:
        raise ValueError(f"deployment gates are not approved: {', '.join(failed)}")


def validate_deployment_configuration(config: dict[str, Any]) -> None:
    required = [
        "rolePackage",
        "model",
        "tools",
        "knowledgeSources",
        "autonomyPolicy",
        "humanRoutingPolicy",
        "budgetPolicy",
    ]
    missing = [key for key in required if not config.get(key)]
    if missing:
        raise ValueError(f"deployment is missing required configuration: {', '.join(missing)}")
    role_package = str(config.get("rolePackage") or "").strip()
    if role_package not in WORKSPACE_ACCESS_PACKAGES:
        raise ValueError("deployment role package is not recognized")
    if not isinstance(config["tools"], list) or not config["tools"]:
        raise ValueError("deployment requires at least one allowlisted tool")
    if not isinstance(config["knowledgeSources"], list):
        raise ValueError("knowledgeSources must be a list")
    if not isinstance(config.get("autonomyPolicy"), str) or not config["autonomyPolicy"].strip():
        raise ValueError("autonomyPolicy must be a non-empty policy identifier")
    if (
        not isinstance(config.get("humanRoutingPolicy"), str)
        or not config["humanRoutingPolicy"].strip()
    ):
        raise ValueError("humanRoutingPolicy must be a non-empty policy identifier")
    human_routing_policy(config["humanRoutingPolicy"])
    budget = config.get("budgetPolicy")
    if (
        not isinstance(budget, dict)
        or not isinstance(budget.get("monthlyTokenLimit"), int)
        or budget["monthlyTokenLimit"] <= 0
    ):
        raise ValueError("budgetPolicy.monthlyTokenLimit must be a positive integer")


async def verify_deployment(db: AsyncSession, deployment: Any) -> dict[str, Any]:
    """Compute gates from authoritative workspace state, never client checkboxes."""
    from sqlalchemy import func

    from app.models.entitlement import EntitlementKey
    from app.services.entitlement_service import EntitlementEnforcementService
    from app.services.klm_agent_service import KlmAgentService

    config = dict(deployment.config or {})
    validate_deployment_configuration(config)
    if config["rolePackage"] != deployment.role_package:
        raise ValueError("deployment role package diverges from its typed binding")
    registry = await KlmAgentService(db).registry(deployment.workspace_id)
    candidates = [
        *registry.get("agents", []),
        *registry.get("specializedAgents", []),
        *registry.get("customAgents", []),
    ]
    definition = next(
        (item for item in candidates if item.get("key") == deployment.agent_key), None
    )
    if definition is None:
        raise ValueError("agent key is not present in the authoritative registry")
    definition_version = int(definition.get("version") or 1)
    if int(config.get("definitionVersion") or 0) != definition_version:
        raise ValueError(
            f"agent definition version mismatch: requested {config.get('definitionVersion')}, "
            f"published {definition_version}"
        )
    allowed_models = {
        str(item).strip() for item in (definition.get("allowedModels") or []) if str(item).strip()
    }
    requested_model = str(config.get("model") or "").strip()
    if not allowed_models or requested_model not in allowed_models:
        raise ValueError("deployment model is not allowlisted by the agent definition")
    allowed_autonomy = {"A0", "A1", "A2", "A3", "A4"}
    requested_autonomy = str(config.get("autonomyPolicy") or "").strip()
    if requested_autonomy not in allowed_autonomy:
        raise ValueError("deployment autonomy policy is not recognized")
    requested_tools = set(config.get("tools") or [])
    allowed_tools = set(definition.get("tools") or [])
    unauthorized = sorted(requested_tools - allowed_tools)
    if unauthorized:
        raise ValueError(
            "deployment contains tools outside the agent allowlist: " + ", ".join(unauthorized)
        )
    policy = human_routing_policy(config["humanRoutingPolicy"])
    await EntitlementEnforcementService(db).enforce_stock(
        workspace_id=deployment.workspace_id,
        entitlement=EntitlementKey.agents,
        load_current_quantity=lambda: db.scalar(
            select(func.count(AgentDeployment.id)).where(
                AgentDeployment.workspace_id == deployment.workspace_id,
                AgentDeployment.status == DeploymentStatus.active,
            )
        ),
        requested_quantity=1,
    )
    owner = await db.scalar(
        select(WorkspaceMembership.user_id)
        .where(
            WorkspaceMembership.workspace_id == deployment.workspace_id,
            WorkspaceMembership.status == MembershipStatus.active,
            WorkspaceMembership.role == WorkspaceRole.workspace_owner,
        )
        .limit(1)
    )
    if owner is None:
        raise ValueError("workspace has no active owner for the mandatory human fallback")
    live_routing = await HumanRoutingService(db).routing_catalog(
        workspace_id=deployment.workspace_id
    )
    published_policies = routing_policy_catalog(live_routing)
    published_policy = next(
        item for item in published_policies["policies"] if item["key"] == policy["key"]
    )
    if not published_policy["available"]:
        raise ValueError("human routing policy has no callable terminal owner")

    # Local handoff dry-run: prove that the selected policy resolves to a real
    # live specialist lane or to the explicit owner fallback.  It deliberately
    # creates no HumanDecision, notification, phone call or external effect.
    dry_run = local_handoff_dry_run(policy, published_policy, live_routing)
    budget_verified = (
        isinstance(config.get("budgetPolicy"), dict)
        and isinstance(config["budgetPolicy"].get("monthlyTokenLimit"), int)
        and config["budgetPolicy"]["monthlyTokenLimit"] > 0
    )
    queue_verified = bool(dry_run["ownerFallbackVerified"] and dry_run["selectedLane"])
    test_passed = bool(queue_verified and dry_run["externalEffect"] is False)
    now = datetime.now(UTC).isoformat()
    return {
        "definitionVerified": int(config["definitionVersion"]) == definition_version,
        "roleVerified": config["rolePackage"] in WORKSPACE_ACCESS_PACKAGES,
        "modelVerified": requested_model in allowed_models,
        "knowledgeVerified": isinstance(config.get("knowledgeSources"), list),
        "autonomyVerified": requested_autonomy in allowed_autonomy,
        "policyVerified": policy["version"] == HUMAN_ROUTING_POLICY_VERSION,
        "entitlementVerified": True,
        "toolsVerified": True,
        "humanQueueVerified": queue_verified,
        "budgetVerified": budget_verified,
        "testPassed": test_passed,
        "testKind": dry_run["kind"],
        "testEvidence": dry_run,
        "externalModelCalled": False,
        "verifiedAt": now,
        "registryPolicyVersion": registry.get("policyVersion"),
        "humanRoutingPolicyVersion": policy["version"],
    }


class HumanRoutingService:
    """Assign required humans deterministically and never create an uncalled wait."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def _resolve_routable_user(
        self,
        *,
        workspace_id: Any,
        candidate_user_id: Any,
        owner_user_id: Any,
        max_hops: int = 4,
    ) -> Any | None:
        """Resolve the same availability/redirect contract used for a call.

        The owner is the explicit terminal fallback and is intentionally
        callable even if their own availability profile is stale.  Every other
        candidate must produce a concrete available recipient; a malformed
        redirect or an availability error is not a routable option.
        """
        if candidate_user_id == owner_user_id:
            return owner_user_id
        try:
            resolution = await StaffAvailabilityService(self.db).resolve_responsibility(
                workspace_id=workspace_id,
                responsible_user_id=candidate_user_id,
                max_hops=max_hops,
            )
        except AvailabilityDomainError:
            return None
        return _usable_availability_resolution(resolution)

    async def routing_catalog(self, *, workspace_id: Any) -> dict[str, Any]:
        """Return only routes which can reach an active human now.

        This is deliberately derived from authoritative memberships and teams,
        rather than the broad static skill vocabulary accepted by workflow
        definitions.  An operator must never be able to type a plausible but
        unstaffed specialty and create a wait that has no intended recipient.
        The owner is always exposed as the finite, explicit last resort.
        """

        def catalog_slug(value: Any) -> str:
            normalized = unicodedata.normalize("NFKD", str(value or ""))
            token = "".join(char for char in normalized if not unicodedata.combining(char))
            token = token.strip().casefold().replace(" ", "_")
            return re.sub(r"[^a-z0-9_-]", "", token) or "team"

        memberships = list(
            (
                await self.db.scalars(
                    select(WorkspaceMembership)
                    .join(User, User.id == WorkspaceMembership.user_id)
                    .where(
                        WorkspaceMembership.workspace_id == workspace_id,
                        WorkspaceMembership.status == MembershipStatus.active,
                        User.is_active.is_(True),
                        User.deleted_at.is_(None),
                        User.agent_type == "human",
                    )
                )
            ).all()
        )
        owner = next(
            (
                membership
                for membership in memberships
                if membership.role == WorkspaceRole.workspace_owner
            ),
            None,
        )
        if owner is None:
            raise HumanRoutingError("workspace has no active owner to terminate escalation")

        active_member_ids = {membership.user_id for membership in memberships}
        role_members: dict[str, set[Any]] = {}
        for membership in memberships:
            values = {
                membership.role.value
                if isinstance(membership.role, WorkspaceRole)
                else str(membership.role),
                (membership.access_package or "").strip(),
            }
            for value in values:
                if value:
                    role_members.setdefault(value, set()).add(membership.user_id)

        team_rows = (
            await self.db.execute(
                select(
                    WorkspaceTeam.slug,
                    WorkspaceTeam.name,
                    WorkspaceTeam.team_type,
                    WorkspaceTeamMember.user_id,
                    WorkspaceTeamMember.is_available,
                )
                .join(WorkspaceTeam, WorkspaceTeam.id == WorkspaceTeamMember.team_id)
                .where(
                    WorkspaceTeam.workspace_id == workspace_id,
                    WorkspaceTeam.is_active.is_(True),
                )
            )
        ).all()
        skill_members: dict[str, set[Any]] = {}
        team_members: dict[tuple[str, str], set[Any]] = {}
        for slug, name, team_type, user_id, is_available in team_rows:
            if user_id not in active_member_ids or not is_available:
                continue
            team_key = catalog_slug(slug or team_type or "team")
            team_label = str(name or slug or team_type or "Equipe")
            team_members.setdefault((team_key, team_label), set()).add(user_id)
            for candidate in (slug, team_type):
                if candidate:
                    skill_members.setdefault(catalog_slug(candidate), set()).add(user_id)

        resolved: dict[Any, Any | None] = {}

        async def routable_targets(candidate_ids: set[Any]) -> set[Any]:
            targets: set[Any] = set()
            for candidate_user_id in candidate_ids:
                if candidate_user_id not in resolved:
                    resolved[candidate_user_id] = await self._resolve_routable_user(
                        workspace_id=workspace_id,
                        candidate_user_id=candidate_user_id,
                        owner_user_id=owner.user_id,
                    )
                target = resolved[candidate_user_id]
                # A redirected specialist is not presented as available for
                # that specialty when the only real destination is the owner.
                # The UI will instead make the direct owner fallback explicit.
                if target is not None and (
                    target != owner.user_id or candidate_user_id == owner.user_id
                ):
                    targets.add(target)
            return targets

        def label(value: str) -> str:
            if value == WorkspaceRole.workspace_owner.value:
                return "Dono do escritório"
            return value.replace("_", " ").replace("-", " ").strip().title()

        roles = []
        for key, user_ids in sorted(role_members.items()):
            targets = await routable_targets(user_ids)
            if targets:
                roles.append({"key": key, "label": label(key), "routableCount": len(targets)})
        # The owner must be an explicit selection even when their package was
        # omitted in legacy membership data.
        if not any(item["key"] == WorkspaceRole.workspace_owner.value for item in roles):
            roles.append(
                {
                    "key": WorkspaceRole.workspace_owner.value,
                    "label": "Dono do escritório",
                    "routableCount": 1,
                }
            )
        roles.sort(
            key=lambda item: (item["key"] != WorkspaceRole.workspace_owner.value, item["label"])
        )
        skills = []
        for key, user_ids in sorted(skill_members.items()):
            targets = await routable_targets(user_ids)
            if targets:
                skills.append({"key": key, "label": label(key), "routableCount": len(targets)})
        teams = []
        for (key, team_label), user_ids in sorted(team_members.items()):
            targets = await routable_targets(user_ids)
            if targets:
                teams.append({"key": key, "label": team_label, "routableCount": len(targets)})
        direct_owner_only = (
            not any(item["key"] != WorkspaceRole.workspace_owner.value for item in roles)
            and not skills
            and not teams
        )
        return {
            "roles": roles,
            "skills": skills,
            "teams": teams,
            "directOwnerOnly": direct_owner_only,
            "ownerFallback": {
                "available": True,
                "label": "Dono do escritório",
                "reason": (
                    "Nenhuma função ou equipe especialista está ativa; a chamada seguirá diretamente para o dono."  # noqa: E501
                    if direct_owner_only
                    else "O dono do escritório acompanha todas as chamadas e recebe a escalada terminal."  # noqa: E501
                ),
            },
        }

    async def create_decision(
        self,
        *,
        workspace_id: Any,
        decision_key: str,
        options: dict[str, Any],
        required_roles: list[str],
        required_skills: list[str],
        requested_user_id: Any | None = None,
        agent_run_id: Any | None = None,
        flow_run_id: Any | None = None,
        sla_minutes: int = 60,
        max_hops: int = 4,
        allow_active_flow_run: bool = False,
    ) -> HumanDecision:
        def safe_routing_token(value: str) -> str:
            normalized = unicodedata.normalize("NFKD", str(value))
            token = "".join(char for char in normalized if not unicodedata.combining(char))
            token = token.strip().casefold().replace(" ", "_")
            if not re.fullmatch(r"[a-z0-9_-]{1,64}", token):
                raise HumanRoutingError("human routing role/skill must be a catalog slug")
            digits = "".join(char for char in token if char.isdigit())
            if token.isdigit() or len(digits) >= 8:
                raise HumanRoutingError("human routing token cannot contain a personal identifier")
            return token

        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9:_-]{0,254}", decision_key):
            raise HumanRoutingError("human decision key must be an opaque operational key")
        required_roles = list(dict.fromkeys(safe_routing_token(v) for v in required_roles))
        required_skills = list(dict.fromkeys(safe_routing_token(v) for v in required_skills))
        role_catalog = {role.value for role in WorkspaceRole} | set(WORKSPACE_ACCESS_PACKAGES)
        unknown_roles = set(required_roles) - role_catalog
        if unknown_roles:
            raise HumanRoutingError("human routing role is not in the workspace catalog")
        if not required_roles and not required_skills and requested_user_id is None:
            raise HumanRoutingError("required role, skill or explicit human is mandatory")
        if sla_minutes < 1 or sla_minutes > 10080:
            raise HumanRoutingError("human SLA must be between 1 minute and 7 days")
        max_hops = max(1, min(max_hops, 8))
        existing = await self.db.scalar(
            select(HumanDecision).where(
                HumanDecision.workspace_id == workspace_id,
                HumanDecision.decision_key == decision_key,
            )
        )
        if existing is not None:
            if existing.status == HumanDecisionStatus.pending:
                return existing
            raise HumanRoutingError("human decision key was already consumed")
        # A human decision is tenant-scoped even when a caller supplies an
        # execution id.  The database foreign keys alone cannot express that
        # both rows belong to the same workspace, so prove it before creating
        # an actionable decision.  Public/manual requests may only attach to a
        # run that is already awaiting a human; workers opt in while they are
        # atomically constructing that wait state.
        if flow_run_id is not None:
            from app.models.flow import FlowRun

            flow_run = await self.db.scalar(
                select(FlowRun).where(
                    FlowRun.id == flow_run_id,
                    FlowRun.workspace_id == workspace_id,
                )
            )
            if flow_run is None:
                raise HumanRoutingError("linked flow run is unavailable in this workspace")
            allowed_flow_statuses = {"waiting_human"}
            if allow_active_flow_run:
                allowed_flow_statuses.update({"active", "paused"})
            if flow_run.status not in allowed_flow_statuses:
                raise HumanRoutingError("linked flow run is not compatible with a human decision")
        if agent_run_id is not None:
            from app.models.agent_run import AgentRun, AgentRunStatus

            agent_run = await self.db.scalar(
                select(AgentRun).where(
                    AgentRun.id == agent_run_id,
                    AgentRun.workspace_id == workspace_id,
                )
            )
            if agent_run is None:
                raise HumanRoutingError("linked agent run is unavailable in this workspace")
            if agent_run.status not in {
                AgentRunStatus.waiting_human,
                AgentRunStatus.failed,
                AgentRunStatus.cancelled,
            }:
                raise HumanRoutingError("linked agent run is not eligible for a human decision")
        owner = await self.db.scalar(
            select(WorkspaceMembership.user_id)
            .join(User, User.id == WorkspaceMembership.user_id)
            .where(
                WorkspaceMembership.workspace_id == workspace_id,
                WorkspaceMembership.status == MembershipStatus.active,
                WorkspaceMembership.role == WorkspaceRole.workspace_owner,
                User.is_active.is_(True),
                User.deleted_at.is_(None),
                User.agent_type == "human",
            )
            .order_by(WorkspaceMembership.created_at.asc())
            .limit(1)
        )
        if owner is None:
            raise HumanRoutingError("workspace has no active owner to terminate escalation")
        explicit_membership: WorkspaceMembership | None = None
        if requested_user_id is not None:
            explicit_membership = await self.db.scalar(
                select(WorkspaceMembership)
                .join(User, User.id == WorkspaceMembership.user_id)
                .where(
                    WorkspaceMembership.workspace_id == workspace_id,
                    WorkspaceMembership.user_id == requested_user_id,
                    WorkspaceMembership.status == MembershipStatus.active,
                    User.is_active.is_(True),
                    User.deleted_at.is_(None),
                    User.agent_type == "human",
                )
            )
            if explicit_membership is None:
                raise HumanRoutingError("requested human is not active in this workspace")
            explicit_role = (
                explicit_membership.role.value
                if isinstance(explicit_membership.role, WorkspaceRole)
                else str(explicit_membership.role)
            )
            explicit_package = (explicit_membership.access_package or "").strip()
            if required_roles and not {
                explicit_role,
                explicit_package,
            }.intersection(required_roles):
                raise HumanRoutingError("requested human does not match the required role")

        all_memberships = list(
            (
                await self.db.scalars(
                    select(WorkspaceMembership)
                    .join(User, User.id == WorkspaceMembership.user_id)
                    .where(
                        WorkspaceMembership.workspace_id == workspace_id,
                        WorkspaceMembership.status == MembershipStatus.active,
                        User.is_active.is_(True),
                        User.deleted_at.is_(None),
                        User.agent_type == "human",
                    )
                )
            ).all()
        )
        memberships = (
            [
                item
                for item in all_memberships
                if item.role.value in required_roles
                or (item.access_package or "") in required_roles
            ]
            if required_roles
            else list(all_memberships)
        )
        if required_skills:
            normalized_skills = {
                value.strip().casefold() for value in required_skills if value.strip()
            }
            skill_rows = (
                await self.db.execute(
                    select(
                        WorkspaceTeamMember.user_id,
                        WorkspaceTeam.slug,
                        WorkspaceTeam.team_type,
                        WorkspaceTeamMember.is_available,
                    )
                    .join(WorkspaceTeam, WorkspaceTeam.id == WorkspaceTeamMember.team_id)
                    .where(
                        WorkspaceTeam.workspace_id == workspace_id,
                        WorkspaceTeam.is_active.is_(True),
                    )
                )
            ).all()
            skilled_user_ids = {
                user_id
                for user_id, slug, team_type, is_available in skill_rows
                if is_available
                if (slug or "").strip().casefold() in normalized_skills
                or (team_type or "").strip().casefold() in normalized_skills
            }
            skill_catalog = _HUMAN_SKILL_CATALOG | {
                safe_routing_token(value)
                for _, slug, team_type, _ in skill_rows
                for value in (slug, team_type)
                if value
            }
            if not normalized_skills.issubset(skill_catalog):
                raise HumanRoutingError("human routing skill is not in the workspace catalog")
            memberships = [item for item in memberships if item.user_id in skilled_user_ids]
            if (
                explicit_membership is not None
                and explicit_membership.user_id not in skilled_user_ids
            ):
                raise HumanRoutingError("requested human does not match the required skills")

        memberships.sort(key=lambda item: _membership_routing_rank(item, required_roles))
        # Prefer the actual lead/supervisor of the specialist's team before
        # falling back to workspace-wide managerial roles.  This makes the
        # escalation organizational instead of choosing an arbitrary manager
        # with the same package.
        specialist_ids = {member.user_id for member in memberships}
        team_rows = (
            await self.db.execute(
                select(
                    WorkspaceTeamMember.team_id,
                    WorkspaceTeamMember.user_id,
                    WorkspaceTeamMember.member_role,
                    WorkspaceTeamMember.is_available,
                ).where(WorkspaceTeamMember.workspace_id == workspace_id)
            )
        ).all()
        specialist_team_ids = {
            team_id for team_id, user_id, _, _ in team_rows if user_id in specialist_ids
        }
        active_user_ids = {member.user_id for member in all_memberships}
        team_manager_ids = [
            user_id
            for team_id, user_id, member_role, is_available in team_rows
            if team_id in specialist_team_ids
            and member_role in {"lead", "supervisor"}
            and is_available
            and user_id in active_user_ids
            and user_id not in specialist_ids
        ]
        membership_by_user = {member.user_id: member for member in all_memberships}
        team_hierarchy = [
            membership_by_user[user_id]
            for user_id in dict.fromkeys(team_manager_ids)
            if user_id in membership_by_user
        ]
        team_hierarchy.sort(
            key=lambda item: (
                _WORKSPACE_ROLE_ESCALATION_RANK.get(item.role, 20),
                str(item.user_id),
            )
        )
        # If a team has no active lead/supervisor, ascend through active
        # workspace managers/admins and finally to the workspace owner.
        fallback_hierarchy = [
            item
            for item in all_memberships
            if item.role
            in {
                WorkspaceRole.legal_manager,
                WorkspaceRole.finance_manager,
                WorkspaceRole.workspace_admin,
            }
            and item.user_id not in specialist_ids
            and item.user_id not in {member.user_id for member in team_hierarchy}
        ]
        fallback_hierarchy.sort(
            key=lambda item: (
                _WORKSPACE_ROLE_ESCALATION_RANK.get(item.role, 20),
                str(item.user_id),
            )
        )
        candidates: list[Any] = (
            ([requested_user_id] if explicit_membership else [])
            + [member.user_id for member in memberships]
            + [member.user_id for member in team_hierarchy]
            + [member.user_id for member in fallback_hierarchy]
        )
        candidate_path = finite_routing_path(candidates, owner=owner, max_hops=max_hops)
        if not candidate_path:
            raise HumanRoutingError("no finite human routing path")
        # Only actually available candidates become escalation recipients.  A
        # redirect source is not an escalation destination; only its approved,
        # available target is.  The workspace owner remains the explicit final
        # accountable fallback and is always notified, even if their own
        # availability profile says unavailable.
        available_path: list[Any] = []
        for candidate in candidate_path:
            if candidate == owner:
                continue
            effective_user_id = await self._resolve_routable_user(
                workspace_id=workspace_id,
                candidate_user_id=candidate,
                owner_user_id=owner,
                max_hops=max_hops,
            )
            if effective_user_id is not None:
                available_path.append(effective_user_id)
        path = finite_routing_path(available_path, owner=owner, max_hops=max_hops)
        assigned = path[0] if path and path[0] != owner else owner
        owner_fallback = assigned == owner
        decision = HumanDecision(
            workspace_id=workspace_id,
            agent_run_id=agent_run_id,
            flow_run_id=flow_run_id,
            decision_key=decision_key,
            options=options,
            required_roles=required_roles,
            required_skills=required_skills,
            assigned_user_id=assigned,
            owner_user_id=owner,
            due_at=datetime.now(UTC) + timedelta(minutes=sla_minutes),
            expires_at=datetime.now(UTC)
            + timedelta(minutes=max(sla_minutes * max_hops, sla_minutes)),
            routing_path=[str(value) for value in path],
            max_hops=max_hops,
        )
        self.db.add(decision)
        await self.db.flush()
        self.db.add(
            KlmAuditEvent(
                workspace_id=workspace_id,
                actor_type="system",
                actor_label="Agent runtime",
                event_type="human_decision_routed",
                entity_type="human_decision",
                entity_id=decision.id,
                summary="Intervenção humana roteada por função com fallback finito ao dono.",
                correlation_id=_decision_correlation(decision_key),
                after_data={
                    "assignedUserId": str(assigned),
                    "ownerUserId": str(owner),
                    "routingPath": decision.routing_path,
                    "dueAt": decision.due_at.isoformat(),
                    "maxHops": max_hops,
                    "ownerFallback": owner_fallback,
                },
            )
        )
        await self._record_call(
            decision,
            assigned,
            "owner" if assigned == owner else "assignee",
            event_key="assigned",
        )
        if owner != assigned:
            await self._record_call(decision, owner, "owner", event_key="owner_watch")
        return decision

    async def _record_call(
        self, decision: HumanDecision, recipient_id: Any, kind: str, *, event_key: str
    ) -> None:
        requested_function = (
            ", ".join(
                [
                    *(getattr(decision, "required_roles", None) or []),
                    *(getattr(decision, "required_skills", None) or []),
                ]
            )
            or "responsável disponível"
        )
        assigned_user_id = getattr(decision, "assigned_user_id", None)
        assignee = (
            await self.db.scalar(select(User).where(User.id == assigned_user_id))
            if assigned_user_id
            else None
        )
        assignee_label = assignee.name if assignee and assignee.name else "responsável definido"
        await NotificationService(self.db).create(
            recipient_id,
            "Intervenção humana necessária",
            f"Função solicitada: {requested_function}. Responsável atual: {assignee_label}. Prazo: "
            f"{decision.due_at.isoformat() if decision.due_at else 'não definido'}.",
            NotificationType.urgent,
            workspace_id=decision.workspace_id,
        )
        self.db.add(
            HumanNotificationDelivery(
                workspace_id=decision.workspace_id,
                decision_id=decision.id,
                recipient_user_id=recipient_id,
                recipient_kind=kind,
                channel="in_app",
                event_key=event_key,
                status=HumanDeliveryStatus.delivered,
                attempts=1,
                delivered_at=datetime.now(UTC),
            )
        )
        workspace = await self.db.scalar(
            select(Workspace).where(Workspace.id == decision.workspace_id)
        )
        user = await self.db.scalar(select(User).where(User.id == recipient_id))
        phone = (
            (workspace.owner_whatsapp_phone or (user.phone if user else None))
            if kind == "owner" and workspace
            else (user.phone if user else None)
        )
        if not phone and decision.owner_user_id:
            await NotificationService(self.db).create(
                decision.owner_user_id,
                "Telefone para alertas humanos não configurado",
                "A intervenção está visível no sistema, mas o aviso telefônico não pode "
                "ser enviado até que o telefone/WhatsApp do escritório seja configurado.",
                NotificationType.warning,
                workspace_id=decision.workspace_id,
            )
        self.db.add(
            HumanNotificationDelivery(
                workspace_id=decision.workspace_id,
                decision_id=decision.id,
                recipient_user_id=recipient_id,
                recipient_kind=kind,
                channel="phone",
                event_key=event_key,
                status=(
                    HumanDeliveryStatus.pending if phone else HumanDeliveryStatus.not_configured
                ),
                attempts=0,
                last_error_code=None if phone else "phone_not_configured",
            )
        )

    async def _record_terminal_owner_alert(
        self, decision: HumanDecision, *, escalation_level: int
    ) -> bool:
        """Durably call the office owner when the finite route is exhausted.

        An ``expired`` decision is terminal, so this is deliberately a fresh,
        idempotent delivery event rather than a boolean asserted in the audit
        payload.  The unique delivery constraint makes a worker retry safe and
        the phone worker turns ``pending`` into a provider receipt, failure or
        explicit ``not_configured`` state.
        """
        if not decision.owner_user_id:
            return False
        await self._record_call(
            decision,
            decision.owner_user_id,
            "owner",
            event_key=f"terminal_owner_{escalation_level}",
        )
        return True

    async def escalate(self, decision: HumanDecision) -> bool:
        if decision.status != HumanDecisionStatus.pending:
            return False
        route = [value for value in decision.routing_path if value]
        next_level = decision.escalation_level + 1
        if next_level >= len(route) or next_level >= decision.max_hops:
            decision.status = HumanDecisionStatus.expired
            decision.due_at = None
            owner_alert_created = await self._record_terminal_owner_alert(
                decision, escalation_level=next_level
            )
            self.db.add(
                KlmAuditEvent(
                    workspace_id=decision.workspace_id,
                    actor_type="system",
                    actor_label="Human escalation worker",
                    event_type="human_decision_expired",
                    entity_type="human_decision",
                    entity_id=decision.id,
                    summary="Intervenção expirou após percorrer a hierarquia finita.",
                    correlation_id=_decision_correlation(decision.decision_key),
                    after_data={
                        "status": "expired",
                        "escalationLevel": decision.escalation_level,
                        "ownerAlertCreated": owner_alert_created,
                    },
                )
            )
            await self.db.flush()
            return False
        from uuid import UUID

        availability = StaffAvailabilityService(self.db)
        next_user = None
        selected_level = None
        owner = decision.owner_user_id
        # Availability is rechecked at every escalation.  A person who became
        # unavailable after the initial call is skipped; the finite route still
        # terminates at the owner, who is the explicit accountable fallback.
        for level in range(next_level, min(len(route), decision.max_hops)):
            candidate = UUID(route[level])
            if candidate == owner:
                next_user = owner
                selected_level = level
                break
            try:
                resolution = await availability.resolve_responsibility(
                    workspace_id=decision.workspace_id,
                    responsible_user_id=candidate,
                    max_hops=decision.max_hops,
                )
            except AvailabilityDomainError:
                continue
            effective_user_id = _usable_availability_resolution(resolution)
            if effective_user_id is not None:
                next_user = effective_user_id
                selected_level = level
                break
        if next_user is None or selected_level is None:
            decision.status = HumanDecisionStatus.expired
            decision.due_at = None
            owner_alert_created = await self._record_terminal_owner_alert(
                decision, escalation_level=next_level
            )
            self.db.add(
                KlmAuditEvent(
                    workspace_id=decision.workspace_id,
                    actor_type="system",
                    actor_label="Human escalation worker",
                    event_type="human_decision_expired",
                    entity_type="human_decision",
                    entity_id=decision.id,
                    summary=(
                        "Intervenção expirou: não restou humano disponível na hierarquia finita."
                    ),
                    correlation_id=_decision_correlation(decision.decision_key),
                    after_data={
                        "status": "expired",
                        "reason": "no_available_human_in_finite_hierarchy",
                        "ownerAlertCreated": owner_alert_created,
                    },
                )
            )
            await self.db.flush()
            return False
        decision.escalation_level = selected_level
        decision.assigned_user_id = next_user
        decision.due_at = datetime.now(UTC) + timedelta(hours=1)
        await self._record_call(
            decision,
            next_user,
            "owner" if decision.owner_user_id == next_user else "assignee",
            event_key=f"escalated_{next_level}",
        )
        if decision.owner_user_id and decision.owner_user_id != next_user:
            await self._record_call(
                decision,
                decision.owner_user_id,
                "owner",
                event_key=f"owner_watch_{next_level}",
            )
        self.db.add(
            KlmAuditEvent(
                workspace_id=decision.workspace_id,
                actor_type="system",
                actor_label="Human escalation worker",
                event_type="human_decision_escalated",
                entity_type="human_decision",
                entity_id=decision.id,
                summary=f"Intervenção escalada para o nível {next_level}.",
                correlation_id=_decision_correlation(decision.decision_key),
                before_data={"escalationLevel": next_level - 1},
                after_data={
                    "escalationLevel": next_level,
                    "assignedUserId": str(next_user),
                    "dueAt": decision.due_at.isoformat(),
                },
            )
        )
        await self.db.flush()
        return True
