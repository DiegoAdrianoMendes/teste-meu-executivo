"""Contracts for the governed copilots embedded in operational pages."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

OperationalCopilotDomain = Literal[
    "playbooks",
    "tracking",
    "connections",
    "social_comments",
    "agenda",
]

_SECRET_MARKERS = (
    "access_token",
    "api_key",
    "app_secret",
    "authorization",
    "client_secret",
    "credential",
    "password",
    "private_key",
    "registration_pin",
    "secret",
    "token",
)


def _contains_secret_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).strip().casefold().replace("-", "_")
            if any(marker in normalized for marker in _SECRET_MARKERS):
                return True
            if _contains_secret_key(item):
                return True
    elif isinstance(value, list):
        return any(_contains_secret_key(item) for item in value)
    return False


class OperationalCopilotAttachment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    media_type: str = Field(min_length=1, max_length=160)
    size_bytes: int = Field(ge=0, le=20_000_000)
    text_excerpt: str | None = Field(default=None, max_length=8000)

    @model_validator(mode="after")
    def reject_audio(self) -> OperationalCopilotAttachment:
        if self.media_type.casefold().startswith("audio/"):
            raise ValueError("Este copiloto não aceita arquivos de áudio")
        return self


class OperationalCopilotPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: OperationalCopilotDomain
    instruction: str = Field(min_length=3, max_length=12000)
    context_key: str | None = Field(default=None, max_length=180)
    context_snapshot: dict[str, Any] = Field(default_factory=dict)
    attachments: list[OperationalCopilotAttachment] = Field(
        default_factory=list,
        max_length=12,
    )

    @model_validator(mode="after")
    def enforce_safe_context_budget(self) -> OperationalCopilotPlanRequest:
        if sum(item.size_bytes for item in self.attachments) > 40_000_000:
            raise ValueError("Os anexos excedem o limite total de 40 MB")
        if _contains_secret_key(self.context_snapshot):
            raise ValueError("O contexto do copiloto não pode conter credenciais ou segredos")
        serialized = json.dumps(
            self.context_snapshot,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if len(serialized) > 50_000:
            raise ValueError("O contexto do copiloto excede o limite de 50 KB")
        return self


class OperationalCopilotActionRead(BaseModel):
    action_id: UUID
    label: str
    description: str
    kind: Literal["ui_update", "local_read", "local_draft", "local_write"]
    risk: Literal["low", "medium"]
    payload: dict[str, Any] = Field(default_factory=dict)
    required_permission: str
    approval_required: bool = True


class OperationalCopilotPlanRead(BaseModel):
    proposal_id: UUID
    status: Literal["awaiting_approval"] = "awaiting_approval"
    engine: str
    domain: OperationalCopilotDomain
    summary: str
    rationale: list[str]
    suggestions: list[str]
    questions: list[str]
    warnings: list[str]
    actions: list[OperationalCopilotActionRead]
    playbook_name: str | None = None
    playbook_version: int | None = None
    account_context_used: bool
    attachment_count: int
    approval_required: bool = True
    external_actions_executed: int = 0
    generated_at: datetime
