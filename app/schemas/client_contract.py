from datetime import date, datetime
from uuid import UUID

from pydantic import Field, model_validator

from app.models.client_contract import ClientContractStatus
from app.schemas.base import CamelCaseSchema, WriteSchema


class ClientContractCreate(WriteSchema):
    client_id: UUID
    matter_id: UUID | None = None
    title: str = Field(min_length=2, max_length=240)
    status: ClientContractStatus = ClientContractStatus.draft
    effective_from: date | None = None
    effective_until: date | None = None

    @model_validator(mode="after")
    def dates_are_ordered(self):
        if (
            self.effective_from
            and self.effective_until
            and self.effective_until < self.effective_from
        ):
            raise ValueError("effectiveUntil não pode ser anterior a effectiveFrom")
        return self


class ClientContractRead(CamelCaseSchema):
    id: UUID
    workspace_id: UUID
    client_id: UUID
    matter_id: UUID | None
    title: str
    status: ClientContractStatus
    effective_from: date | None
    effective_until: date | None
    version: int
    created_at: datetime
    updated_at: datetime
