"""expand canonical commercial workflow RLS and campaign quota catalog

Revision ID: d6a8c0e2f415
Revises: c5f7a9b1d304
Create Date: 2026-07-31 04:45:00.000000

Prospect conversion, customer provisioning and billing all revalidate their
own narrow platform capability before entering the canonical commercial
command bus.  The original commercial RLS policies only recognized the
Blackbox contract-management capability, so those legitimate workflows failed
at PostgreSQL despite passing application authorization.

This forward-only policy revision adds the three originating capabilities to
the canonical aggregate, receipt and event tables.  Offer/version writes stay
root-only.  It also adds the canonical monthly ``campaign_recipients`` quota
to every persisted entitlement CHECK, including commercial bonuses.  The
downgrade restores the exact previous catalogs and refuses a lossy contraction
when the new quota is already persisted.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from hashlib import sha256

import sqlalchemy as sa

from alembic import op

revision: str = "d6a8c0e2f415"
down_revision: str | Sequence[str] | None = "c5f7a9b1d304"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PLATFORM_SCOPE_SETTING = "app.current_platform_scope"
PLATFORM_ACTION_SETTING = "app.current_platform_action"
PLATFORM_ACTOR_SETTING = "app.current_platform_actor_id"

PLATFORM_READ_POLICY = "platform_commercial_read"
PLATFORM_INSERT_POLICY = "platform_commercial_insert"
PLATFORM_UPDATE_POLICY = "platform_commercial_update"
TENANT_READ_POLICY = "workspace_current_id_select"
PORTABILITY_GUARD_FUNCTION = "klm_guard_commercial_portability_update"
PORTABILITY_GUARD_TRIGGERS = {
    "commercial_adjustments": "trg_commercial_adjustments_portability_guard",
    "entitlement_bonuses": "trg_entitlement_bonuses_portability_guard",
}
PORTABILITY_GUARD_FUNCTION_BODY = f"""
BEGIN
    IF current_setting('{PLATFORM_SCOPE_SETTING}', true)
           IS DISTINCT FROM 'commercial_portability'
       OR (
           current_setting('{PLATFORM_ACTION_SETTING}', true)
               IS DISTINCT FROM 'commercial_portability:rebind'
           AND current_setting('{PLATFORM_ACTION_SETTING}', true)
               IS DISTINCT FROM 'commercial_portability:restore_rebind'
       ) THEN
        RETURN NEW;
    END IF;

    IF NEW.version <> OLD.version + 1
       OR NEW.updated_at IS NULL
       OR OLD.updated_at IS NULL
       OR NEW.updated_at <= OLD.updated_at
       OR NEW.updated_at > clock_timestamp() THEN
        RAISE EXCEPTION 'invalid internal commercial portability version'
            USING ERRCODE = '42501';
    END IF;

    IF TG_TABLE_NAME = 'commercial_adjustments' THEN
        IF NEW.id IS DISTINCT FROM OLD.id
           OR NEW.contract_id IS DISTINCT FROM OLD.contract_id
           OR NEW.type IS DISTINCT FROM OLD.type
           OR NEW.value IS DISTINCT FROM OLD.value
           OR NEW.starts_at IS DISTINCT FROM OLD.starts_at
           OR NEW.ends_at IS DISTINCT FROM OLD.ends_at
           OR NEW.remaining_cycles IS DISTINCT FROM OLD.remaining_cycles
           OR NEW.stack_group IS DISTINCT FROM OLD.stack_group
           OR NEW.stackable IS DISTINCT FROM OLD.stackable
           OR NEW.portability IS DISTINCT FROM OLD.portability
           OR NEW.reason IS DISTINCT FROM OLD.reason
           OR NEW.source IS DISTINCT FROM OLD.source
           OR NEW.created_by_user_id IS DISTINCT FROM OLD.created_by_user_id
           OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
            RAISE EXCEPTION 'internal portability cannot alter adjustment economics'
                USING ERRCODE = '42501';
        END IF;
        IF current_setting('{PLATFORM_ACTION_SETTING}', true)
               = 'commercial_portability:restore_rebind' THEN
            IF NOT (
                OLD.portability IN (
                    'preserve',
                    'expire_on_offer_change',
                    'manual_review',
                    'recalculate'
                )
                AND OLD.status IN ('scheduled', 'active')
                AND NEW.status IS NOT DISTINCT FROM OLD.status
                AND OLD.contract_line_id IS NOT NULL
                AND NEW.contract_line_id IS NOT NULL
                AND NEW.contract_line_id IS DISTINCT FROM OLD.contract_line_id
            ) THEN
                RAISE EXCEPTION 'invalid internal restore portability rebind'
                    USING ERRCODE = '42501';
            END IF;
        ELSIF NOT (
            (
                OLD.portability = 'expire_on_offer_change'
                AND OLD.status IN ('scheduled', 'active')
                AND NEW.status = 'revoked'
                AND NEW.contract_line_id IS NOT DISTINCT FROM OLD.contract_line_id
            )
            OR (
                OLD.portability = 'preserve'
                AND OLD.status IN ('active', 'scheduled')
                AND NEW.status IS NOT DISTINCT FROM OLD.status
                AND OLD.contract_line_id IS NOT NULL
                AND NEW.contract_line_id IS NOT NULL
                AND NEW.contract_line_id IS DISTINCT FROM OLD.contract_line_id
            )
        ) THEN
            RAISE EXCEPTION 'invalid internal adjustment portability transition'
                USING ERRCODE = '42501';
        END IF;
    ELSIF TG_TABLE_NAME = 'entitlement_bonuses' THEN
        IF current_setting('{PLATFORM_ACTION_SETTING}', true)
               = 'commercial_portability:restore_rebind' THEN
            RAISE EXCEPTION 'restore portability cannot update bonuses'
                USING ERRCODE = '42501';
        END IF;
        IF NEW.id IS DISTINCT FROM OLD.id
           OR NEW.workspace_id IS DISTINCT FROM OLD.workspace_id
           OR NEW.entitlement IS DISTINCT FROM OLD.entitlement
           OR NEW.mode IS DISTINCT FROM OLD.mode
           OR NEW.quantity IS DISTINCT FROM OLD.quantity
           OR NEW.starts_at IS DISTINCT FROM OLD.starts_at
           OR NEW.ends_at IS DISTINCT FROM OLD.ends_at
           OR NEW.renews_each_period IS DISTINCT FROM OLD.renews_each_period
           OR NEW.portability IS DISTINCT FROM OLD.portability
           OR NEW.reason IS DISTINCT FROM OLD.reason
           OR NEW.source IS DISTINCT FROM OLD.source
           OR NEW.created_by_user_id IS DISTINCT FROM OLD.created_by_user_id
           OR NEW.created_at IS DISTINCT FROM OLD.created_at
           OR OLD.portability <> 'expire_on_offer_change'
           OR OLD.status NOT IN ('scheduled', 'active')
           OR NEW.status <> 'revoked' THEN
            RAISE EXCEPTION 'invalid internal bonus portability transition'
                USING ERRCODE = '42501';
        END IF;
    ELSE
        RAISE EXCEPTION 'internal portability guard attached to unexpected table'
            USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
END
""".strip()

COMMERCIAL_TABLES = (
    "commercial_adjustments",
    "commercial_catalog_events",
    "commercial_command_receipts",
    "commercial_offer_versions",
    "commercial_offers",
    "entitlement_bonuses",
    "workspace_commercial_contracts",
    "workspace_contract_lines",
)
TENANT_READ_TABLES = frozenset(
    {
        "commercial_adjustments",
        "commercial_offer_versions",
        "commercial_offers",
        "entitlement_bonuses",
        "workspace_commercial_contracts",
        "workspace_contract_lines",
    }
)
MUTABLE_TABLES = frozenset(
    {
        "commercial_adjustments",
        "entitlement_bonuses",
        "workspace_commercial_contracts",
        "workspace_contract_lines",
    }
)
WORKFLOW_AGGREGATE_WRITE_TABLES = frozenset(
    {
        "commercial_catalog_events",
        "commercial_command_receipts",
        "workspace_commercial_contracts",
        "workspace_contract_lines",
    }
)
WORKFLOW_CAPABILITIES: tuple[tuple[str, str], ...] = (
    ("sales", "manage"),
    ("customers", "manage"),
    ("finance", "manage"),
)
PORTABILITY_REBIND_CAPABILITY = ("commercial_portability", "rebind")
PORTABILITY_RESTORE_REBIND_CAPABILITY = (
    "commercial_portability",
    "restore_rebind",
)
PORTABILITY_TABLES = frozenset(
    {
        "commercial_adjustments",
        "entitlement_bonuses",
    }
)

PREVIOUS_ENTITLEMENT_VALUES = (
    "tokens",
    "ai_executions",
    "ai_conversations",
    "audio_minutes",
    "storage_bytes",
    "files",
    "users",
    "workspaces",
    "whatsapp_numbers",
    "agents",
    "legal_suite",
    "legal_areas",
    "legal_cases",
    "djen_terms",
    "legal_ai_generations",
    "legal_documents",
    "legal_automations",
    "legal_templates",
    "legal_signatures",
    "protocol_folders",
    "legal_bulk_operations",
    "legal_audit",
    "legal_integrations",
    "automations",
    "active_tasks",
    "task_participants",
    "task_reviews",
    "review_rounds",
    "task_reminder_rules",
    "meeting_participants",
    "digital_signature",
    "calendar",
    "email",
    "webhooks",
    "advanced_audit",
    "success_manager",
    "monitored_processes",
)
ENTITLEMENT_VALUES = (*PREVIOUS_ENTITLEMENT_VALUES, "campaign_recipients")
PREVIOUS_ENTITLEMENT_LENGTH = max(map(len, PREVIOUS_ENTITLEMENT_VALUES))
ENTITLEMENT_LENGTH = max(map(len, ENTITLEMENT_VALUES))
ENTITLEMENT_CATALOG_TABLES = {
    "plan_entitlements": "entitlement_key_enum",
    "workspace_monthly_entitlement_usage": "entitlement_key_enum",
    "entitlement_usage_reservations": "entitlement_key_enum",
    "workspace_entitlement_overrides": "entitlement_key_enum",
    "entitlement_bonuses": "commercial_bonus_entitlement_enum",
}
SQLITE_NAMING_CONVENTION = {
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
}

BASE_READ_CAPABILITIES: dict[str, tuple[tuple[str, str], ...]] = {
    "commercial_offers": (
        ("plans", "view"),
        ("plans", "manage"),
        ("plans", "draft"),
        ("plans", "preview"),
        ("plans", "publish"),
        ("plans", "restore"),
        ("commercial_contracts", "view"),
        ("commercial_contracts", "manage"),
    ),
    "commercial_offer_versions": (
        ("plans", "view"),
        ("plans", "manage"),
        ("plans", "draft"),
        ("plans", "preview"),
        ("plans", "publish"),
        ("plans", "restore"),
        ("commercial_contracts", "view"),
        ("commercial_contracts", "manage"),
    ),
    "workspace_commercial_contracts": (
        ("commercial_contracts", "view"),
        ("commercial_contracts", "manage"),
        ("commercial_adjustments", "view"),
        ("commercial_adjustments", "manage"),
    ),
    "workspace_contract_lines": (
        ("commercial_contracts", "view"),
        ("commercial_contracts", "manage"),
        ("commercial_adjustments", "manage"),
    ),
    "commercial_adjustments": (
        ("commercial_contracts", "view"),
        ("commercial_contracts", "manage"),
        ("commercial_adjustments", "view"),
        ("commercial_adjustments", "manage"),
    ),
    "entitlement_bonuses": (
        ("commercial_contracts", "view"),
        ("commercial_contracts", "manage"),
        ("entitlement_bonuses", "view"),
        ("entitlement_bonuses", "manage"),
    ),
    "commercial_command_receipts": (
        ("plans", "draft"),
        ("plans", "publish"),
        ("plans", "restore"),
        ("commercial_contracts", "manage"),
        ("commercial_adjustments", "manage"),
        ("entitlement_bonuses", "manage"),
    ),
    "commercial_catalog_events": (
        ("plans", "view"),
        ("plans", "draft"),
        ("plans", "preview"),
        ("plans", "publish"),
        ("plans", "restore"),
        ("commercial_contracts", "manage"),
        ("commercial_adjustments", "manage"),
        ("entitlement_bonuses", "manage"),
    ),
}

LEGACY_WRITE_CAPABILITIES: dict[str, tuple[tuple[str, str], ...]] = {
    "commercial_offers": (),
    "commercial_offer_versions": (),
    "workspace_commercial_contracts": (("commercial_contracts", "manage"),),
    "workspace_contract_lines": (("commercial_contracts", "manage"),),
    "commercial_adjustments": (
        ("commercial_contracts", "manage"),
        ("commercial_adjustments", "manage"),
    ),
    "entitlement_bonuses": (
        ("commercial_contracts", "manage"),
        ("entitlement_bonuses", "manage"),
    ),
    "commercial_command_receipts": (
        ("plans", "draft"),
        ("plans", "publish"),
        ("plans", "restore"),
        ("commercial_contracts", "manage"),
        ("commercial_adjustments", "manage"),
        ("entitlement_bonuses", "manage"),
    ),
    "commercial_catalog_events": (
        ("plans", "draft"),
        ("plans", "publish"),
        ("plans", "restore"),
        ("commercial_contracts", "manage"),
        ("commercial_adjustments", "manage"),
        ("entitlement_bonuses", "manage"),
    ),
}

FORWARD_INSERT_CAPABILITIES: dict[str, tuple[tuple[str, str], ...]] = {
    **LEGACY_WRITE_CAPABILITIES,
    "workspace_commercial_contracts": (
        ("commercial_contracts", "manage"),
        *WORKFLOW_CAPABILITIES,
    ),
    "workspace_contract_lines": (
        ("commercial_contracts", "manage"),
        *WORKFLOW_CAPABILITIES,
    ),
    "commercial_adjustments": (("commercial_adjustments", "manage"),),
    "entitlement_bonuses": (("entitlement_bonuses", "manage"),),
    "commercial_command_receipts": (
        *LEGACY_WRITE_CAPABILITIES["commercial_command_receipts"],
        *WORKFLOW_CAPABILITIES,
    ),
    "commercial_catalog_events": (
        *LEGACY_WRITE_CAPABILITIES["commercial_catalog_events"],
        *WORKFLOW_CAPABILITIES,
    ),
}

FORWARD_UPDATE_CAPABILITIES: dict[str, tuple[tuple[str, str], ...]] = {
    "workspace_commercial_contracts": (
        ("commercial_contracts", "manage"),
        *WORKFLOW_CAPABILITIES,
    ),
    "workspace_contract_lines": (
        ("commercial_contracts", "manage"),
        *WORKFLOW_CAPABILITIES,
    ),
    "commercial_adjustments": (
        ("commercial_adjustments", "manage"),
        PORTABILITY_REBIND_CAPABILITY,
        PORTABILITY_RESTORE_REBIND_CAPABILITY,
    ),
    "entitlement_bonuses": (
        ("entitlement_bonuses", "manage"),
        PORTABILITY_REBIND_CAPABILITY,
    ),
}


def _is_postgresql() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _entitlement_check(values: tuple[str, ...]) -> str:
    allowed = ", ".join(f"'{value}'" for value in values)
    return f"entitlement IN ({allowed})"


def _constraint_entitlement_values(
    table_name: str,
    constraint_name: str,
) -> frozenset[str]:
    checks = sa.inspect(op.get_bind()).get_check_constraints(table_name)
    matches = [check for check in checks if check.get("name") == constraint_name]
    if len(matches) != 1:
        raise RuntimeError(
            "Entitlement catalog drift blocks migration for "
            f"{table_name}; expected exactly one {constraint_name!r} CHECK, "
            f"found {len(matches)}"
        )
    sqltext = str(matches[0].get("sqltext") or "")
    return frozenset(re.findall(r"'([^']+)'", sqltext))


def _require_entitlement_catalog(values: tuple[str, ...]) -> None:
    expected = frozenset(values)
    for table_name, constraint_name in ENTITLEMENT_CATALOG_TABLES.items():
        actual = _constraint_entitlement_values(table_name, constraint_name)
        if actual != expected:
            raise RuntimeError(
                "Entitlement catalog drift blocks migration for "
                f"{table_name}; expected={sorted(expected)!r}, "
                f"actual={sorted(actual)!r}"
            )


def _replace_entitlement_catalog(
    values: tuple[str, ...],
    *,
    previous_values: tuple[str, ...],
    previous_length: int,
    target_length: int,
) -> None:
    """Replace every persisted non-native enum CHECK without hiding drift."""

    _require_entitlement_catalog(previous_values)
    expression = _entitlement_check(values)
    if op.get_bind().dialect.name == "sqlite":
        for table_name, constraint_name in ENTITLEMENT_CATALOG_TABLES.items():
            with op.batch_alter_table(
                table_name,
                naming_convention=SQLITE_NAMING_CONVENTION,
                recreate="always",
            ) as batch_op:
                batch_op.drop_constraint(constraint_name, type_="check")
                batch_op.alter_column(
                    "entitlement",
                    existing_type=sa.String(length=previous_length),
                    type_=sa.String(length=target_length),
                    existing_nullable=False,
                )
                batch_op.create_check_constraint(constraint_name, expression)
    else:
        for table_name, constraint_name in ENTITLEMENT_CATALOG_TABLES.items():
            op.drop_constraint(constraint_name, table_name, type_="check")
            op.alter_column(
                table_name,
                "entitlement",
                existing_type=sa.String(length=previous_length),
                type_=sa.String(length=target_length),
                existing_nullable=False,
            )
            op.create_check_constraint(
                constraint_name,
                table_name,
                expression,
            )
    _require_entitlement_catalog(values)


def _require_no_campaign_recipient_usage_before_downgrade() -> None:
    """Refuse to contract a quota that already carries durable state."""

    bind = op.get_bind()
    violations = {
        table_name: int(
            bind.execute(
                sa.text(
                    f'SELECT COUNT(*) FROM "{table_name}" '
                    "WHERE entitlement = :entitlement"
                ),
                {"entitlement": "campaign_recipients"},
            ).scalar_one()
        )
        for table_name in ENTITLEMENT_CATALOG_TABLES
    }
    violations = {name: count for name, count in violations.items() if count > 0}
    if violations:
        detail = ", ".join(
            f"{table_name}={count}"
            for table_name, count in sorted(violations.items())
        )
        raise RuntimeError(
            "Cannot downgrade campaign_recipients while durable entitlement "
            f"state is persisted: {detail}"
        )


def _platform_predicate(*capabilities: tuple[str, str]) -> str:
    allowed = " OR ".join(
        "("
        f"current_setting('{PLATFORM_SCOPE_SETTING}', true) = '{module}' "
        f"AND current_setting('{PLATFORM_ACTION_SETTING}', true) = '{module}:{action}'"
        ")"
        for module, action in capabilities
    )
    return (
        f"NULLIF(current_setting('{PLATFORM_ACTOR_SETTING}', true), '') IS NOT NULL "
        "AND ("
        f"current_setting('{PLATFORM_SCOPE_SETTING}', true) = 'platform_root'"
        + (f" OR {allowed}" if allowed else "")
        + ")"
    )


def _expected_policy_catalog(table_name: str) -> set[tuple[str, str, str, str]]:
    expected = {
        (PLATFORM_READ_POLICY, "SELECT", "PERMISSIVE", "{public}"),
        (PLATFORM_INSERT_POLICY, "INSERT", "PERMISSIVE", "{public}"),
    }
    if table_name in TENANT_READ_TABLES:
        expected.add((TENANT_READ_POLICY, "SELECT", "PERMISSIVE", "{public}"))
    if table_name in MUTABLE_TABLES:
        expected.add((PLATFORM_UPDATE_POLICY, "UPDATE", "PERMISSIVE", "{public}"))
    return expected


def _require_exact_policy_catalog() -> None:
    bind = op.get_bind()
    for table_name in COMMERCIAL_TABLES:
        rows = bind.execute(
            sa.text(
                """
                SELECT policyname, cmd, permissive, roles::text
                FROM pg_policies
                WHERE schemaname = 'public' AND tablename = :table_name
                """
            ),
            {"table_name": table_name},
        ).all()
        actual = {
            (str(name), str(command), str(permissive), str(roles))
            for name, command, permissive, roles in rows
        }
        expected = _expected_policy_catalog(table_name)
        if actual != expected:
            raise RuntimeError(
                "Commercial RLS policy drift blocks capability migration for "
                f"{table_name}; expected={sorted(expected)!r}, actual={sorted(actual)!r}"
            )


def _normalized_sql(value: str) -> str:
    return " ".join(value.split()).strip()


def _normalized_trigger_definition(value: str, *, table_name: str) -> str:
    normalized = _normalized_sql(value).replace('"', "").rstrip(";").lower()
    normalized = normalized.replace(
        f" on {table_name} ",
        f" on public.{table_name} ",
    )
    normalized = normalized.replace(
        f" execute function {PORTABILITY_GUARD_FUNCTION}()",
        f" execute function public.{PORTABILITY_GUARD_FUNCTION}()",
    )
    return normalized


def _expected_trigger_definition(table_name: str, trigger_name: str) -> str:
    return (
        f"create trigger {trigger_name} before update on public.{table_name} "
        "for each row execute function "
        f"public.{PORTABILITY_GUARD_FUNCTION}()"
    )


def _require_portability_guard(*, installed: bool) -> None:
    """Refuse to install over drift or remove a guard we do not own exactly."""

    bind = op.get_bind()
    function_state = bind.execute(
        sa.text(
            """
            SELECT procedure.prosrc,
                   procedure.prosecdef,
                   language.lanname,
                   procedure.proconfig,
                   EXISTS (
                       SELECT 1
                       FROM aclexplode(
                           COALESCE(
                               procedure.proacl,
                               acldefault('f', procedure.proowner)
                           )
                       ) AS privilege
                       WHERE privilege.grantee = 0
                         AND privilege.privilege_type = 'EXECUTE'
                   )
            FROM pg_proc AS procedure
            JOIN pg_namespace AS namespace
              ON namespace.oid = procedure.pronamespace
            JOIN pg_language AS language
              ON language.oid = procedure.prolang
            WHERE namespace.nspname = 'public'
              AND procedure.oid = to_regprocedure(
                  'public.klm_guard_commercial_portability_update()'
              )
            """
        )
    ).one_or_none()
    trigger_rows = bind.execute(
        sa.text(
            """
            SELECT relation.relname,
                   trigger.tgname,
                   procedure.proname,
                   function_namespace.nspname,
                   trigger.tgenabled::text,
                   trigger.tgtype,
                   trigger.tgqual IS NULL,
                   trigger.tgattr::text,
                   trigger.tgnargs,
                   trigger.tgfoid = to_regprocedure(
                       'public.klm_guard_commercial_portability_update()'
                   ),
                   pg_get_triggerdef(trigger.oid)
            FROM pg_trigger AS trigger
            JOIN pg_class AS relation
              ON relation.oid = trigger.tgrelid
            JOIN pg_namespace AS namespace
              ON namespace.oid = relation.relnamespace
            JOIN pg_proc AS procedure
              ON procedure.oid = trigger.tgfoid
            JOIN pg_namespace AS function_namespace
              ON function_namespace.oid = procedure.pronamespace
            WHERE namespace.nspname = 'public'
              AND trigger.tgname IN (
                  'trg_commercial_adjustments_portability_guard',
                  'trg_entitlement_bonuses_portability_guard'
              )
              AND NOT trigger.tgisinternal
            """
        )
    ).all()
    if not installed:
        if function_state is not None or trigger_rows:
            raise RuntimeError(
                "Commercial portability guard drift blocks migration; "
                "an unowned function or trigger already exists"
            )
        return

    expected_trigger_map = {
        (table_name, trigger_name)
        for table_name, trigger_name in PORTABILITY_GUARD_TRIGGERS.items()
    }
    actual_trigger_map = {(str(row[0]), str(row[1])) for row in trigger_rows}
    if function_state is None or actual_trigger_map != expected_trigger_map:
        raise RuntimeError(
            "Commercial portability update guard is incomplete or drifted"
        )
    body, security_definer, language, config, public_execute = function_state
    if (
        _normalized_sql(str(body))
        != _normalized_sql(PORTABILITY_GUARD_FUNCTION_BODY)
        or bool(security_definer)
        or str(language) != "plpgsql"
        or set(config or ()) != {"search_path=pg_catalog, pg_temp"}
        or bool(public_execute)
    ):
        raise RuntimeError(
            "Commercial portability guard function is unsafe or drifted"
        )
    for (
        table_name,
        trigger_name,
        function_name,
        function_namespace,
        enabled,
        trigger_type,
        no_when_clause,
        update_columns,
        argument_count,
        exact_function,
        trigger_definition,
    ) in trigger_rows:
        normalized_definition = _normalized_trigger_definition(
            str(trigger_definition),
            table_name=str(table_name),
        )
        expected_definition = _expected_trigger_definition(
            str(table_name),
            str(trigger_name),
        )
        trigger_invariants = (
            ("function_name", str(function_name), PORTABILITY_GUARD_FUNCTION),
            ("function_namespace", str(function_namespace), "public"),
            ("enabled", str(enabled), "O"),
            # PostgreSQL trigger bitmask: ROW(1) | BEFORE(2) | UPDATE(16).
            ("trigger_type", int(trigger_type), 19),
            ("no_when_clause", bool(no_when_clause), True),
            ("update_columns", str(update_columns), ""),
            ("argument_count", int(argument_count), 0),
            ("exact_function", bool(exact_function), True),
            ("definition", normalized_definition, expected_definition),
        )
        drift: list[str] = []
        for name, actual_value, expected_value in trigger_invariants:
            if actual_value == expected_value:
                continue
            if name == "definition":
                actual_value = (
                    "sha256:"
                    + sha256(str(actual_value).encode("utf-8")).hexdigest()
                )
                expected_value = (
                    "sha256:"
                    + sha256(str(expected_value).encode("utf-8")).hexdigest()
                )
            drift.append(
                f"{name}: expected={expected_value!r}, actual={actual_value!r}"
            )
        if drift:
            raise RuntimeError(
                "Commercial portability trigger is unsafe or drifted "
                f"for public.{table_name}/{trigger_name}: "
                + "; ".join(drift)
            )


def _install_portability_guard() -> None:
    if not _is_postgresql():
        return
    _require_portability_guard(installed=False)
    op.execute(
        f"""
        CREATE FUNCTION public.{PORTABILITY_GUARD_FUNCTION}()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY INVOKER
        SET search_path = pg_catalog, pg_temp
        AS $guard$
        {PORTABILITY_GUARD_FUNCTION_BODY}
        $guard$
        """
    )
    op.execute(
        f"REVOKE ALL ON FUNCTION public.{PORTABILITY_GUARD_FUNCTION}() FROM PUBLIC"
    )
    for table_name, trigger_name in PORTABILITY_GUARD_TRIGGERS.items():
        op.execute(
            f"""
            CREATE TRIGGER "{trigger_name}"
            BEFORE UPDATE ON "{table_name}"
            FOR EACH ROW
            EXECUTE FUNCTION public.{PORTABILITY_GUARD_FUNCTION}()
            """
        )
    _require_portability_guard(installed=True)


def _drop_portability_guard() -> None:
    if not _is_postgresql():
        return
    _require_portability_guard(installed=True)
    for table_name, trigger_name in PORTABILITY_GUARD_TRIGGERS.items():
        op.execute(f'DROP TRIGGER "{trigger_name}" ON "{table_name}"')
    op.execute(f"DROP FUNCTION public.{PORTABILITY_GUARD_FUNCTION}()")
    _require_portability_guard(installed=False)


def _alter_policy(
    table_name: str,
    policy_name: str,
    *,
    using: str | None = None,
    with_check: str | None = None,
) -> None:
    clauses = [f'ALTER POLICY "{policy_name}" ON "{table_name}"']
    if using is not None:
        clauses.append(f"USING ({using})")
    if with_check is not None:
        clauses.append(f"WITH CHECK ({with_check})")
    op.execute("\n".join(clauses))


def _install(*, forward: bool) -> None:
    if not _is_postgresql():
        return
    _require_exact_policy_catalog()
    for table_name in COMMERCIAL_TABLES:
        read_capabilities = BASE_READ_CAPABILITIES[table_name]
        if forward:
            read_capabilities = (*read_capabilities, *WORKFLOW_CAPABILITIES)
            if table_name in PORTABILITY_TABLES:
                read_capabilities = (
                    *read_capabilities,
                    PORTABILITY_REBIND_CAPABILITY,
                )
            if table_name == "commercial_adjustments":
                read_capabilities = (
                    *read_capabilities,
                    PORTABILITY_RESTORE_REBIND_CAPABILITY,
                )
        read_predicate = _platform_predicate(*read_capabilities)
        _alter_policy(table_name, PLATFORM_READ_POLICY, using=read_predicate)

        insert_capabilities = (
            FORWARD_INSERT_CAPABILITIES[table_name]
            if forward
            else LEGACY_WRITE_CAPABILITIES[table_name]
        )
        insert_predicate = _platform_predicate(*insert_capabilities)
        _alter_policy(
            table_name,
            PLATFORM_INSERT_POLICY,
            with_check=insert_predicate,
        )
        if table_name in MUTABLE_TABLES:
            update_capabilities = (
                FORWARD_UPDATE_CAPABILITIES[table_name]
                if forward
                else LEGACY_WRITE_CAPABILITIES[table_name]
            )
            update_predicate = _platform_predicate(*update_capabilities)
            _alter_policy(
                table_name,
                PLATFORM_UPDATE_POLICY,
                using=update_predicate,
                with_check=update_predicate,
            )


def upgrade() -> None:
    _replace_entitlement_catalog(
        ENTITLEMENT_VALUES,
        previous_values=PREVIOUS_ENTITLEMENT_VALUES,
        previous_length=PREVIOUS_ENTITLEMENT_LENGTH,
        target_length=ENTITLEMENT_LENGTH,
    )
    _install(forward=True)
    _install_portability_guard()


def downgrade() -> None:
    _require_no_campaign_recipient_usage_before_downgrade()
    _drop_portability_guard()
    _install(forward=False)
    _replace_entitlement_catalog(
        PREVIOUS_ENTITLEMENT_VALUES,
        previous_values=ENTITLEMENT_VALUES,
        previous_length=ENTITLEMENT_LENGTH,
        target_length=PREVIOUS_ENTITLEMENT_LENGTH,
    )
