"""Regressão: a chave de idempotência do reenvio de convite cabe na coluna.

`identity_idempotency_records.operation` é String(100) no PostgreSQL. A chave
antiga, com dois UUIDs e prefixo de 28 caracteres, tinha 101 e derrubava o
reenvio em produção (08/09/2026) sem que a suíte em SQLite percebesse.
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from app.models.identity import IdentityIdempotencyRecord
from app.services.identity_lifecycle_service import (
    IDENTITY_IDEMPOTENCY_OPERATION_MAX_LENGTH,
    invitation_resend_operation,
)


def test_operation_max_length_matches_the_model_column() -> None:
    column = IdentityIdempotencyRecord.__table__.c.operation
    assert column.type.length == IDENTITY_IDEMPOTENCY_OPERATION_MAX_LENGTH


def test_invitation_resend_operation_fits_the_column_with_two_uuids() -> None:
    operation = invitation_resend_operation(uuid4(), uuid4())
    assert operation.startswith("invitation-resend:")
    assert len(operation) <= IDENTITY_IDEMPOTENCY_OPERATION_MAX_LENGTH


def test_invitation_resend_operation_refuses_to_overflow() -> None:
    too_long = "x" * IDENTITY_IDEMPOTENCY_OPERATION_MAX_LENGTH
    with pytest.raises(ValueError):
        invitation_resend_operation(too_long, uuid4())
