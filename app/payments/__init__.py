"""Camada de abstração de gateway de pagamentos (F1).

`PaymentGatewayPort` define o contrato; `AsaasAdapter` é a implementação real
(fail-closed, atrás de `external_delivery_allowed()`); `FakeGatewayAdapter` é
o dublê determinístico usado em teste/dev. `get_gateway_for_workspace`
resolve a credencial do workspace (via `IntegrationConnection`) e devolve o
adapter correto.
"""
from __future__ import annotations

from app.payments.factory import GatewayCredentialError, get_gateway_for_workspace
from app.payments.port import (
    ChargeRequest,
    ChargeResult,
    PaymentGatewayPort,
    StatementEntry,
)

__all__ = [
    "ChargeRequest",
    "ChargeResult",
    "StatementEntry",
    "PaymentGatewayPort",
    "GatewayCredentialError",
    "get_gateway_for_workspace",
]
