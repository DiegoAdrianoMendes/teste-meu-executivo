"""
Schemas Pydantic para Client.
Compatível com a interface Client do frontend TypeScript.
Todos os campos são snake_case no banco e camelCase no JSON (via CamelCaseSchema).
"""

from datetime import datetime
from uuid import UUID

from pydantic import field_validator

from app.schemas.base import CamelCaseSchema, WriteSchema
from app.services.whatsapp.helpers import normalize_phone_number


def _normalize_client_phone(value: str | None) -> str | None:
    if not str(value or "").strip():
        return None
    normalized = normalize_phone_number(value)
    if normalized is None:
        raise ValueError(
            "Informe DDI, DDD e número válidos. Exemplo aceito pela Meta: 5561999999999"
        )
    return normalized


class ClientCreate(WriteSchema):
    name: str
    cpf: str | None = None
    email: str | None = None
    phone: str | None = None
    employer: str | None = None
    position: str | None = None

    _canonical_phone = field_validator("phone")(_normalize_client_phone)


class ClientUpdate(WriteSchema):
    name: str | None = None
    cpf: str | None = None
    email: str | None = None
    phone: str | None = None
    employer: str | None = None
    position: str | None = None
    # Optional optimistic version for commands/flows. Legacy callers remain
    # compatible when they omit it.
    expected_updated_at: datetime | None = None

    _canonical_phone = field_validator("phone")(_normalize_client_phone)


class ClientRead(CamelCaseSchema):
    """
    Compatível com a interface Client do frontend:
      id, name, cpf, email, phone, employer, position
    """

    id: UUID
    name: str
    cpf: str | None = None
    email: str | None = None
    phone: str | None = None
    employer: str | None = None
    position: str | None = None
    sensitive_data_hidden: bool = False
    masked_label: str | None = None
