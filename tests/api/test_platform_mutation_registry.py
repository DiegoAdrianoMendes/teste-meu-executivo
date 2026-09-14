"""Gate central do contrato de mutações administrativas do Blackbox."""

from app.main import app

_WRITE_METHODS = {"post", "put", "patch", "delete"}
_EXPECTED_POST_QUERIES = {
    ("post", "/api/v1/platform/search/query"),
    ("post", "/api/v1/platform/lookups/query"),
    ("post", "/api/v1/platform/lookups/resolve"),
    ("post", "/api/v1/platform/staff-reassignments/preview"),
    ("post", "/api/v1/platform/commercial/contracts/preview"),
    ("post", "/api/v1/platform/commercial/contracts/legacy-preview"),
    ("post", "/api/v1/platform/commercial/contracts/{contract_id}/revision-preview"),
    ("post", "/api/v1/platform/commercial/contracts/{contract_id}/restore-preview"),
    ("post", "/api/v1/platform/commercial/offers/{sku}/draft/preview"),
    ("post", "/api/v1/platform/commercial/offers/{sku}/restore-preview"),
    ("post", "/api/v1/platform/finance/workspaces/{workspace_id}/drilldown"),
}
_EXPECTED_DEPRECATED = {
    ("post", "/api/v1/platform/workspace-bulk-actions"),
}


def test_every_platform_write_operation_has_a_canonical_contract() -> None:
    operations = app.openapi()["paths"]
    observed_queries: set[tuple[str, str]] = set()
    observed_deprecated: set[tuple[str, str]] = set()
    commands = 0

    for path, path_operations in operations.items():
        if not path.startswith(("/api/v1/platform", "/api/v1/error-logs")):
            continue
        for method, operation in path_operations.items():
            if method not in _WRITE_METHODS:
                continue
            descriptor = operation.get("x-platform-mutation")
            assert descriptor is not None, (method, path)
            kind = descriptor["mutationKind"]
            adapter = descriptor["snapshotAdapter"]
            if kind == "query":
                observed_queries.add((method, path))
                assert adapter == "not_applicable_query", (method, path)
            elif kind == "deprecated":
                observed_deprecated.add((method, path))
                assert adapter == "not_applicable_deprecated", (method, path)
            else:
                commands += 1
                assert kind == "command", (method, path)
                assert adapter != "none", (method, path)
                assert descriptor["resourceType"] not in {"", None}, (method, path)
                assert descriptor["auditSchemaVersion"] == 1, (method, path)

    assert commands == 188
    assert observed_queries == _EXPECTED_POST_QUERIES
    assert observed_deprecated == _EXPECTED_DEPRECATED


def test_people_restoration_contract_matches_reversible_domain_bundle() -> None:
    operations = app.openapi()["paths"]

    block = operations[
        "/api/v1/platform/people/{workspace_id}/{user_id}/block"
    ]["patch"]["x-platform-mutation"]
    access_package = operations[
        "/api/v1/platform/people/{workspace_id}/{user_id}/access-package"
    ]["patch"]["x-platform-mutation"]
    revoke_sessions = operations[
        "/api/v1/platform/people/{workspace_id}/{user_id}/sessions/revoke"
    ]["post"]["x-platform-mutation"]
    transfer_ownership = operations[
        "/api/v1/platform/people/{workspace_id}/{user_id}/transfer-ownership"
    ]["post"]["x-platform-mutation"]

    assert block["resourceType"] == "customer_membership"
    assert block["restorationAdapter"] == "customer_membership"
    assert access_package["resourceType"] == "customer_membership"
    assert access_package["restorationAdapter"] == "customer_membership"
    assert revoke_sessions["resourceType"] == "customer_membership"
    assert revoke_sessions["restorationAdapter"] is None
    assert transfer_ownership["resourceType"] == "workspace_ownership"
    assert transfer_ownership["restorationAdapter"] is None
