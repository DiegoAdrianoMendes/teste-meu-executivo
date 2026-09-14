"""Verificação manual da assinatura `stripe-signature`, sem SDK.

Formato do header (doc Stripe): `t=<timestamp>,v1=<assinatura_hex>[,v1=...]`.
Assinatura = HMAC-SHA256(secret, f"{timestamp}.{payload}") em hex, comparada
em tempo constante (`hmac.compare_digest`). Tolerância de 5 minutos contra
replay. Mesma lógica de `app/api/v1/endpoints/stripe_webhook.py`
(`_verify_stripe_signature`) — extraída aqui para reuso pelo adapter/fake
sem tocar naquele arquivo já testado.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

DEFAULT_TOLERANCE_SECONDS = 300  # 5 minutos, mesma janela do Stripe SDK oficial.


class StripeSignatureError(ValueError):
    """`stripe-signature` ausente, malformada, expirada ou inválida."""


def compute_stripe_signature(payload: bytes, secret: str, timestamp: int) -> str:
    """HMAC-SHA256 hex do payload assinado `f"{timestamp}.{payload}"`."""
    signed_payload = f"{timestamp}.".encode("utf-8") + payload
    return hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()


def verify_stripe_signature(
    payload: bytes,
    sig_header: str,
    secret: str,
    *,
    tolerance_seconds: int = DEFAULT_TOLERANCE_SECONDS,
    now: float | None = None,
) -> dict[str, Any]:
    """Verifica `sig_header` contra `payload` e devolve o evento parseado.

    Levanta `StripeSignatureError` (nunca aceita payload não assinado ou
    fora da janela de tolerância) — fail-closed.
    """
    secret = (secret or "").strip()
    if not secret:
        raise StripeSignatureError("STRIPE_WEBHOOK_SECRET não configurado.")
    if not sig_header or not sig_header.strip():
        raise StripeSignatureError("Cabeçalho stripe-signature ausente.")

    try:
        parts = [part.strip().split("=", 1) for part in sig_header.split(",")]
        timestamp = int(next(value for key, value in parts if key == "t"))
        v1_signatures = [value for key, value in parts if key == "v1" and value]
    except (ValueError, StopIteration):
        raise StripeSignatureError("Cabeçalho stripe-signature malformado.") from None
    if not v1_signatures:
        raise StripeSignatureError("Cabeçalho stripe-signature sem assinatura v1.")

    current = time.time() if now is None else now
    if abs(current - timestamp) > tolerance_seconds:
        raise StripeSignatureError("Evento Stripe fora da janela de tolerância (possível replay).")

    expected = compute_stripe_signature(payload, secret, timestamp)
    if not any(hmac.compare_digest(expected, candidate) for candidate in v1_signatures):
        raise StripeSignatureError("Assinatura Stripe inválida.")

    try:
        event = json.loads(payload)
    except (ValueError, TypeError) as exc:
        raise StripeSignatureError("Payload do evento Stripe não é JSON válido.") from exc
    if not isinstance(event, dict):
        raise StripeSignatureError("Payload do evento Stripe deve ser um objeto JSON.")
    return event
