#!/usr/bin/env bash
set -euo pipefail

: "${BACKEND_IMAGE:?BACKEND_IMAGE is required}"
: "${GITHUB_SHA:?GITHUB_SHA is required}"

expected_head="c9d0e1f2a3b4"
expected_contract="resolver-owner-v1"

if [[ ! "$BACKEND_IMAGE" =~ @sha256:[0-9a-f]{64}$ ]]; then
  echo "BACKEND_IMAGE must identify an immutable sha256 digest." >&2
  exit 1
fi

if [[ ! "$GITHUB_SHA" =~ ^[0-9a-f]{40}$ ]]; then
  echo "GITHUB_SHA must be a full lowercase Git SHA-1." >&2
  exit 1
fi

image_revision="$(
  docker image inspect \
    --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' \
    "$BACKEND_IMAGE"
)"
image_head="$(
  docker image inspect \
    --format '{{ index .Config.Labels "io.meuexecutivo.database.alembic-head" }}' \
    "$BACKEND_IMAGE"
)"
image_contract="$(
  docker image inspect \
    --format '{{ index .Config.Labels "io.meuexecutivo.database.contract" }}' \
    "$BACKEND_IMAGE"
)"

if [[ "$image_revision" != "$GITHUB_SHA" ]]; then
  echo "Candidate image revision label does not match GITHUB_SHA." >&2
  exit 1
fi
if [[ "$image_head" != "$expected_head" ]]; then
  echo "Candidate image Alembic head label is not $expected_head." >&2
  exit 1
fi
if [[ "$image_contract" != "$expected_contract" ]]; then
  echo "Candidate image database contract label is not $expected_contract." >&2
  exit 1
fi

probe_json="$(
  docker run --rm \
    --network none \
    --read-only \
    --cap-drop ALL \
    --security-opt no-new-privileges \
    --entrypoint python \
    "$BACKEND_IMAGE" \
    -c '
import ast
import inspect
import json
from pathlib import Path

from app.db import aws_database_roles as roles

expected_head = "c9d0e1f2a3b4"
expected_contract = "resolver-owner-v1"
expected_marker = "Database migration and post-head runtime grant contract completed."


def migration_metadata(path_value):
    path = Path(path_value)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    values = {}
    for node in tree.body:
        target = None
        value = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target = node.target.id
            value = node.value
        elif (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            target = node.targets[0].id
            value = node.value
        if target not in {"revision", "down_revision"}:
            continue
        if target in values or value is None:
            raise RuntimeError(f"Duplicate or empty {target} in {path}.")
        try:
            literal = ast.literal_eval(value)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"Non-literal {target} in {path}.") from exc
        if target == "revision" and not isinstance(literal, str):
            raise RuntimeError(f"Non-string revision in {path}.")
        if target == "down_revision" and not (
            literal is None
            or isinstance(literal, str)
            or (
                isinstance(literal, (tuple, list))
                and literal
                and all(isinstance(parent, str) for parent in literal)
            )
        ):
            raise RuntimeError(f"Invalid down_revision in {path}.")
        values[target] = literal
    if set(values) != {"revision", "down_revision"}:
        raise RuntimeError(f"Incomplete Alembic metadata in {path}.")
    return values


def migration_graph(directory_value):
    directory = Path(directory_value)
    revisions = {}
    parents_by_revision = {}
    referenced_parents = set()
    migration_files = [
        path for path in sorted(directory.glob("*.py"))
        if path.name != "__init__.py"
    ]
    if not migration_files:
        raise RuntimeError(f"No Alembic migrations found in {directory}.")
    for path in migration_files:
        metadata = migration_metadata(path)
        revision = metadata["revision"]
        if revision in revisions:
            raise RuntimeError(
                f"Duplicate Alembic revision {revision}: {revisions[revision]} and {path}."
            )
        revisions[revision] = path
        down_revision = metadata["down_revision"]
        if isinstance(down_revision, str):
            parents = (down_revision,)
        elif down_revision is None:
            parents = ()
        elif down_revision is not None:
            parents = tuple(down_revision)
        parents_by_revision[revision] = parents
        referenced_parents.update(parents)
    missing_parents = sorted(referenced_parents - set(revisions))
    if missing_parents:
        raise RuntimeError(
            f"Alembic graph references missing revisions: {missing_parents}."
        )
    visit_state = {}

    def visit(revision, trail):
        state = visit_state.get(revision, 0)
        if state == 1:
            cycle_start = trail.index(revision)
            cycle = trail[cycle_start:] + [revision]
            raise RuntimeError(f"Alembic graph contains a cycle: {cycle}.")
        if state == 2:
            return
        visit_state[revision] = 1
        for parent in parents_by_revision[revision]:
            visit(parent, [*trail, revision])
        visit_state[revision] = 2

    for revision in sorted(revisions):
        visit(revision, [])
    heads = sorted(set(revisions) - referenced_parents)
    if heads != [expected_head]:
        raise RuntimeError(
            f"Candidate image must contain exactly head {expected_head}; observed {heads}."
        )
    return revisions


migration_revisions = migration_graph("/app/alembic/versions")
hardening_migration = migration_metadata(
    "/app/alembic/versions/f2c4d6e8a031_harden_tenant_resolver_owner_access.py"
)
template_resolver_migration = migration_metadata(
    "/app/alembic/versions/a3d5e7f9b142_add_whatsapp_template_webhook_resolver.py"
)
webhook_processing_migration = migration_metadata(
    "/app/alembic/versions/c5f7a9b1d304_harden_whatsapp_webhook_processing.py"
)
commercial_workflow_rls_migration = migration_metadata(
    "/app/alembic/versions/d6a8c0e2f415_expand_commercial_workflow_rls.py"
)
contact_stock_entitlement_migration = migration_metadata(
    "/app/alembic/versions/e7b9d1f3a526_add_contact_stock_entitlement.py"
)
auth_rate_limit_scope_width_migration = migration_metadata(
    "/app/alembic/versions/e8c0a2d4f617_widen_auth_rate_limit_event_scope.py"
)
v1_merge_migration = migration_metadata(
    "/app/alembic/versions/8f3c1a9d2b70_merge_v1_finance_agents.py"
)
tax_reform_alert_head_migration = migration_metadata(
    "/app/alembic/versions/jk2e5f6a8b31_add_finance_tax_reform_alerts.py"
)
agent_role_package_migration = migration_metadata(
    "/app/alembic/versions/lm4g7b0c9d53_add_agent_deployment_role_package.py"
)
finance_tax_history_migration = migration_metadata(
    "/app/alembic/versions/mn5h8c1e0f64_preserve_finance_tax_profile_history.py"
)
workspace_knowledge_migration = migration_metadata(
    "/app/alembic/versions/no6h9d2f1a75_allow_workspace_knowledge_chunks.py"
)
required_password_change_migration = migration_metadata(
    "/app/alembic/versions/op7i0e3g2b86_add_required_password_change.py"
)
whatsapp_coexistence_sync_migration = migration_metadata(
    "/app/alembic/versions/oq8j1f4h3c97_add_whatsapp_coexistence_sync_state.py"
)
whatsapp_automation_cooldown_migration = migration_metadata(
    "/app/alembic/versions/pr9k2g5i4d08_add_whatsapp_automation_cooldowns.py"
)
identity_email_delivery_migration = migration_metadata(
    "/app/alembic/versions/rt1m4i7j6e20_add_identity_email_delivery.py"
)
whatsapp_automation_dispatch_rls_migration = migration_metadata(
    "/app/alembic/versions/su2n5j8k7f31_enforce_whatsapp_automation_dispatch_rls.py"
)
task_legal_checklists_migration = migration_metadata(
    "/app/alembic/versions/tv3o6k8l9g42_add_task_legal_checklists.py"
)
flexible_task_context_migration = migration_metadata(
    "/app/alembic/versions/uv4p7l9m0h53_add_flexible_task_context.py"
)
task_external_shares_migration = migration_metadata(
    "/app/alembic/versions/wx5q8m1n2j64_add_task_external_shares.py"
)
task_flow_runs_migration = migration_metadata(
    "/app/alembic/versions/xy6r9n2p3k75_link_tasks_to_flow_runs.py"
)
client_contracts_migration = migration_metadata(
    "/app/alembic/versions/yz7s0p4q5l86_add_canonical_client_contracts.py"
)
task_organization_migration = migration_metadata(
    "/app/alembic/versions/za8t1q4r5m97_add_task_organization.py"
)
client_contract_rls_migration = migration_metadata(
    "/app/alembic/versions/zb9u2r5s6n08_enforce_client_contracts_rls.py"
)
token_expiration_migration = migration_metadata(
    "/app/alembic/versions/zc0v3s6t7p19_track_meta_token_expiration.py"
)
commercial_account_requests_migration = migration_metadata(
    "/app/alembic/versions/zd1w4t7u8q20_add_commercial_account_requests.py"
)
commercial_stripe_bridge_migration = migration_metadata(
    "/app/alembic/versions/ze2x5u8v9r31_add_canonical_commercial_stripe_bridge.py"
)
meta_webhook_control_plane_migration = migration_metadata(
    "/app/alembic/versions/zf3y6v9w0s42_add_meta_webhook_control_plane.py"
)
meta_webhook_rls_alignment_migration = migration_metadata(
    "/app/alembic/versions/zg4z7w0x1t53_align_meta_webhook_rls_policy_names.py"
)
runtime_heads_merge_migration = migration_metadata(
    "/app/alembic/versions/kl3f6a9b8c42_merge_runtime_heads.py"
)
owner_exclusivity_migration = migration_metadata(
    "/app/alembic/versions/b4e6f8a0c253_enforce_platform_owner_exclusivity.py"
)
run_source = inspect.getsource(roles.run_migrations)
reconcile_source = inspect.getsource(roles.reconcile_runtime_privileges_as_migrator)
main_source = inspect.getsource(roles.main)

assert roles._GREENFIELD_HEAD_REVISION == expected_head
assert expected_head in migration_revisions
assert task_organization_migration["revision"] == "za8t1q4r5m97"
assert task_organization_migration["down_revision"] == "yz7s0p4q5l86"
assert client_contract_rls_migration["revision"] == "zb9u2r5s6n08"
assert token_expiration_migration["revision"] == "zc0v3s6t7p19"
assert token_expiration_migration["down_revision"] == "zb9u2r5s6n08"
assert commercial_account_requests_migration["revision"] == "zd1w4t7u8q20"
assert commercial_account_requests_migration["down_revision"] == "zc0v3s6t7p19"
assert commercial_stripe_bridge_migration["revision"] == "ze2x5u8v9r31"
assert commercial_stripe_bridge_migration["down_revision"] == "zd1w4t7u8q20"
assert meta_webhook_control_plane_migration["revision"] == "zf3y6v9w0s42"
assert meta_webhook_rls_alignment_migration["revision"] == "zg4z7w0x1t53"
assert meta_webhook_rls_alignment_migration["down_revision"] == "zf3y6v9w0s42"
assert meta_webhook_control_plane_migration["down_revision"] == "ze2x5u8v9r31"
assert client_contract_rls_migration["down_revision"] == "za8t1q4r5m97"
assert agent_role_package_migration["revision"] == "lm4g7b0c9d53"
assert agent_role_package_migration["down_revision"] == "kl3f6a9b8c42"
assert finance_tax_history_migration["revision"] == "mn5h8c1e0f64"
assert finance_tax_history_migration["down_revision"] == "lm4g7b0c9d53"
assert workspace_knowledge_migration["revision"] == "no6h9d2f1a75"
assert workspace_knowledge_migration["down_revision"] == "mn5h8c1e0f64"
assert required_password_change_migration["revision"] == "op7i0e3g2b86"
assert required_password_change_migration["down_revision"] == "no6h9d2f1a75"
assert whatsapp_coexistence_sync_migration["revision"] == "oq8j1f4h3c97"
assert whatsapp_coexistence_sync_migration["down_revision"] == "op7i0e3g2b86"
assert whatsapp_automation_cooldown_migration["revision"] == "pr9k2g5i4d08"
assert whatsapp_automation_cooldown_migration["down_revision"] == "oq8j1f4h3c97"
assert identity_email_delivery_migration["revision"] == "rt1m4i7j6e20"
assert identity_email_delivery_migration["down_revision"] == "qs0l3h6j5e19"
assert whatsapp_automation_dispatch_rls_migration["revision"] == "su2n5j8k7f31"
assert whatsapp_automation_dispatch_rls_migration["down_revision"] == "rt1m4i7j6e20"
assert task_legal_checklists_migration["revision"] == "tv3o6k8l9g42"
assert task_legal_checklists_migration["down_revision"] == "su2n5j8k7f31"
assert flexible_task_context_migration["revision"] == "uv4p7l9m0h53"
assert flexible_task_context_migration["down_revision"] == "tv3o6k8l9g42"
assert task_external_shares_migration["revision"] == "wx5q8m1n2j64"
assert task_external_shares_migration["down_revision"] == "uv4p7l9m0h53"
assert task_flow_runs_migration["revision"] == "xy6r9n2p3k75"
assert task_flow_runs_migration["down_revision"] == "wx5q8m1n2j64"
assert client_contracts_migration["revision"] == "yz7s0p4q5l86"
assert client_contracts_migration["down_revision"] == "xy6r9n2p3k75"
assert runtime_heads_merge_migration["revision"] == "kl3f6a9b8c42"
assert set(runtime_heads_merge_migration["down_revision"]) == {
    "ij1e3f4a7b20", "jk2e5f6a8b31"
}
assert tax_reform_alert_head_migration["revision"] == "jk2e5f6a8b31"
assert tax_reform_alert_head_migration["down_revision"] == "ij1e4f5a7b20"
assert v1_merge_migration["revision"] == "8f3c1a9d2b70"
assert set(v1_merge_migration["down_revision"]) == {
    "e8c0a2d4f617", "100finance_dimensional", "ab12cd34ef56"
}
assert auth_rate_limit_scope_width_migration["revision"] == "e8c0a2d4f617"
assert auth_rate_limit_scope_width_migration["down_revision"] == "e7b9d1f3a526"
assert contact_stock_entitlement_migration["revision"] == "e7b9d1f3a526"
assert contact_stock_entitlement_migration["down_revision"] == "d6a8c0e2f415"
assert commercial_workflow_rls_migration["revision"] == "d6a8c0e2f415"
assert commercial_workflow_rls_migration["down_revision"] == "c5f7a9b1d304"
assert webhook_processing_migration["down_revision"] == "b4e6f8a0c253"
assert owner_exclusivity_migration["down_revision"] == "a3d5e7f9b142"
assert template_resolver_migration["down_revision"] == "f2c4d6e8a031"
assert hardening_migration["down_revision"] == "e1b3c5d7e920"
assert "[\"alembic\", \"upgrade\", \"head\"]" in run_source
assert "reconcile_runtime_privileges_as_migrator" in run_source
assert "_verify_resolver_owner_contract" in reconcile_source
assert "_verify_exact_runtime_dml" in reconcile_source
assert expected_marker in main_source

print(json.dumps({
    "alembicHead": expected_head,
    "databaseContract": expected_contract,
    "migrationCommand": ["python", "-m", "app.db.aws_database_roles", "migrate"],
    "migrationSuccessMarkerRequired": expected_marker,
    "postHeadCatalogAudit": True,
}, sort_keys=True, separators=(",", ":")))
'
)"

jq -ce \
  --arg source_revision "$GITHUB_SHA" \
  --arg oci_revision_label "$image_revision" \
  --arg runtime_image_uri "$BACKEND_IMAGE" \
  --arg runtime_image_digest "${BACKEND_IMAGE##*@}" '
    .alembicHead == "c9d0e1f2a3b4" and
    .databaseContract == "resolver-owner-v1" and
    .migrationCommand == ["python", "-m", "app.db.aws_database_roles", "migrate"] and
    .migrationSuccessMarkerRequired ==
      "Database migration and post-head runtime grant contract completed." and
    .postHeadCatalogAudit == true
      | if . then {
        sourceRevision: $source_revision,
        ociRevisionLabel: $oci_revision_label,
        runtimeImageUri: $runtime_image_uri,
        runtimeImageDigest: $runtime_image_digest,
      alembicHead: "c9d0e1f2a3b4",
        databaseContract: "resolver-owner-v1",
        migrationCommand: ["python", "-m", "app.db.aws_database_roles", "migrate"],
        migrationSuccessMarkerRequired:
          "Database migration and post-head runtime grant contract completed.",
        postHeadCatalogAudit: true
      } else error("Candidate image migration probe returned an invalid contract") end
  ' <<<"$probe_json"
