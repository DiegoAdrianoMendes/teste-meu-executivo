"""Fail-closed transition policy for extensible KLM-owned aggregates."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any, Literal

NativeAggregateEffect = Literal["read", "upsert", "archive", "event"]
NativeAggregateState = Literal["active", "archived"]

_DOMAIN_BY_INTERNAL_SOURCE = {
    "chatwoot": "communications",
    "rocketchat": "collaboration",
    "chattermate": "automation",
    "bottlecrm": "crm",
}
_RESERVED_OWNERSHIP_FIELDS = frozenset(
    {
        "capability",
        "createdat",
        "createdbyuserid",
        "domain",
        "provider",
        "recordversion",
        "resourceid",
        "schemaversion",
        "tenantid",
        "updatedat",
        "updatedbyuserid",
        "workspaceid",
    }
)
_RESTORE_HEADS = frozenset({"activate", "enable", "reopen", "restore"})
_TYPE_RE = re.compile(r"[a-z][a-z0-9_.:-]{0,119}\Z")


class NativeAggregatePolicyError(ValueError):
    """Base error for invalid native aggregate transitions."""


class NativeAggregateVersionRequiredError(NativeAggregatePolicyError):
    """Raised when an existing aggregate is mutated without a version precondition."""


class NativeAggregateVersionConflictError(NativeAggregatePolicyError):
    """Raised when a transition targets a stale aggregate version."""


class NativeAggregateStateError(NativeAggregatePolicyError):
    """Raised when a transition is invalid for the current state."""


@dataclass(frozen=True)
class NativeAggregateContract:
    domain: str
    aggregate_type: str
    schema_version: int = 1


@dataclass(frozen=True)
class NativeAggregateTransition:
    state: NativeAggregateState
    data: dict[str, Any]
    version: int
    data_sha256: str
    created: bool


def canonical_native_data_digest(data: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            data,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def native_aggregate_contract(
    internal_source: str,
    aggregate_type: str,
) -> NativeAggregateContract:
    domain = _DOMAIN_BY_INTERNAL_SOURCE.get(str(internal_source).casefold())
    if domain is None:
        raise NativeAggregatePolicyError("Domínio nativo desconhecido.")
    normalized_type = str(aggregate_type or "").strip().casefold()
    if _TYPE_RE.fullmatch(normalized_type) is None:
        raise NativeAggregatePolicyError("Tipo de agregado nativo inválido.")
    return NativeAggregateContract(domain=domain, aggregate_type=normalized_type)


def _validate_payload(value: Any, *, depth: int = 0) -> None:
    if depth > 12:
        raise NativeAggregatePolicyError("Payload excede a profundidade máxima.")
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str) or not key or len(key) > 160:
                raise NativeAggregatePolicyError("Campo de agregado inválido.")
            if key.casefold().replace("_", "") in _RESERVED_OWNERSHIP_FIELDS:
                raise NativeAggregatePolicyError(
                    f"Campo de ownership não pode ser alterado: {key}."
                )
            _validate_payload(child, depth=depth + 1)
    elif isinstance(value, list):
        for child in value:
            _validate_payload(child, depth=depth + 1)
    elif isinstance(value, float) and not math.isfinite(value):
        raise NativeAggregatePolicyError("Payload contém número não finito.")
    elif value is not None and not isinstance(value, str | int | float | bool):
        raise NativeAggregatePolicyError("Payload contém valor não serializável.")


def _deep_merge(
    current: dict[str, Any],
    patch: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(current)
    for key, value in patch.items():
        previous = merged.get(key)
        if isinstance(previous, dict) and isinstance(value, dict):
            merged[key] = _deep_merge(previous, value)
        else:
            merged[key] = value
    return merged


def _operation_head(operation_id: str) -> str:
    return re.split(r"[._:-]+", str(operation_id).casefold(), maxsplit=1)[0]


def apply_native_aggregate_transition(
    *,
    effect: NativeAggregateEffect,
    operation_id: str,
    patch: dict[str, Any],
    current_state: NativeAggregateState | None,
    current_data: dict[str, Any] | None,
    current_version: int,
    expected_version: int | None,
) -> NativeAggregateTransition:
    """Apply one deterministic state transition without hidden last-write-wins."""
    if effect not in {"read", "upsert", "archive", "event"}:
        raise NativeAggregatePolicyError("Efeito de agregado nativo inválido.")
    if not isinstance(patch, dict):
        raise NativeAggregatePolicyError("Patch do agregado deve ser um objeto.")
    _validate_payload(patch)
    if current_version < 0:
        raise NativeAggregatePolicyError("Versão atual inválida.")
    exists = current_state is not None
    if exists != (current_version > 0):
        raise NativeAggregatePolicyError("Estado e versão atuais são incoerentes.")
    if expected_version is not None and expected_version < 0:
        raise NativeAggregatePolicyError("Versão esperada inválida.")

    if effect == "read":
        if expected_version is not None and expected_version != current_version:
            raise NativeAggregateVersionConflictError(
                f"Versão atual {current_version}; leitura solicitou {expected_version}."
            )
        data = dict(current_data or {})
        return NativeAggregateTransition(
            state=current_state or "active",
            data=data,
            version=current_version,
            data_sha256=canonical_native_data_digest(data),
            created=False,
        )

    if exists:
        if expected_version is None:
            raise NativeAggregateVersionRequiredError(
                "expectedRecordVersion é obrigatório para alterar agregado existente."
            )
        if expected_version != current_version:
            raise NativeAggregateVersionConflictError(
                f"Versão atual {current_version}; alteração solicitou {expected_version}."
            )
    elif expected_version not in {None, 0}:
        raise NativeAggregateVersionConflictError(
            f"Agregado ainda não existe; versão esperada deve ser 0, não {expected_version}."
        )

    if effect == "archive":
        if not exists:
            raise NativeAggregateStateError("Agregado não encontrado para arquivamento.")
        if current_state == "archived":
            raise NativeAggregateStateError("Agregado já está arquivado.")
        data = dict(current_data or {})
        return NativeAggregateTransition(
            state="archived",
            data=data,
            version=current_version + 1,
            data_sha256=canonical_native_data_digest(data),
            created=False,
        )

    if effect == "event" and exists:
        raise NativeAggregateStateError(
            "Eventos nativos são imutáveis e exigem um resourceId ainda não utilizado."
        )

    restoring = _operation_head(operation_id) in _RESTORE_HEADS
    if current_state == "archived" and not restoring:
        raise NativeAggregateStateError(
            "Agregado arquivado exige uma operação explícita de restauração."
        )
    data = _deep_merge(dict(current_data or {}), patch)
    version = current_version + 1
    return NativeAggregateTransition(
        state="active",
        data=data,
        version=version,
        data_sha256=canonical_native_data_digest(data),
        created=not exists,
    )


__all__ = [
    "NativeAggregateContract",
    "NativeAggregatePolicyError",
    "NativeAggregateStateError",
    "NativeAggregateTransition",
    "NativeAggregateVersionConflictError",
    "NativeAggregateVersionRequiredError",
    "apply_native_aggregate_transition",
    "canonical_native_data_digest",
    "native_aggregate_contract",
]
