"""Read contracts for the pinned OSS FunctionUnit ledger."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from app.schemas.base import CamelCaseSchema

OSSFunctionUnitState = Literal[
    "discovered",
    "mapped",
    "native_shadow",
    "native_primary",
    "native_verified",
    "blocked_proprietary",
    "blocked_license",
]


class OSSFunctionUnitDispositionRead(CamelCaseSchema):
    state: OSSFunctionUnitState
    rule: str
    reason: str
    priority: int = 0


class OSSFunctionUnitMappingRead(CamelCaseSchema):
    assessment: str
    canonical_entity: str | None = None
    spa_route: str | None = None
    application_service: str | None = None
    agent_tool: str | None = None
    audit_event: str | None = None
    local_mode: str | None = None
    fixture: str | None = None
    klm_tests: list[str] = Field(default_factory=list)
    visual_proof: list[str] = Field(default_factory=list)
    sidecar_off_proof: list[str] = Field(default_factory=list)
    capability_id: str | None = None
    coverage_kind: str | None = None
    api_route: str | None = None
    runtime_contract_id: str | None = None
    runtime_handler: str | None = None
    semantic_profile: str | None = None
    proof_profile: str | None = None
    verification_manifest: str | None = None
    binding_id: str | None = None
    native_operation_id: str | None = None
    action_id: str | None = None
    capability_action_id: str | None = None
    equivalence_group: str | None = None
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    error_contract: list[str] = Field(default_factory=list)
    authorization_policy: dict[str, Any] = Field(default_factory=dict)
    state_transition: dict[str, Any] = Field(default_factory=dict)
    effects: dict[str, Any] = Field(default_factory=dict)
    maturity: OSSFunctionUnitState | None = None
    conformance_ledger: str | None = None


class OSSFunctionUnitSourceRead(CamelCaseSchema):
    path: str
    line: int = Field(ge=1)
    evidence: str
    extractor: str
    boundary_type: str
    family: str
    transport: str
    operation: str
    method: str | None = None
    route: str
    handler: str
    aliases: list[str] = Field(default_factory=list)
    candidate_id: str
    candidate_kind: str
    details: dict[str, Any] = Field(default_factory=dict)


class OSSFunctionUnitLicenseRead(CamelCaseSchema):
    status: str
    conclusion: str
    scope_prefix: str
    package: dict[str, Any] | None = None
    evidence: list[dict[str, Any]] = Field(default_factory=list)


class OSSFunctionUnitRead(CamelCaseSchema):
    id: str
    upstream: str
    ref: str
    commit: str
    category: str
    facets: list[str] = Field(default_factory=list)
    name: str
    contract_key: str
    source: OSSFunctionUnitSourceRead
    license: OSSFunctionUnitLicenseRead
    dependencies: list[str] = Field(default_factory=list)
    interface_contract: dict[str, Any] = Field(default_factory=dict)
    upstream_verification: dict[str, Any] = Field(default_factory=dict)
    klm_mapping: OSSFunctionUnitMappingRead
    disposition: OSSFunctionUnitDispositionRead


class OSSFunctionUnitSummaryRead(CamelCaseSchema):
    denominator: int = Field(ge=0)
    licensed: int = Field(ge=0)
    blocked: int = Field(ge=0)
    verified: int = Field(ge=0)
    remaining: int = Field(ge=0)
    by_upstream: dict[str, int]
    by_category: dict[str, int]
    by_disposition: dict[str, int]
    all_pins_verified: bool


class OSSFunctionUnitFilterOptionsRead(CamelCaseSchema):
    upstreams: list[str]
    categories: list[str]
    dispositions: list[str]
    families: list[str]
    capabilities: list[str]


class OSSFunctionUnitListRead(CamelCaseSchema):
    schema_version: int
    scope: Literal["pinned-community-function-units"]
    digest: str
    summary: OSSFunctionUnitSummaryRead
    filters: OSSFunctionUnitFilterOptionsRead
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)
    total: int = Field(ge=0)
    pages: int = Field(ge=0)
    items: list[OSSFunctionUnitRead]


class OSSFunctionUnitRuntimeRead(CamelCaseSchema):
    schema_version: str
    unit: dict[str, Any]
    contract: dict[str, Any]
    state: dict[str, Any]
    native_source: dict[str, Any]
    runtime: dict[str, Any]


class OSSFunctionUnitActionWrite(CamelCaseSchema):
    mode: Literal["dry-run", "execute", "configure"] = "dry-run"
    idempotency_key: str = Field(min_length=8, max_length=180)
    resource_id: str | None = Field(default=None, max_length=180)
    payload: dict[str, Any] = Field(default_factory=dict)


class OSSFunctionUnitActionRead(CamelCaseSchema):
    status: Literal["succeeded", "configured", "simulated", "blocked_external"]
    idempotent_replay: bool
    event_id: str | None = None
    resource_id: str
    result: dict[str, Any]
    external_network_executed: Literal[False]
