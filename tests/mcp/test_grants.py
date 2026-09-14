from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.mcp.errors import McpToolError
from app.mcp.grants import (
    GrantClaims,
    build_signed_grant,
    database_fingerprint,
    load_signed_grant,
    sign_claims,
)


def claims(**overrides) -> GrantClaims:
    now = datetime.now(UTC)
    values = {
        "grant_id": uuid4(),
        "profile": "test",
        "workspace_id": uuid4(),
        "user_id": uuid4(),
        "membership_id": uuid4(),
        "capabilities": ("crm.read",),
        "issued_at": now - timedelta(minutes=1),
        "expires_at": now + timedelta(hours=1),
        "database_fingerprint": database_fingerprint("sqlite:///test.db"),
    }
    values.update(overrides)
    return GrantClaims(**values)


def write_private(path: Path, content: bytes) -> None:
    path.write_bytes(content)
    os.chmod(path, 0o600)


def write_grant(tmp_path: Path, item: GrantClaims, key: bytes = b"k" * 32):
    key_file = tmp_path / "key"
    grant_file = tmp_path / "grant.json"
    write_private(key_file, key)
    write_private(grant_file, build_signed_grant(item, key).model_dump_json().encode())
    return grant_file, key_file


def test_loads_valid_private_signed_grant(tmp_path: Path):
    item = claims()
    grant_file, key_file = write_grant(tmp_path, item)
    loaded = load_signed_grant(
        grant_file,
        key_file,
        expected_database_fingerprint=item.database_fingerprint,
    )
    assert loaded == item


def test_rejects_tampered_signature(tmp_path: Path):
    item = claims()
    grant_file, key_file = write_grant(tmp_path, item)
    payload = json.loads(grant_file.read_text())
    payload["claims"]["profile"] = "tampered"
    write_private(grant_file, json.dumps(payload).encode())
    with pytest.raises(McpToolError, match="signature"):
        load_signed_grant(
            grant_file,
            key_file,
            expected_database_fingerprint=item.database_fingerprint,
        )


def test_rejects_world_readable_grant(tmp_path: Path):
    item = claims()
    grant_file, key_file = write_grant(tmp_path, item)
    os.chmod(grant_file, 0o644)
    with pytest.raises(McpToolError, match="0600"):
        load_signed_grant(
            grant_file,
            key_file,
            expected_database_fingerprint=item.database_fingerprint,
        )


def test_rejects_symlink_grant(tmp_path: Path):
    item = claims()
    grant_file, key_file = write_grant(tmp_path, item)
    link = tmp_path / "link.json"
    link.symlink_to(grant_file)
    with pytest.raises(McpToolError, match="non-symlink"):
        load_signed_grant(
            link,
            key_file,
            expected_database_fingerprint=item.database_fingerprint,
        )


def test_rejects_expired_grant(tmp_path: Path):
    item = claims(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    grant_file, key_file = write_grant(tmp_path, item)
    with pytest.raises(McpToolError) as error:
        load_signed_grant(
            grant_file,
            key_file,
            expected_database_fingerprint=item.database_fingerprint,
        )
    assert error.value.code == "MCP_GRANT_EXPIRED"


def test_rejects_grant_for_other_database(tmp_path: Path):
    item = claims()
    grant_file, key_file = write_grant(tmp_path, item)
    with pytest.raises(McpToolError, match="another database"):
        load_signed_grant(
            grant_file,
            key_file,
            expected_database_fingerprint=database_fingerprint("sqlite:///other.db"),
        )


def test_rejects_short_signing_key():
    with pytest.raises(ValueError, match="32 bytes"):
        sign_claims(claims(), b"short")


def test_rejects_unknown_capability():
    with pytest.raises(ValidationError, match="Unsupported grant capabilities"):
        claims(capabilities=("crm.read", "root.everything"))


def test_rejects_grant_without_read_capability():
    with pytest.raises(ValidationError, match="requires crm.read"):
        claims(capabilities=("crm.write",))
