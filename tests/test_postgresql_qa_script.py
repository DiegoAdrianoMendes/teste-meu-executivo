from pathlib import Path

SCRIPT = Path("scripts/verify_postgresql_qa.sh")
ROLE_BOOTSTRAP = Path("docker/postgres/bootstrap-runtime-role.sh")
MIGRATION_CYCLE = Path("scripts/run_postgresql_migration_cycle.py")
GREENFIELD_GATE = Path("scripts/verify_greenfield_v1_mode.sh")


def test_postgresql_qa_script_is_ephemeral_and_runs_all_gated_suites() -> None:
    content = SCRIPT.read_text(encoding="utf-8")

    assert "--publish 127.0.0.1::5432" in content
    assert "trap 'cleanup $?' EXIT" in content
    assert 'exit "$status"' in content
    assert "KLM_TEST_POSTGRES_ADMIN_URL" in content
    assert "KLM_TEST_POSTGRES_MASTER_URL" in content
    assert "KLM_TEST_POSTGRES_APP_URL" in content
    assert "test_email_ingest_concurrency_postgresql.py" in content
    assert "test_identity_registration_postgresql.py" in content
    assert "test_meeting_concurrency_postgresql.py" in content
    assert "test_migration_data_loss_preflights_postgresql.py" in content
    assert "test_database_role_bootstrap_postgresql.py" in content
    assert "test_postgresql_rls.py" in content
    assert "test_task_collaboration_rls_postgresql.py" in content
    assert "test_whatsapp_webhook_concurrency_postgresql.py" in content
    assert "test_legal_crm_rls_postgresql.py" in content
    assert "POSTGRES_USER=klm_master" in content
    assert "POSTGRES_USER=klm_migrator" not in content
    assert 'KLM_POSTGRES_QA_IMAGE:-postgres:17-alpine' in content
    assert "SHOW server_version_num" in content
    assert '[[ "$POSTGRES_SERVER_VERSION_NUM" =~ ^17[0-9]{4}$ ]]' in content
    assert "scripts.run_postgresql_migration_cycle" in content
    assert "verify_greenfield_v1_mode.sh" in content
    assert "KLM_RELEASE_MODE=greenfield-prepare" in content
    assert "KLM_PREVIOUS_APP_REF" not in content
    assert "--defer-runtime-contract" not in content
    assert "--contract-only" not in content
    assert "KLM_KEEP_POSTGRES_QA" in content
    assert "KLM_POSTGRES_QA_ENV_FILE" in content
    assert "KLM_POSTGRES_QA_APP_ASYNC_URL" in content
    assert "KLM_POSTGRES_QA_APP_SYNC_URL" in content
    assert 'chmod 0600 "$POSTGRES_ENV_PATH"' in content
    assert 'docker logs "$CONTAINER_ID"' in content
    assert '"$status" != "0"' in content
    assert 'rm -f "$POSTGRES_ENV_PATH"' in content
    assert "::add-mask::" in content
    assert "--all" in content
    assert "--junitxml=" in content
    assert "--pytest-target=*" in content
    assert 'PYTEST_TARGETS+=("$target")' in content
    assert "Use either --all or --pytest-target, never both." in content
    assert '"${PYTEST_TARGETS[@]}"' in content
    assert "verify_junit_clean.py" in content


def test_migration_cycle_uses_real_bootstrap_and_exact_migrator_identity() -> None:
    content = MIGRATION_CYCLE.read_text(encoding="utf-8")
    hardening_migration = Path(
        "alembic/versions/f2c4d6e8a031_harden_tenant_resolver_owner_access.py"
    ).read_text(encoding="utf-8")

    assert "bootstrap_database_roles(config)" in content
    assert "assert_migration_role(migration_url, config.migrator.username)" in content
    assert "SELECT current_user," in content
    assert "session_user," in content
    assert "def _finalize_secure_head(" in content
    assert 'CURRENT_HEAD_REVISION = "c9d0e1f2a3b4"' in content
    assert hardening_migration.count("GRANTED BY %I") >= 4
    assert "Resolver ACL drift blocks owner hardening" in hardening_migration
    assert "Resolver ACL drift blocks downgrade" in hardening_migration
    assert "COALESCE(grantee.rolname, 'PUBLIC')" in hardening_migration
    assert "RDS master users are not PostgreSQL superusers" in hardening_migration
    assert "grantee.rolname NOT IN (migrator_role, runtime_role)" in hardening_migration
    assert "grantee.rolcreatedb" in hardening_migration
    assert "grantee.rolcreaterole" in hardening_migration
    for unsafe_flag in (
        "rolsuper",
        "rolcreatedb",
        "rolcreaterole",
        "rolinherit",
        "rolreplication",
        "rolbypassrls",
    ):
        assert unsafe_flag in content
    first_upgrade = content.index('_run_alembic(alembic_bin, migration_url, "upgrade", "head")')
    resolver_downgrade = content.index(
        '_run_alembic(alembic_bin, migration_url, "downgrade", "e1b3c5d7e920")'
    )
    e1_contract = content.index("_assert_e1_resolver_contract_after_downgrade(config)")
    resolver_reupgrade = content.index(
        '_run_alembic(alembic_bin, migration_url, "upgrade", "head")',
        first_upgrade + 1,
    )
    rollback_cycle = content.index(
        '_run_alembic(alembic_bin, migration_url, "downgrade", "base")'
    )
    final_upgrade = content.index(
        '_run_alembic(alembic_bin, migration_url, "upgrade", "head")',
        resolver_reupgrade + 1,
    )
    metadata_check = content.index('_run_alembic(alembic_bin, migration_url, "check")')
    assert (
        first_upgrade
        < resolver_downgrade
        < e1_contract
        < resolver_reupgrade
        < rollback_cycle
        < final_upgrade
        < metadata_check
    )


def test_greenfield_gate_forbids_legacy_binary_and_service_rollout() -> None:
    gate = GREENFIELD_GATE.read_text(encoding="utf-8")

    assert "KLM_RELEASE_MODE" in gate
    assert "greenfield-prepare" in gate
    assert "KLM_PREVIOUS_APP_REF is forbidden" in gate
    assert "AWS_ECS_SERVICE_TARGETS is forbidden" in gate
    assert "a671ed1 is not compatible" in gate


def test_runtime_role_bootstrap_reconciles_grants_with_rls_and_immutability() -> None:
    content = ROLE_BOOTSTRAP.read_text(encoding="utf-8")

    assert "relation.relrowsecurity" in content
    assert "pg_policies" in content
    assert "immutable_trigger.tgname LIKE '%immutable%'" in content
    assert "candidate.privilege IN ('UPDATE', 'DELETE')" in content
    assert "REVOKE %s ON TABLE %I.%I FROM %I" in content
    assert "platform_official_whatsapp_deliveries" in content
    assert "REVOKE DELETE ON TABLE %I FROM %I" in content
