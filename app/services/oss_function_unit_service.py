"""Read-only, fail-closed access to the pinned FunctionUnit census."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.data.oss_function_unit_contract import DISPOSITION_STATES, REQUIRED_UPSTREAMS

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_LOCK_PATH = _REPOSITORY_ROOT / "docs" / "oss-function-units.lock.json"
_EMBEDDED_UPSTREAM = "rocket-embedded-chat"


class OSSFunctionUnitCatalogError(RuntimeError):
    """Raised when the committed ledger cannot prove its own integrity."""


class OSSFunctionUnitNotFoundError(LookupError):
    """Raised when a FunctionUnit id is absent from the pinned denominator."""


@dataclass(frozen=True)
class OSSFunctionUnitQuery:
    page: int = 1
    page_size: int = 50
    search: str | None = None
    upstream: str | None = None
    category: str | None = None
    disposition: str | None = None
    family: str | None = None
    capability: str | None = None


def _canonical_digest(document: dict[str, Any]) -> str:
    payload = dict(document)
    payload.pop("reproducibility", None)
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@lru_cache(maxsize=2)
def _load_catalog(path: str, modified_ns: int, size: int) -> dict[str, Any]:
    del modified_ns, size
    lock_path = Path(path)
    try:
        document = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise OSSFunctionUnitCatalogError(
            f"Não foi possível ler o ledger FunctionUnit: {error}"
        ) from error
    if not isinstance(document, dict) or document.get("schemaVersion") != 2:
        raise OSSFunctionUnitCatalogError("O ledger FunctionUnit não usa schemaVersion 2.")
    units = document.get("units")
    coverage = document.get("coverage")
    receipt = document.get("reproducibility")
    if not isinstance(units, list) or not isinstance(coverage, dict) or not isinstance(
        receipt, dict
    ):
        raise OSSFunctionUnitCatalogError("O ledger FunctionUnit está incompleto.")
    if coverage.get("denominator") != len(units):
        raise OSSFunctionUnitCatalogError("O denominador diverge da lista de FunctionUnits.")
    recorded_digest = receipt.get("payloadSha256")
    if recorded_digest != _canonical_digest(document):
        raise OSSFunctionUnitCatalogError("O digest do ledger FunctionUnit é inválido.")
    ids = [unit.get("id") for unit in units if isinstance(unit, dict)]
    if len(ids) != len(units) or len(set(ids)) != len(ids):
        raise OSSFunctionUnitCatalogError("O ledger FunctionUnit contém IDs inválidos.")
    return document


def function_unit_catalog() -> dict[str, Any]:
    stat = _LOCK_PATH.stat()
    return _load_catalog(str(_LOCK_PATH), stat.st_mtime_ns, stat.st_size)


def _searchable(unit: dict[str, Any]) -> str:
    source = unit["source"]
    mapping = unit["klmMapping"]
    return " ".join(
        str(value)
        for value in (
            unit["id"],
            unit["upstream"],
            unit["category"],
            unit["name"],
            source["family"],
            source["path"],
            source["route"],
            source["handler"],
            mapping.get("capabilityId"),
            mapping.get("canonicalEntity"),
        )
        if value
    ).casefold()


def _matches(unit: dict[str, Any], query: OSSFunctionUnitQuery) -> bool:
    source = unit["source"]
    return (
        (query.upstream is None or unit["upstream"] == query.upstream)
        and (query.category is None or unit["category"] == query.category)
        and (
            query.disposition is None
            or unit["disposition"]["state"] == query.disposition
        )
        and (query.family is None or source["family"] == query.family)
        and (
            query.capability is None
            or unit["klmMapping"].get("capabilityId") == query.capability
        )
        and (
            not query.search
            or query.search.strip().casefold() in _searchable(unit)
        )
    )


def _summary(document: dict[str, Any]) -> dict[str, Any]:
    coverage = document["coverage"]
    dispositions = dict(coverage["byDisposition"])
    denominator = int(coverage["denominator"])
    blocked = int(dispositions.get("blocked_license", 0)) + int(
        dispositions.get("blocked_proprietary", 0)
    )
    verified = int(dispositions.get("native_verified", 0))
    return {
        "denominator": denominator,
        "licensed": denominator - blocked,
        "blocked": blocked,
        "verified": verified,
        "remaining": denominator - blocked - verified,
        "byUpstream": dict(coverage["byUpstream"]),
        "byCategory": dict(coverage["byCategory"]),
        "byDisposition": dispositions,
        "allPinsVerified": bool(coverage["allPinsVerified"]),
    }


def _filter_options(units: list[dict[str, Any]]) -> dict[str, list[str]]:
    return {
        "upstreams": sorted({unit["upstream"] for unit in units}),
        "categories": sorted({unit["category"] for unit in units}),
        "dispositions": [
            state
            for state in DISPOSITION_STATES
            if any(unit["disposition"]["state"] == state for unit in units)
        ],
        "families": sorted({unit["source"]["family"] for unit in units}),
        "capabilities": sorted(
            {
                str(unit["klmMapping"]["capabilityId"])
                for unit in units
                if unit["klmMapping"].get("capabilityId")
            }
        ),
    }


def list_function_units(query: OSSFunctionUnitQuery) -> dict[str, Any]:
    if query.page < 1 or not 1 <= query.page_size <= 200:
        raise ValueError("Paginação FunctionUnit inválida.")
    if query.upstream is not None and query.upstream not in {
        *REQUIRED_UPSTREAMS,
        _EMBEDDED_UPSTREAM,
    }:
        raise ValueError("Upstream FunctionUnit inválido.")
    if query.disposition is not None and query.disposition not in DISPOSITION_STATES:
        raise ValueError("Estado FunctionUnit inválido.")
    if query.capability is not None and not query.capability.strip():
        raise ValueError("Capacidade FunctionUnit inválida.")
    document = function_unit_catalog()
    units = document["units"]
    filtered = [unit for unit in units if _matches(unit, query)]
    total = len(filtered)
    start = (query.page - 1) * query.page_size
    items = filtered[start : start + query.page_size]
    return {
        "schemaVersion": 2,
        "scope": "pinned-community-function-units",
        "digest": document["reproducibility"]["payloadSha256"],
        "summary": _summary(document),
        "filters": _filter_options(units),
        "page": query.page,
        "pageSize": query.page_size,
        "total": total,
        "pages": math.ceil(total / query.page_size) if total else 0,
        "items": items,
    }


def get_function_unit(unit_id: str) -> dict[str, Any]:
    if not unit_id or len(unit_id) > 240:
        raise OSSFunctionUnitNotFoundError(unit_id)
    document = function_unit_catalog()
    for unit in document["units"]:
        if unit["id"] == unit_id:
            return unit
    raise OSSFunctionUnitNotFoundError(unit_id)


__all__ = [
    "OSSFunctionUnitCatalogError",
    "OSSFunctionUnitNotFoundError",
    "OSSFunctionUnitQuery",
    "function_unit_catalog",
    "get_function_unit",
    "list_function_units",
]
