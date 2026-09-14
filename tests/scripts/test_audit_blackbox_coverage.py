"""Gates estruturais do inventário backend → Blackbox."""

from __future__ import annotations

from scripts.audit_blackbox_coverage import frontend_sources, inventory, rbac_module_gaps


def test_one_shot_legacy_imports_are_not_misreported_as_missing_operator_ui():
    rows = {
        (row["method"], row["path"]): row
        for row in inventory()
    }

    for path in (
        "/api/v1/platform/commercial/contracts/legacy-preview",
        "/api/v1/platform/commercial/contracts/legacy-import",
    ):
        assert rows[("POST", path)]["status"] == "nao_interativo"


def test_account_360_covers_its_three_commercial_permission_modules():
    gaps = rbac_module_gaps(frontend_sources())

    assert "commercial_contracts" not in gaps
    assert "commercial_adjustments" not in gaps
    assert "entitlement_bonuses" not in gaps


def test_every_interactive_administrative_command_has_snapshot_adapter():
    commands = [
        row
        for row in inventory()
        if row["method"] in {"POST", "PUT", "PATCH", "DELETE"}
        and row["status"] not in {"nao_interativo", "nao_aplicavel"}
        and (row.get("mutationDescriptor") or {}).get("mutationKind") == "command"
    ]

    assert commands
    missing = [
        (row["method"], row["path"])
        for row in commands
        if (row.get("mutationDescriptor") or {}).get("snapshotAdapter") in {None, "none"}
    ]
    assert missing == []


def test_agent_and_finance_control_plane_routes_have_frontend_consumers():
    protected_prefixes = (
        "/api/v1/agent-platform",
        "/api/v1/finance/dimensional",
        "/api/v1/finance/exports",
    )
    missing = [
        (row["method"], row["path"])
        for row in inventory()
        if str(row["path"]).startswith(protected_prefixes)
        and row["status"] == "ausente"
    ]

    assert missing == []
