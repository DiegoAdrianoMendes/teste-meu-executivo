import json
import os
import subprocess
import sys
from datetime import UTC, datetime

import bcrypt
import pytest
from sqlalchemy import func, select

from app.db.staging_identity_bootstrap import (
    StagingBootstrapError,
    _asyncpg_connection,
    _deployment_environment,
    _hash_password,
    bootstrap_staging_identities,
    load_payload,
    main,
)
from app.models.commercial_catalog import (
    CommercialAdjustment,
    CommercialAdjustmentType,
    CommercialOffer,
    WorkspaceCommercialContract,
    WorkspaceContractLine,
)
from app.models.user import AgentType, User, UserRole
from app.models.workspace import Workspace
from app.models.workspace_membership import WorkspaceMembership, WorkspaceRole
from app.services.commercial_catalog_service import CommercialCatalogService


def _payload() -> str:
    return json.dumps(
        {
            "root": {
                "name": "Platform Owner",
                "email": "kleistfilho@gmail.com",
                "password": "12345",
            },
            "platform_admins": [
                {
                    "name": "Platform Admin",
                    "email": "kleist@meuexecutivo.com",
                    "password": "12345",
                },
            ],
            "technical_staff": [
                {
                    "name": "Diego Adriano Mendes",
                    "email": "diegoadrianomendes@gmail.com",
                    "password": "12345",
                },
            ],
            "pilot_workspace": {
                "name": "KLM Office Owner",
                "email": "kleist.adv@gmail.com",
                "password": "12345",
            },
            "rotate_temporary_passwords": True,
        }
    )


def test_bootstrap_module_import_does_not_require_runtime_secret_key() -> None:
    environment = dict(os.environ)
    environment.pop("SECRET_KEY", None)
    environment.pop("KLM_SECRET_KEY", None)
    environment.update(
        {
            "APP_ENV": "production",
            "ENVIRONMENT": "staging",
            "KLM_RUNTIME_MODE": "aws_private",
            "KLM_LOCAL_BOOTSTRAP_ENABLED": "false",
        }
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from app.db.staging_identity_bootstrap import _load_commercial_services; "
            "_load_commercial_services()",
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_deployment_environment_accepts_only_staging_or_production(monkeypatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    assert _deployment_environment() == "production"

    monkeypatch.setenv("ENVIRONMENT", "preview")
    with pytest.raises(StagingBootstrapError, match="ENVIRONMENT inválido"):
        _deployment_environment()


def test_cli_malformed_payload_never_echoes_rejected_values() -> None:
    sentinel = "LEAK-ME-BOOTSTRAP-SECRET"
    environment = dict(os.environ)
    environment["KLM_IDENTITY_BOOTSTRAP_PAYLOAD"] = json.dumps(
        {"unexpected_secret": sentinel}
    )
    environment["DATABASE_URL"] = "postgresql+asyncpg://invalid:invalid@127.0.0.1/invalid"
    result = subprocess.run(
        [sys.executable, "-m", "app.db.staging_identity_bootstrap", "apply"],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert sentinel not in result.stdout
    assert sentinel not in result.stderr
    assert "payload de identidade inválido" in result.stderr


@pytest.mark.asyncio
async def test_bootstrap_password_hash_uses_bcrypt_without_runtime_settings() -> None:
    hashed = await _hash_password("temporary-bootstrap-secret")
    assert bcrypt.checkpw(b"temporary-bootstrap-secret", hashed.encode("utf-8"))


def test_asyncpg_connection_translates_required_sslmode() -> None:
    url, connect_args = _asyncpg_connection(
        "postgresql+asyncpg://user:password@db.example/meu_executivo?sslmode=require"
    )
    assert "sslmode" not in url.query
    assert connect_args == {"ssl": "require"}


def test_asyncpg_connection_rejects_unproved_ssl_modes() -> None:
    with pytest.raises(StagingBootstrapError, match="sslmode não suportado"):
        _asyncpg_connection(
            "postgresql+asyncpg://user:password@db.example/meu_executivo?sslmode=prefer"
        )


def test_load_payload_canonicalizes_and_hides_secret_value() -> None:
    payload = load_payload(_payload())
    assert payload.root.email == "kleistfilho@gmail.com"
    assert "12345" not in repr(payload)
    assert payload.workspace_slug == "klm-piloto"


def test_load_payload_rejects_wrong_identity_without_echoing_secret() -> None:
    raw = json.loads(_payload())
    raw["root"]["email"] = "root@evil.example"
    raw["root"]["password"] = "do-not-echo-this"
    with pytest.raises(StagingBootstrapError) as exc:
        payload = load_payload(json.dumps(raw))
        from app.db.staging_identity_bootstrap import _validate_target_domains

        _validate_target_domains(payload)
    assert "do-not-echo-this" not in str(exc.value)


def test_load_payload_rejects_duplicate_admins() -> None:
    raw = json.loads(_payload())
    raw["platform_admins"][0]["email"] = raw["pilot_workspace"]["email"]
    payload = load_payload(json.dumps(raw))
    from app.db.staging_identity_bootstrap import _validate_target_domains

    with pytest.raises(StagingBootstrapError):
        _validate_target_domains(payload)


def test_cli_accepts_only_the_fixed_apply_command(monkeypatch, capsys) -> None:
    async def fake_run_from_environment() -> dict[str, object]:
        return {"workspace_slug": "klm-piloto", "platform_admin_count": 1}

    monkeypatch.setattr(
        "app.db.staging_identity_bootstrap.run_from_environment",
        fake_run_from_environment,
    )
    monkeypatch.setattr(sys, "argv", ["staging_identity_bootstrap", "apply"])

    main()

    result = json.loads(capsys.readouterr().out)
    assert result == {"platform_admin_count": 1, "workspace_slug": "klm-piloto"}


def test_cli_rejects_arbitrary_commands(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["staging_identity_bootstrap", "shell"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2


@pytest.mark.asyncio
async def test_bootstrap_is_idempotent_and_keeps_platform_and_tenant_identities_separate(
    db_session,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    payload = load_payload(_payload())
    existing_staff = User(
        name="Equipe Blackbox existente",
        email="existing-blackbox@example.com",
        hashed_password=await _hash_password("senha-anterior"),
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="EB",
        is_active=True,
        email_verified_at=datetime.now(UTC),
        is_internal_staff=True,
        is_platform_admin=False,
        platform_role_package="support",
        platform_access_active=True,
    )
    db_session.add(existing_staff)
    await db_session.commit()

    first = await bootstrap_staging_identities(db_session, payload)
    await db_session.commit()
    root_hash_before = await db_session.scalar(
        select(User.hashed_password).where(User.email == "kleistfilho@gmail.com")
    )

    second = await bootstrap_staging_identities(db_session, payload)
    await db_session.commit()

    assert second == first
    assert await db_session.scalar(select(func.count(Workspace.id))) == 1
    assert await db_session.scalar(select(func.count(WorkspaceCommercialContract.id))) == 1
    assert await db_session.scalar(select(func.count(WorkspaceMembership.id))) == 1

    root = await db_session.scalar(select(User).where(User.email == "kleistfilho@gmail.com"))
    admin = await db_session.scalar(select(User).where(User.email == "kleist@meuexecutivo.com"))
    technical = await db_session.scalar(
        select(User).where(User.email == "diegoadrianomendes@gmail.com")
    )
    pilot = await db_session.scalar(select(User).where(User.email == "kleist.adv@gmail.com"))
    membership = await db_session.scalar(select(WorkspaceMembership))
    contract_lines = list(
        (
            await db_session.execute(
                select(CommercialOffer.sku, WorkspaceContractLine.contracted_price_cents)
                .join(WorkspaceContractLine, WorkspaceContractLine.offer_id == CommercialOffer.id)
                .order_by(CommercialOffer.sku)
            )
        ).all()
    )
    adjustment = await db_session.scalar(select(CommercialAdjustment))
    contract = await db_session.scalar(select(WorkspaceCommercialContract))
    workspace = await db_session.scalar(select(Workspace))
    priced_contract = await CommercialCatalogService(db_session).get_contract(contract.id)

    assert root is not None
    assert root.platform_role_package == "platform_owner"
    assert root.password_change_required is True
    assert root.hashed_password != "12345"
    assert root.hashed_password == root_hash_before
    assert admin is not None
    assert admin.platform_role_package == "platform_admin"
    assert admin.is_platform_admin is True
    assert admin.password_change_required is True
    assert technical is not None
    assert technical.platform_role_package == "technical_support"
    assert technical.is_platform_admin is False
    assert technical.is_internal_staff is True
    assert technical.platform_role_package != "platform_owner"
    assert technical.password_change_required is True
    await db_session.refresh(existing_staff)
    assert bcrypt.checkpw(b"senha-anterior", existing_staff.hashed_password.encode("utf-8"))
    assert existing_staff.password_change_required is False
    assert existing_staff.platform_role_package == "support"
    assert existing_staff.platform_role_package != "platform_owner"
    assert pilot is not None
    assert pilot.is_internal_staff is False
    assert pilot.is_platform_admin is False
    assert pilot.platform_access_active is False
    assert pilot.platform_role_package is None
    assert pilot.password_change_required is True
    assert membership is not None
    assert membership.user_id == pilot.id
    assert membership.role == WorkspaceRole.workspace_owner
    assert workspace is not None
    assert workspace.settings["runtime"] == "production"
    assert [sku for sku, _ in contract_lines] == ["crm_evolution", "legal_360"]
    assert dict(contract_lines) == {"crm_evolution": 0, "legal_360": 1}
    assert adjustment is not None
    assert adjustment.type == CommercialAdjustmentType.fixed_discount
    assert adjustment.value == 1
    assert adjustment.ends_at is None
    assert priced_contract.price.recurring_price_cents == 0
    assert priced_contract.price.amount_due_cents == 0
