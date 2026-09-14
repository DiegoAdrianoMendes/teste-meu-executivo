from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class DeploymentCreate(BaseModel):
    agent_key: str = Field(min_length=1, max_length=160)
    role_package: str = Field(min_length=1, max_length=80)
    version: int = 1
    config: dict[str, Any] = Field(default_factory=dict)
    gates: dict[str, Any] = Field(default_factory=dict)


class HumanRoutingCatalogOption(BaseModel):
    """Safe, workspace-scoped authoring option; deliberately no person ID."""

    key: str
    label: str
    routable_count: int = Field(
        ge=0,
        validation_alias="routableCount",
        serialization_alias="routableCount",
    )


class HumanRoutingOwnerFallback(BaseModel):
    available: bool
    label: str
    reason: str


class HumanRoutingCatalogRead(BaseModel):
    roles: list[HumanRoutingCatalogOption]
    skills: list[HumanRoutingCatalogOption]
    teams: list[HumanRoutingCatalogOption]
    direct_owner_only: bool = Field(
        validation_alias="directOwnerOnly",
        serialization_alias="directOwnerOnly",
    )
    owner_fallback: HumanRoutingOwnerFallback = Field(
        validation_alias="ownerFallback",
        serialization_alias="ownerFallback",
    )


class HumanRoutingPolicyRead(BaseModel):
    key: str
    version: str
    label: str
    description: str
    required_roles: list[str] = Field(
        validation_alias="required_roles", serialization_alias="requiredRoles"
    )
    required_skills: list[str] = Field(
        validation_alias="required_skills", serialization_alias="requiredSkills"
    )
    max_hops: int = Field(validation_alias="max_hops", serialization_alias="maxHops")
    available: bool
    specialist_available: bool = Field(
        validation_alias="specialistAvailable", serialization_alias="specialistAvailable"
    )
    matched_roles: list[str] = Field(
        validation_alias="matchedRoles", serialization_alias="matchedRoles"
    )
    matched_skills: list[str] = Field(
        validation_alias="matchedSkills", serialization_alias="matchedSkills"
    )
    fallback_mode: str = Field(validation_alias="fallbackMode", serialization_alias="fallbackMode")


class HumanRoutingPolicyCatalogRead(BaseModel):
    version: str
    policies: list[HumanRoutingPolicyRead]


class HumanDecisionCreate(BaseModel):
    options: dict[str, Any] = Field(default_factory=dict)
    agent_run_id: UUID | None = None
    flow_run_id: UUID | None = None
    required_roles: list[str] = Field(default_factory=list, max_length=16)
    required_skills: list[str] = Field(default_factory=list, max_length=16)
    requested_user_id: UUID | None = None
    sla_minutes: int = Field(default=60, ge=1, le=10080)
    max_hops: int = Field(default=4, ge=1, le=8)


class HumanDecisionResolve(BaseModel):
    choice: dict[str, Any]


class AgentRunHumanInput(BaseModel):
    """Allowlisted values supplied to a waiting AgentRun checkpoint."""

    values: dict[str, Any] = Field(default_factory=dict, max_length=32)


class ContextRunCreate(BaseModel):
    context_type: str
    context_id: UUID | None = None
    agent_name: str
    trigger_source: str
    input_payload: dict[str, Any] | None = None
    dedupe_key: str | None = None
