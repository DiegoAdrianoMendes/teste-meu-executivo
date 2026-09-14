"""Operator-issued local grants used as the MCP stdio trust boundary."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import stat
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.mcp.errors import McpToolError

GRANT_VERSION = 1
ALLOWED_CAPABILITIES = frozenset(
    {
        "crm.read",
        "crm.sensitive.read",
        "crm.write",
        "handoff.request",
        "approval.request",
    }
)


class GrantClaims(BaseModel):
    """Signed identity and least-privilege capabilities selected by an operator."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = GRANT_VERSION
    grant_id: UUID
    profile: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    workspace_id: UUID
    user_id: UUID
    membership_id: UUID
    capabilities: tuple[str, ...]
    issued_at: datetime
    expires_at: datetime
    database_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator("capabilities")
    @classmethod
    def validate_capabilities(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(dict.fromkeys(value))
        unknown = sorted(set(normalized) - ALLOWED_CAPABILITIES)
        if unknown:
            raise ValueError(f"Unsupported grant capabilities: {', '.join(unknown)}")
        if "crm.sensitive.read" in normalized and "crm.read" not in normalized:
            raise ValueError("crm.sensitive.read requires crm.read")
        if "crm.read" not in normalized:
            raise ValueError("Every MCP grant requires crm.read")
        return normalized

    @field_validator("issued_at", "expires_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Grant timestamps must include a timezone")
        return value.astimezone(UTC)


class SignedGrant(BaseModel):
    """On-disk grant envelope. The signing key is kept in a separate 0600 file."""

    model_config = ConfigDict(extra="forbid")

    claims: GrantClaims
    signature: str = Field(pattern=r"^[a-f0-9]{64}$")


def database_fingerprint(database_url: str) -> str:
    """Bind a local grant to one configured database without exposing its URL."""

    return hashlib.sha256(database_url.encode("utf-8")).hexdigest()


def _canonical_claims(claims: GrantClaims | dict[str, Any]) -> bytes:
    if isinstance(claims, GrantClaims):
        payload = claims.model_dump(mode="json")
    else:
        payload = claims
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "utf-8"
    )


def sign_claims(claims: GrantClaims, key: bytes) -> str:
    if len(key) < 32:
        raise ValueError("The MCP signing key must contain at least 32 bytes")
    return hmac.new(key, _canonical_claims(claims), hashlib.sha256).hexdigest()


def build_signed_grant(claims: GrantClaims, key: bytes) -> SignedGrant:
    return SignedGrant(claims=claims, signature=sign_claims(claims, key))


def _assert_private_regular_file(path: Path, label: str) -> None:
    try:
        file_stat = path.lstat()
    except FileNotFoundError as exc:
        raise McpToolError("MCP_GRANT_INVALID", f"{label} not found.") from exc
    if stat.S_ISLNK(file_stat.st_mode) or not stat.S_ISREG(file_stat.st_mode):
        raise McpToolError("MCP_GRANT_INVALID", f"{label} must be a regular non-symlink file.")
    if hasattr(os, "getuid") and file_stat.st_uid != os.getuid():
        raise McpToolError("MCP_GRANT_INVALID", f"{label} must be owned by the current user.")
    if stat.S_IMODE(file_stat.st_mode) & 0o077:
        raise McpToolError("MCP_GRANT_INVALID", f"{label} permissions must be 0600 or stricter.")


def load_signed_grant(
    grant_file: str | Path,
    key_file: str | Path,
    *,
    expected_database_fingerprint: str,
    now: datetime | None = None,
) -> GrantClaims:
    """Load and verify a local grant without accepting identity from tool arguments."""

    grant_path = Path(grant_file).expanduser()
    key_path = Path(key_file).expanduser()
    if not grant_path.is_absolute():
        grant_path = Path.cwd() / grant_path
    if not key_path.is_absolute():
        key_path = Path.cwd() / key_path
    _assert_private_regular_file(grant_path, "MCP grant")
    _assert_private_regular_file(key_path, "MCP signing key")

    try:
        envelope = SignedGrant.model_validate_json(grant_path.read_text(encoding="utf-8"))
        key = key_path.read_bytes()
    except (OSError, ValueError) as exc:
        raise McpToolError("MCP_GRANT_INVALID", "The MCP grant could not be parsed.") from exc

    try:
        expected_signature = sign_claims(envelope.claims, key)
    except ValueError as exc:
        raise McpToolError("MCP_GRANT_INVALID", "The MCP signing key is invalid.") from exc
    if not hmac.compare_digest(envelope.signature, expected_signature):
        raise McpToolError("MCP_GRANT_INVALID", "The MCP grant signature is invalid.")

    current_time = (now or datetime.now(UTC)).astimezone(UTC)
    if envelope.claims.version != GRANT_VERSION:
        raise McpToolError("MCP_GRANT_INVALID", "Unsupported MCP grant version.")
    if envelope.claims.issued_at > current_time:
        raise McpToolError("MCP_GRANT_INVALID", "The MCP grant is not active yet.")
    if envelope.claims.expires_at <= current_time:
        raise McpToolError("MCP_GRANT_EXPIRED", "The MCP grant has expired.")
    if not hmac.compare_digest(
        envelope.claims.database_fingerprint,
        expected_database_fingerprint,
    ):
        raise McpToolError("MCP_GRANT_INVALID", "The MCP grant belongs to another database.")
    return envelope.claims


def validate_requested_capabilities(capabilities: Iterable[str]) -> tuple[str, ...]:
    requested = tuple(dict.fromkeys(capabilities))
    unknown = sorted(set(requested) - ALLOWED_CAPABILITIES)
    if unknown:
        raise ValueError(f"Unsupported capabilities: {', '.join(unknown)}")
    if "crm.sensitive.read" in requested and "crm.read" not in requested:
        raise ValueError("crm.sensitive.read requires crm.read")
    return requested
