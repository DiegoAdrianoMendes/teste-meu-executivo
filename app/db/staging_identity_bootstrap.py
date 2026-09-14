"""Idempotent, non-local identity bootstrap for a controlled AWS environment.

This module is deliberately separate from the local simulation seed.  It is
safe to run repeatedly from an ECS one-shot task with the payload supplied by
Secrets Manager through ``KLM_IDENTITY_BOOTSTRAP_PAYLOAD``.  The payload is
never logged and passwords are only ever passed to the password hasher.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
from datetime import UTC, datetime
from typing import Any

import bcrypt
from pydantic import (
    BaseModel,
    ConfigDict,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)
from sqlalchemy import select
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.platform_control import set_platform_control_context
from app.models.commercial_catalog import (
    CommercialAdjustmentSource,
    CommercialAdjustmentType,
    CommercialContractStatus,
    CommercialPortability,
    WorkspaceCommercialContract,
)
from app.models.entitlement import EntitlementKey
from app.models.user import AgentType, User, UserRole
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.models.workspace_membership import MembershipStatus, WorkspaceMembership, WorkspaceRole
from app.schemas.commercial_catalog import (
    CommercialAdjustmentCreateRequest,
    WorkspaceContractCreateRequest,
    WorkspaceContractLineWrite,
)


class IdentityPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    email: str
    password: SecretStr

    @field_validator("email")
    @classmethod
    def canonical_email(cls, value: str) -> str:
        value = value.strip().lower()
        if "@" not in value or value.startswith("@") or value.endswith("@"):
            raise ValueError("email inválido")
        return value


class StagingIdentityPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    root: IdentityPayload
    platform_admins: list[IdentityPayload]
    technical_staff: list[IdentityPayload]
    pilot_workspace: IdentityPayload
    rotate_temporary_passwords: bool = False
    workspace_name: str = "KLM Piloto"
    workspace_slug: str = "klm-piloto"

    @field_validator("platform_admins")
    @classmethod
    def exactly_one_admin(cls, value: list[IdentityPayload]) -> list[IdentityPayload]:
        if len(value) != 1:
            raise ValueError("platform_admins deve conter exatamente um usuário")
        return value

    @field_validator("technical_staff")
    @classmethod
    def exactly_one_technical_staff(cls, value: list[IdentityPayload]) -> list[IdentityPayload]:
        if len(value) != 1:
            raise ValueError("technical_staff deve conter exatamente um usuário")
        return value

    @field_validator("workspace_slug")
    @classmethod
    def slug(cls, value: str) -> str:
        value = value.strip().lower()
        if not value or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-" for char in value):
            raise ValueError("workspace_slug inválido")
        return value

    @model_validator(mode="after")
    def fixed_pilot_provisional_password(self) -> StagingIdentityPayload:
        if not self.rotate_temporary_passwords:
            return self
        identities = [
            self.root,
            *self.platform_admins,
            *self.technical_staff,
            self.pilot_workspace,
        ]
        if any(item.password.get_secret_value() != "12345" for item in identities):
            raise ValueError("a rotação controlada do piloto exige a senha provisória aprovada")
        return self


class StagingBootstrapError(RuntimeError):
    """Raised when an existing identity cannot be safely converged."""


def _asyncpg_connection(database_url: str) -> tuple[URL, dict[str, Any]]:
    """Translate the migration-style SSL query into asyncpg connect args."""
    url = make_url(database_url)
    sslmode = url.query.get("sslmode")
    if sslmode is None:
        return url, {}
    if sslmode != "require":
        raise StagingBootstrapError("DATABASE_URL sslmode não suportado no bootstrap")
    return url.difference_update_query(["sslmode"]), {"ssl": "require"}


async def _hash_password(password: str) -> str:
    """Hash a bootstrap password without importing JWT/runtime settings."""

    def hash_sync() -> str:
        return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    return await asyncio.to_thread(hash_sync)


async def _password_matches(password: str, hashed_password: str | None) -> bool:
    if not hashed_password:
        return False
    return await asyncio.to_thread(
        bcrypt.checkpw,
        password.encode("utf-8"),
        hashed_password.encode("utf-8"),
    )


def _deployment_environment() -> str:
    """Return the explicit AWS environment without inventing production."""
    environment = os.environ.get("ENVIRONMENT", "staging").strip().lower()
    if environment not in {"staging", "production"}:
        raise StagingBootstrapError("ENVIRONMENT inválido para bootstrap de identidade")
    return environment


def _load_commercial_services() -> tuple[type[Any], type[Any]]:
    """Load application services with an isolated one-shot signing key.

    Commercial services import the global application settings even though
    this bootstrap never issues or verifies JWTs.  The migration task must not
    receive the runtime signing secret, so a random process-only value satisfies
    that unrelated import contract and disappears when the one-shot task exits.
    """
    # Always isolate the one-shot process even if a future task definition
    # accidentally inherits runtime credentials.
    os.environ["SECRET_KEY"] = secrets.token_urlsafe(48)
    os.environ["INTEGRATION_SECRET_ENCRYPTION_KEY"] = secrets.token_urlsafe(48)
    environment = _deployment_environment()
    app_host = (
        "app.meuexecutivo.com"
        if environment == "production"
        else "staging-app.meuexecutivo.com"
    )
    blackbox_host = (
        "blackbox.meuexecutivo.com"
        if environment == "production"
        else "staging-blackbox.meuexecutivo.com"
    )
    os.environ.setdefault("FRONTEND_URL", f"https://{app_host}")
    os.environ.setdefault(
        "BLACKBOX_FRONTEND_URL", f"https://{blackbox_host}"
    )
    os.environ.setdefault(
        "CORS_ORIGINS",
        f'["https://{app_host}","https://{blackbox_host}"]',
    )
    from app.services.commercial_catalog_service import CommercialCatalogService
    from app.services.commercial_legacy_bridge_service import CanonicalContractCommand

    return CommercialCatalogService, CanonicalContractCommand


def load_payload(raw: str | None = None) -> StagingIdentityPayload:
    """Parse the JSON secret without ever including its content in exceptions."""
    raw = raw if raw is not None else os.environ.get("KLM_IDENTITY_BOOTSTRAP_PAYLOAD")
    if not raw:
        raise StagingBootstrapError("KLM_IDENTITY_BOOTSTRAP_PAYLOAD não configurado")
    try:
        return StagingIdentityPayload.model_validate_json(raw)
    except (ValidationError, ValueError, json.JSONDecodeError):
        # Pydantic includes rejected input values in ValidationError details.
        # Suppress the cause so a CLI traceback cannot copy bootstrap secrets
        # into CloudWatch when the payload is malformed.
        raise StagingBootstrapError("payload de identidade inválido") from None


def _initials(name: str) -> str:
    parts = [part for part in name.strip().split() if part]
    return "".join(part[0] for part in parts[:2]).upper() or "ME"


def _validate_target_domains(payload: StagingIdentityPayload) -> None:
    expected = {
        "kleistfilho@gmail.com": ("gmail.com",),
        "kleist.adv@gmail.com": ("gmail.com",),
        "kleist@meuexecutivo.com": ("meuexecutivo.com",),
        "diegoadrianomendes@gmail.com": ("gmail.com",),
    }
    identities = [
        payload.root,
        *payload.platform_admins,
        *payload.technical_staff,
        payload.pilot_workspace,
    ]
    emails = [item.email for item in identities]
    if len(set(emails)) != len(emails):
        raise StagingBootstrapError("emails de bootstrap devem ser únicos")
    for item in identities:
        domain = item.email.rsplit("@", 1)[1]
        allowed = expected.get(item.email)
        if allowed is None or domain not in allowed:
            raise StagingBootstrapError("email fora do conjunto de identidades autorizadas")


async def _upsert_identity(
    db: AsyncSession,
    item: IdentityPayload,
    *,
    role_package: str | None,
    platform_identity: bool,
    rotate_temporary_password: bool,
) -> User:
    user = await db.scalar(select(User).where(User.email == item.email).with_for_update())
    if user is None:
        user = User(
            name=item.name,
            email=item.email,
            hashed_password=await _hash_password(item.password.get_secret_value()),
            agent_type=AgentType.human,
            role=UserRole.admin,
            initials=_initials(item.name),
            is_active=True,
            email_verified_at=datetime.now(UTC),
            password_change_required=True,
            temporary_password_issued_at=datetime.now(UTC),
            is_internal_staff=platform_identity,
            is_platform_admin=role_package in {"platform_owner", "platform_admin"},
            platform_role_package=role_package,
            platform_access_active=platform_identity,
            platform_permission_grants=[],
            platform_permission_denials=[],
        )
        db.add(user)
        await db.flush()
    else:
        if platform_identity:
            memberships = list(
                await db.scalars(
                    select(WorkspaceMembership).where(WorkspaceMembership.user_id == user.id)
                )
            )
            if memberships:
                raise StagingBootstrapError(f"{role_package} existente possui vínculo de workspace")
            if user.platform_role_package not in (None, role_package):
                raise StagingBootstrapError("identidade existente possui papel incompatível")
        elif (
            user.is_internal_staff
            or user.is_platform_admin
            or user.platform_access_active
            or user.platform_role_package is not None
        ):
            raise StagingBootstrapError("identidade piloto existente possui acesso de plataforma")
        user.name = item.name
        user.is_active = True
        user.email_verified_at = user.email_verified_at or datetime.now(UTC)
        user.is_internal_staff = platform_identity
        user.is_platform_admin = role_package in {"platform_owner", "platform_admin"}
        user.platform_role_package = role_package
        user.platform_access_active = platform_identity
        user.role = UserRole.admin
        raw_password = item.password.get_secret_value()
        if rotate_temporary_password:
            if not await _password_matches(raw_password, user.hashed_password):
                user.hashed_password = await _hash_password(raw_password)
            user.password_change_required = True
            user.temporary_password_issued_at = (
                user.temporary_password_issued_at or datetime.now(UTC)
            )
    return user


async def bootstrap_staging_identities(
    db: AsyncSession, payload: StagingIdentityPayload
) -> dict[str, Any]:
    """Converge platform identities, pilot workspace and canonical Evolution contract."""
    environment = _deployment_environment()
    catalog_service_cls, contract_command_cls = _load_commercial_services()
    _validate_target_domains(payload)
    root = await _upsert_identity(
        db,
        payload.root,
        role_package="platform_owner",
        platform_identity=True,
        rotate_temporary_password=payload.rotate_temporary_passwords,
    )
    await set_platform_control_context(
        db,
        actor_user_id=root.id,
        module="plans",
        action="publish",
        is_platform_root=True,
    )
    admins = [
        await _upsert_identity(
            db,
            item,
            role_package="platform_admin",
            platform_identity=True,
            rotate_temporary_password=payload.rotate_temporary_passwords,
        )
        for item in payload.platform_admins
    ]
    technical_staff = [
        await _upsert_identity(
            db,
            item,
            role_package="technical_support",
            platform_identity=True,
            rotate_temporary_password=payload.rotate_temporary_passwords,
        )
        for item in payload.technical_staff
    ]
    owner = await _upsert_identity(
        db,
        payload.pilot_workspace,
        role_package=None,
        platform_identity=False,
        rotate_temporary_password=payload.rotate_temporary_passwords,
    )
    # Rotation is deliberately limited to the identities explicitly named in
    # this sealed payload. Never reset every employee who happens to have
    # platform access: that would turn a pilot bootstrap into an account-wide
    # credential operation.
    rotated_platform_users = (
        1 + len(admins) + len(technical_staff)
        if payload.rotate_temporary_passwords
        else 0
    )

    other_roots = list(
        await db.scalars(
            select(User).where(
                User.platform_role_package == "platform_owner",
                User.id != root.id,
            )
        )
    )
    if other_roots:
        raise StagingBootstrapError("já existe outro platform_owner")

    workspace = await db.scalar(
        select(Workspace).where(Workspace.slug == payload.workspace_slug).with_for_update()
    )
    if workspace is None:
        workspace = Workspace(
            name=payload.workspace_name,
            slug=payload.workspace_slug,
            status=WorkspaceStatus.active,
            setup_mode=WorkspaceSetupMode.assisted,
            is_active=True,
            onboarding_completed=False,
            settings={
                "runtime": environment,
                "externalActionsEnabled": False,
                "awsEnabled": True,
            },
        )
        db.add(workspace)
        await db.flush()
    elif workspace.name != payload.workspace_name:
        workspace.name = payload.workspace_name
    foreign_memberships = list(
        await db.scalars(
            select(WorkspaceMembership).where(
                WorkspaceMembership.user_id == owner.id,
                WorkspaceMembership.workspace_id != workspace.id,
            )
        )
    )
    if foreign_memberships:
        raise StagingBootstrapError("identidade piloto possui vínculo com outro workspace")
    membership = await db.scalar(
        select(WorkspaceMembership)
        .where(
            WorkspaceMembership.workspace_id == workspace.id,
            WorkspaceMembership.user_id == owner.id,
        )
        .with_for_update()
    )
    if membership is None:
        membership = WorkspaceMembership(
            workspace_id=workspace.id,
            user_id=owner.id,
            role=WorkspaceRole.workspace_owner,
            status=MembershipStatus.active,
            is_default=True,
            access_package="crm_evolution",
        )
        db.add(membership)
    else:
        membership.role = WorkspaceRole.workspace_owner
        membership.status = MembershipStatus.active
        membership.is_default = True
        membership.access_package = "crm_evolution"
    await db.flush()

    catalog, _ = await catalog_service_cls(db).bootstrap_default_catalog(
        actor_user_id=root.id,
        idempotency_key=f"{environment}-identity-bootstrap:catalog:v1",
    )
    evolution = next((item for item in catalog if item.sku == "crm_evolution"), None)
    legal = next((item for item in catalog if item.sku == "legal_360"), None)
    if evolution is None or legal is None:
        raise StagingBootstrapError("catálogo não publicou crm_evolution e legal_360")
    legal_limits = {
        EntitlementKey.users: 25,
        EntitlementKey.legal_areas: 25,
        EntitlementKey.storage_bytes: 100 * 1024**3,
        EntitlementKey.monitored_processes: 1_000,
        EntitlementKey.legal_documents: 20_000,
        EntitlementKey.protocol_folders: 5_000,
    }
    legal_overrides = {
        key: legal.snapshot.entitlements[key].model_copy(
            update={"enabled": True, "limit_quantity": limit}
        )
        for key, limit in legal_limits.items()
    }
    existing_contract = await db.scalar(
        select(WorkspaceCommercialContract).where(
            WorkspaceCommercialContract.workspace_id == workspace.id
        )
    )
    contract_command = contract_command_cls(db, control_plane=True)
    if existing_contract is None:
        contract, _ = await contract_command.create_contract(
            payload=WorkspaceContractCreateRequest(
                workspace_id=workspace.id,
                lines=[
                    WorkspaceContractLineWrite(
                        sku="crm_evolution",
                        policy_version_id=evolution.id,
                        contracted_price_cents=0,
                    ),
                    WorkspaceContractLineWrite(
                        sku="legal_360",
                        policy_version_id=legal.id,
                        # Quote-only products require a positive contracted price.
                        # A separately persisted adjustment below makes the pilot
                        # free without weakening that catalog invariant.
                        contracted_price_cents=1,
                        entitlement_overrides=legal_overrides,
                    ),
                ],
                status=CommercialContractStatus.active,
                reason=f"Bootstrap idempotente do workspace piloto de {environment}.",
            ),
            actor_user_id=root.id,
            idempotency_key=f"{environment}-identity-bootstrap:contract:{workspace.id}",
        )
    else:
        contract = await catalog_service_cls(db).get_contract(existing_contract.id)

    legal_line = next((line for line in contract.lines if line.sku == "legal_360"), None)
    if legal_line is None or legal_line.id is None:
        raise StagingBootstrapError("contrato piloto não contém linha legal_360 ativa")
    await contract_command.add_adjustment(
        contract_id=contract.id,
        payload=CommercialAdjustmentCreateRequest(
            contract_line_id=legal_line.id,
            type=CommercialAdjustmentType.fixed_discount,
            value=1,
            starts_at=datetime(2026, 8, 5, tzinfo=UTC),
            stack_group=f"{environment}-pilot-full-courtesy",
            stackable=False,
            portability=CommercialPortability.preserve,
            reason=(
                "Cortesia integral e permanente do escritório piloto KLM em "
                f"{environment}."
            ),
            source=CommercialAdjustmentSource.manual,
        ),
        actor_user_id=root.id,
        idempotency_key=f"{environment}-identity-bootstrap:courtesy:{workspace.id}",
    )
    await db.flush()
    return {
        "root_user_id": str(root.id),
        "platform_admin_count": len(admins),
        "technical_staff_count": len(technical_staff),
        "rotated_platform_user_count": rotated_platform_users,
        "workspace_id": str(workspace.id),
        "workspace_slug": workspace.slug,
        "contract": ["crm_evolution", "legal_360"],
    }


async def run_from_environment() -> dict[str, Any]:
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        raise StagingBootstrapError("DATABASE_URL não configurada")
    payload = load_payload()
    asyncpg_url, connect_args = _asyncpg_connection(database_url)
    engine = create_async_engine(
        asyncpg_url,
        pool_pre_ping=True,
        connect_args=connect_args,
        hide_parameters=True,
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as db:
            async with db.begin():
                return await bootstrap_staging_identities(db, payload)
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Bootstrap seguro de identidades AWS")
    parser.add_argument(
        "command",
        nargs="?",
        default="apply",
        choices=("apply",),
        help="Comando fixo aceito pela task ECS de bootstrap.",
    )
    parser.parse_args()
    result = asyncio.run(run_from_environment())
    # Safe operational summary only; never print the secret payload.
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
