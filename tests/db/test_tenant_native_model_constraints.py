from __future__ import annotations

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint

import app.models  # noqa: F401
from app.db.base import Base

TENANT_FOREIGN_KEYS = (
    ("klm_omnichannel_threads", "matter_id", "matters"),
    ("klm_omnichannel_threads", "client_id", "clients"),
    ("klm_omnichannel_messages", "thread_id", "klm_omnichannel_threads"),
    ("klm_omnichannel_messages", "matter_id", "matters"),
    ("klm_follow_up_tasks", "matter_id", "matters"),
    ("klm_follow_up_tasks", "thread_id", "klm_omnichannel_threads"),
    ("klm_acquisition_events", "matter_id", "matters"),
    ("klm_acquisition_events", "client_id", "clients"),
    ("klm_legal_artifact_versions", "matter_id", "matters"),
    ("klm_process_monitoring_records", "matter_id", "matters"),
    ("klm_external_action_intents", "matter_id", "matters"),
    ("oss_native_commands", "state_id", "oss_native_capability_states"),
    ("oss_native_records", "last_command_id", "oss_native_commands"),
    ("oss_native_audit_events", "state_id", "oss_native_capability_states"),
    ("oss_native_audit_events", "command_id", "oss_native_commands"),
)


def _foreign_key_signature(constraint: ForeignKeyConstraint):
    elements = tuple(constraint.elements)
    return (
        tuple(element.parent.name for element in elements),
        elements[0].column.table.name,
        tuple(element.column.name for element in elements),
    )


def _unique_signatures(table_name: str) -> set[tuple[str, ...]]:
    table = Base.metadata.tables[table_name]
    return {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }


def _check_constraint_names(table_name: str) -> set[str]:
    return {
        str(constraint.name)
        for constraint in Base.metadata.tables[table_name].constraints
        if isinstance(constraint, CheckConstraint) and constraint.name is not None
    }


def test_native_models_expose_exact_tenant_bound_parent_references() -> None:
    assert len(TENANT_FOREIGN_KEYS) == 15
    for table_name, child_column, parent_table in TENANT_FOREIGN_KEYS:
        table = Base.metadata.tables[table_name]
        signatures = {
            _foreign_key_signature(constraint)
            for constraint in table.constraints
            if isinstance(constraint, ForeignKeyConstraint)
        }
        assert (
            ("workspace_id", child_column),
            parent_table,
            ("workspace_id", "id"),
        ) in signatures
        assert ((child_column,), parent_table, ("id",)) not in signatures


def test_native_composite_foreign_key_parents_are_unique() -> None:
    for table_name in (
        "clients",
        "matters",
        "klm_omnichannel_threads",
        "klm_legal_artifact_versions",
        "oss_native_capability_states",
        "oss_native_commands",
    ):
        assert ("workspace_id", "id") in _unique_signatures(table_name)

    # Constraint histórica usada por FKs na ordem (matter_id, workspace_id).
    # Mantê-la no metadata impede o autogenerate de tentar removê-la.
    assert ("id", "workspace_id") in _unique_signatures("matters")

    assert (
        "workspace_id",
        "id",
        "matter_id",
    ) in _unique_signatures("klm_legal_artifact_versions")
    assert ("id", "matter_id") in _unique_signatures("approval_queue_items")


def test_legal_artifact_state_is_tenant_and_matter_bound() -> None:
    table = Base.metadata.tables["klm_legal_artifact_states"]
    signatures = {
        _foreign_key_signature(constraint)
        for constraint in table.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }
    assert (
        ("workspace_id", "artifact_version_id", "matter_id"),
        "klm_legal_artifact_versions",
        ("workspace_id", "id", "matter_id"),
    ) in signatures
    assert (
        ("approval_item_id", "approval_matter_id"),
        "approval_queue_items",
        ("id", "matter_id"),
    ) in signatures


def test_platform_task_models_publish_database_level_workflow_invariants() -> None:
    assert {
        "ck_platform_tasks_version_positive",
        "ck_platform_tasks_review_round_nonnegative",
        "ck_platform_tasks_reviewer_distinct_owner",
        "ck_platform_tasks_review_contract",
        "ck_platform_tasks_owner_deadline_within_task",
        "ck_platform_tasks_review_deadline_within_task",
    } <= _check_constraint_names("platform_tasks")
    assert {
        "ck_platform_task_participants_required_assignee",
        "ck_platform_task_participants_observer_without_deadline",
        "ck_platform_task_participants_lifecycle",
    } <= _check_constraint_names("platform_task_participants")
    assert {
        "ck_platform_task_review_round_number_positive",
        "ck_platform_task_review_version_positive",
        "ck_platform_task_review_resolution",
    } <= _check_constraint_names("platform_task_review_rounds")


def test_commercial_models_publish_v1_currency_and_adjustment_bounds() -> None:
    assert "ck_commercial_offer_currency_brl" in _check_constraint_names(
        "commercial_offer_versions"
    )
    assert "ck_workspace_contract_currency_brl" in _check_constraint_names(
        "workspace_commercial_contracts"
    )
    assert {
        "ck_commercial_adjustment_percentage_bps",
        "ck_commercial_adjustment_remaining_cycles_nonnegative",
    } <= _check_constraint_names("commercial_adjustments")
