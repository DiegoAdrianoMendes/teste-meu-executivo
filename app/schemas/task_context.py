# ruff: noqa: UP042
"""Explicit context contract for canonical tenant tasks."""

from __future__ import annotations

import enum
from uuid import UUID

from pydantic import Field, model_validator

from app.schemas.base import WriteSchema
from app.schemas.task import TaskCreate


class TaskContextType(str, enum.Enum):
    workspace = "workspace"
    client = "client"
    contract = "contract"
    matter = "matter"


class TaskOrigin(str, enum.Enum):
    legacy = "legacy"
    manual = "manual"
    administrative = "administrative"
    commercial = "commercial"
    publication = "publication"
    process = "process"
    hearing = "hearing"
    deadline = "deadline"
    automation = "automation"


LEGAL_MATTER_REQUIRED_ORIGINS = frozenset(
    {
        TaskOrigin.publication,
        TaskOrigin.process,
        TaskOrigin.hearing,
        TaskOrigin.deadline,
    }
)


class TaskContextWrite(WriteSchema):
    type: TaskContextType
    client_id: UUID | None = None
    contract_id: UUID | None = None
    matter_id: UUID | None = None

    @model_validator(mode="after")
    def validate_exact_anchor(self) -> TaskContextWrite:
        if self.type == TaskContextType.workspace:
            if any((self.client_id, self.contract_id, self.matter_id)):
                raise ValueError("Contexto workspace não aceita outro vínculo.")
        elif self.type == TaskContextType.client:
            if self.client_id is None or self.contract_id is not None or self.matter_id is not None:
                raise ValueError("Contexto cliente exige somente clientId.")
        elif self.type == TaskContextType.matter:
            if self.matter_id is None or self.client_id is not None or self.contract_id is not None:
                raise ValueError("Contexto Caso 360 exige somente matterId.")
        elif self.type == TaskContextType.contract:
            if self.client_id is None or self.contract_id is None or self.matter_id is not None:
                raise ValueError("Contexto contrato exige clientId e contractId.")
        return self


class ContextualTaskCreate(TaskCreate):
    context: TaskContextWrite
    origin: TaskOrigin = TaskOrigin.manual
    origin_resource_id: UUID | None = None
    origin_label: str | None = Field(default=None, max_length=240)

    @model_validator(mode="after")
    def legal_sources_require_matter(self) -> ContextualTaskCreate:
        if self.origin in LEGAL_MATTER_REQUIRED_ORIGINS:
            if self.context.type != TaskContextType.matter:
                raise ValueError(
                    "Publicação, processo, audiência e prazo exigem vínculo com Caso 360."
                )
            if self.origin_resource_id is None:
                raise ValueError("A origem jurídica exige originResourceId.")
        return self
