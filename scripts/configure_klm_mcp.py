#!/usr/bin/env python3
"""Issue a short-lived, operator-selected local MCP grant for KLM Hub."""

# ruff: noqa: E402 - local-only environment gates must be set before app imports.

from __future__ import annotations

import argparse
import asyncio
import os
import secrets
import stat
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

LOCAL_DATABASE_URL = f"sqlite+aiosqlite:///{ROOT / '.local' / 'klm-hub.db'}"
os.environ.setdefault("DATABASE_URL", os.environ.get("KLM_MCP_DATABASE_URL", LOCAL_DATABASE_URL))
os.environ.setdefault("KLM_RUNTIME_MODE", "local_simulation")
os.environ.setdefault("KLM_EXTERNAL_ACTIONS_ENABLED", "false")
os.environ.setdefault("KLM_AWS_ENABLED", "false")
os.environ.setdefault("KLM_ONLINE_APPROVED", "false")

from sqlalchemy import func, select

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.db.tenant import set_workspace_context
from app.mcp.grants import (
    GrantClaims,
    build_signed_grant,
    database_fingerprint,
    load_signed_grant,
    validate_requested_capabilities,
)
from app.mcp.identity import capabilities_allowed_by_permissions
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceStatus
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Cria um grant local, tenant-safe e com prazo para o MCP do KLM Hub."
    )
    parser.add_argument("--workspace-id", required=True, type=UUID)
    identity = parser.add_mutually_exclusive_group(required=True)
    identity.add_argument("--user-id", type=UUID)
    identity.add_argument("--user-email")
    parser.add_argument("--profile", default="local-owner")
    parser.add_argument(
        "--expires-hours",
        type=int,
        default=24,
        choices=range(1, 721),
        metavar="1..720",
    )
    parser.add_argument("--allow-sensitive", action="store_true")
    parser.add_argument("--allow-write", action="store_true")
    parser.add_argument("--allow-handoff", action="store_true")
    parser.add_argument("--allow-approval", action="store_true")
    parser.add_argument("--grant-file", type=Path)
    parser.add_argument("--key-file", type=Path)
    parser.add_argument("--replace", action="store_true", help="Substitui um grant existente.")
    parser.add_argument("--dry-run", action="store_true", help="Valida sem gravar credenciais.")
    return parser


def _requested_capabilities(args: argparse.Namespace) -> tuple[str, ...]:
    capabilities = ["crm.read"]
    if args.allow_sensitive:
        capabilities.append("crm.sensitive.read")
    if args.allow_write:
        capabilities.append("crm.write")
    if args.allow_handoff:
        capabilities.append("handoff.request")
    if args.allow_approval:
        capabilities.append("approval.request")
    return validate_requested_capabilities(capabilities)


def _private_file_ok(path: Path) -> bool:
    file_stat = path.lstat()
    return (
        stat.S_ISREG(file_stat.st_mode)
        and not stat.S_ISLNK(file_stat.st_mode)
        and (not hasattr(os, "getuid") or file_stat.st_uid == os.getuid())
        and not (stat.S_IMODE(file_stat.st_mode) & 0o077)
    )


def _load_or_create_key(path: Path) -> bytes:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    if path.exists():
        if not _private_file_ok(path):
            raise RuntimeError("O arquivo de chave deve ser regular, do usuário atual e modo 0600.")
        key = path.read_bytes()
        if len(key) < 32:
            raise RuntimeError("A chave MCP existente é curta demais.")
        return key
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    key = secrets.token_bytes(32)
    try:
        os.write(descriptor, key)
        os.fsync(descriptor)
        os.fchmod(descriptor, 0o600)
    finally:
        os.close(descriptor)
    return key


def _atomic_write_private(path: Path, content: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        os.chmod(path, 0o600)
    finally:
        temporary_path.unlink(missing_ok=True)


async def _resolve_identity(args: argparse.Namespace):
    async with AsyncSessionLocal() as db:
        await set_workspace_context(db, args.workspace_id)
        conditions = [
            WorkspaceMembership.workspace_id == args.workspace_id,
            WorkspaceMembership.status == MembershipStatus.active,
            Workspace.id == args.workspace_id,
            Workspace.status == WorkspaceStatus.active,
            Workspace.is_active.is_(True),
            Workspace.deleted_at.is_(None),
            User.is_active.is_(True),
            User.deleted_at.is_(None),
        ]
        if args.user_id:
            conditions.append(WorkspaceMembership.user_id == args.user_id)
        else:
            conditions.append(func.lower(User.email) == args.user_email.strip().lower())
        row = (
            await db.execute(
                select(WorkspaceMembership, User, Workspace)
                .join(User, User.id == WorkspaceMembership.user_id)
                .join(Workspace, Workspace.id == WorkspaceMembership.workspace_id)
                .where(*conditions)
            )
        ).one_or_none()
        if row is None:
            raise RuntimeError("Usuário, workspace ou membership ativo não encontrado.")
        return row


async def _run(args: argparse.Namespace) -> int:
    requested = _requested_capabilities(args)
    membership, user, workspace = await _resolve_identity(args)
    allowed = capabilities_allowed_by_permissions(
        membership.role,
        membership.permission_grants,
        membership.permission_denials,
    )
    denied = sorted(set(requested) - allowed)
    if denied:
        raise RuntimeError(
            "As permissões atuais não permitem emitir: " + ", ".join(denied)
        )

    grant_file = (
        args.grant_file or ROOT / ".local" / "klm-mcp" / f"{args.profile}.grant.json"
    ).expanduser().resolve()
    key_file = (
        args.key_file or ROOT / ".local" / "klm-mcp" / "signing.key"
    ).expanduser().resolve()
    if grant_file.exists() and not args.replace and not args.dry_run:
        raise RuntimeError("O grant já existe; use --replace para rotacioná-lo conscientemente.")

    now = datetime.now(UTC)
    claims = GrantClaims(
        grant_id=uuid4(),
        profile=args.profile,
        workspace_id=workspace.id,
        user_id=user.id,
        membership_id=membership.id,
        capabilities=requested,
        issued_at=now,
        expires_at=now + timedelta(hours=args.expires_hours),
        database_fingerprint=database_fingerprint(settings.DATABASE_URL),
    )

    print(f"Workspace: {workspace.name} ({workspace.id})")
    print(f"Usuário: {user.name} ({user.id})")
    print(f"Papel: {membership.role.value}")
    print(f"Capacidades: {', '.join(requested)}")
    print(f"Expira em: {claims.expires_at.isoformat()}")
    if args.dry_run:
        print("Dry-run concluído; nenhum arquivo foi criado.")
        return 0

    key = _load_or_create_key(key_file)
    envelope = build_signed_grant(claims, key)
    _atomic_write_private(grant_file, envelope.model_dump_json(indent=2))
    load_signed_grant(
        grant_file,
        key_file,
        expected_database_fingerprint=database_fingerprint(settings.DATABASE_URL),
    )

    print(f"Grant: {grant_file}")
    print(f"Chave: {key_file}")
    print("Configure o cliente MCP com:")
    print(f"  command: {ROOT / 'scripts' / 'run_klm_mcp.sh'}")
    print(f"  KLM_MCP_GRANT_FILE={grant_file}")
    print(f"  KLM_MCP_KEY_FILE={key_file}")
    return 0


def main() -> None:
    args = _parser().parse_args()
    try:
        raise SystemExit(asyncio.run(_run(args)))
    except RuntimeError as exc:
        print(f"Configuração recusada: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
