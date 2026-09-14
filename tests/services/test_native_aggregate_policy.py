from __future__ import annotations

import pytest

from app.services.native_aggregate_policy import (
    NativeAggregatePolicyError,
    NativeAggregateStateError,
    NativeAggregateVersionConflictError,
    NativeAggregateVersionRequiredError,
    apply_native_aggregate_transition,
    canonical_native_data_digest,
    native_aggregate_contract,
)


def test_contract_projects_internal_sources_to_canonical_klm_domains() -> None:
    assert native_aggregate_contract("chatwoot", "conversation").domain == (
        "communications"
    )
    assert native_aggregate_contract("rocketchat", "thread").domain == "collaboration"
    assert native_aggregate_contract("chattermate", "flow").domain == "automation"
    assert native_aggregate_contract("bottlecrm", "matter").domain == "crm"

    with pytest.raises(NativeAggregatePolicyError):
        native_aggregate_contract("unknown", "record")


def test_transition_uses_deep_merge_and_requires_version_for_existing_state() -> None:
    created = apply_native_aggregate_transition(
        effect="upsert",
        operation_id="create_record",
        patch={"profile": {"name": "Ana", "phone": "1"}, "score": 10},
        current_state=None,
        current_data=None,
        current_version=0,
        expected_version=0,
    )

    assert created.version == 1
    assert created.created is True
    with pytest.raises(NativeAggregateVersionRequiredError):
        apply_native_aggregate_transition(
            effect="upsert",
            operation_id="update_record",
            patch={"profile": {"phone": "2"}},
            current_state="active",
            current_data=created.data,
            current_version=created.version,
            expected_version=None,
        )

    updated = apply_native_aggregate_transition(
        effect="upsert",
        operation_id="update_record",
        patch={"profile": {"phone": "2"}},
        current_state="active",
        current_data=created.data,
        current_version=created.version,
        expected_version=1,
    )
    assert updated.data == {
        "profile": {"name": "Ana", "phone": "2"},
        "score": 10,
    }
    assert updated.version == 2
    assert updated.data_sha256 == canonical_native_data_digest(updated.data)


def test_transition_rejects_stale_writes_and_reserved_ownership_fields() -> None:
    with pytest.raises(NativeAggregateVersionConflictError):
        apply_native_aggregate_transition(
            effect="upsert",
            operation_id="update_record",
            patch={"name": "Stale"},
            current_state="active",
            current_data={"name": "Atual"},
            current_version=3,
            expected_version=2,
        )
    with pytest.raises(NativeAggregatePolicyError, match="ownership"):
        apply_native_aggregate_transition(
            effect="upsert",
            operation_id="create_record",
            patch={"workspaceId": "outro-tenant"},
            current_state=None,
            current_data=None,
            current_version=0,
            expected_version=0,
        )


def test_archive_and_restore_are_explicit_state_machine_transitions() -> None:
    archived = apply_native_aggregate_transition(
        effect="archive",
        operation_id="archive_record",
        patch={},
        current_state="active",
        current_data={"name": "Caso"},
        current_version=1,
        expected_version=1,
    )
    assert archived.state == "archived"
    assert archived.version == 2

    with pytest.raises(NativeAggregateStateError, match="restauração"):
        apply_native_aggregate_transition(
            effect="upsert",
            operation_id="update_record",
            patch={"name": "Mudança indevida"},
            current_state="archived",
            current_data=archived.data,
            current_version=2,
            expected_version=2,
        )

    restored = apply_native_aggregate_transition(
        effect="upsert",
        operation_id="restore_record",
        patch={"reason": "Reabertura aprovada"},
        current_state="archived",
        current_data=archived.data,
        current_version=2,
        expected_version=2,
    )
    assert restored.state == "active"
    assert restored.version == 3


def test_event_records_are_append_only_per_resource_identity() -> None:
    event = apply_native_aggregate_transition(
        effect="event",
        operation_id="message_received",
        patch={"message": "Olá"},
        current_state=None,
        current_data=None,
        current_version=0,
        expected_version=0,
    )
    assert event.created is True

    with pytest.raises(NativeAggregateStateError, match="imutáveis"):
        apply_native_aggregate_transition(
            effect="event",
            operation_id="message_received",
            patch={"message": "Sobrescrita"},
            current_state="active",
            current_data=event.data,
            current_version=1,
            expected_version=1,
        )
