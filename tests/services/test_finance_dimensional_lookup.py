from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.api.v1.endpoints.finance_dimensional import upsert_tax_profile
from app.models.client import Client
from app.models.klm_operations import KlmAuditEvent
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.schemas.finance_dimensional import TaxProfileWrite
from app.services.finance_dimensional_service import (
    FinanceDimensionalService,
    FinanceDimensionValidationError,
)
from app.services.finance_tax_rules_service import (
    due_tax_reform_reminders,
    tax_profile_alerts,
    tax_rule_catalog,
)


@pytest.mark.asyncio
async def test_lookup_rejects_unknown_dimension_without_query():
    service = FinanceDimensionalService(None)
    with pytest.raises(FinanceDimensionValidationError, match="inválido"):
        await service.lookup(None, entity_type="secret")


@pytest.mark.asyncio
async def test_client_lookup_is_runtime_tenant_scoped_and_human_labelled(db_session, auth_context):
    foreign_workspace = Workspace(
        name="Workspace Financeiro Externo",
        slug=f"finance-foreign-{uuid4().hex[:8]}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(foreign_workspace)
    await db_session.flush()
    own_client = Client(workspace_id=auth_context.workspace_id, name="Cliente Visível Financeiro")
    foreign_client = Client(workspace_id=foreign_workspace.id, name="Cliente Sigiloso Externo")
    db_session.add_all([own_client, foreign_client])
    await db_session.flush()

    result = await FinanceDimensionalService(db_session).lookup(
        auth_context.workspace_id, entity_type="client", search="Cliente", limit=20
    )

    assert result["items"] == [{"id": str(own_client.id), "label": "Cliente Visível Financeiro"}]
    assert str(foreign_client.id) not in {item["id"] for item in result["items"]}


@pytest.mark.asyncio
async def test_tax_profile_audit_persists_real_sanitized_before_and_after(db_session, auth_context):
    first = TaxProfileWrite(
        regime="simples_anexo_iv",
        tax_rate=Decimal("12.50"),
        effective_from=date(2026, 1, 1),
        municipality="Brasília",
        sources=[{"type": "contador", "reference": "Parecer interno 1"}],
        reform_calendar={"transitionStart": "2026-01-01"},
        settings={"humanValidationRequired": True},
    )
    created = await upsert_tax_profile(
        first, d=(auth_context.workspace_id, auth_context, db_session)
    )
    second = TaxProfileWrite(
        regime="lucro_presumido",
        tax_rate=Decimal("14.25"),
        effective_from=date(2027, 1, 1),
        municipality="Brasília",
        sources=[{"type": "contador", "reference": "Parecer interno 2"}],
        reform_calendar={"transitionStart": "2027-01-01"},
        settings={"humanValidationRequired": True},
        expected_version=created.version,
    )
    updated = await upsert_tax_profile(
        second, d=(auth_context.workspace_id, auth_context, db_session)
    )

    events = list(
        (
            await db_session.scalars(
                select(KlmAuditEvent)
                .where(
                    KlmAuditEvent.workspace_id == auth_context.workspace_id,
                    KlmAuditEvent.event_type == "finance.tax_profile.updated",
                )
                .order_by(KlmAuditEvent.occurred_at, KlmAuditEvent.id)
            )
        ).all()
    )
    assert len(events) == 2
    update_event = next(event for event in events if event.before_data is not None)
    assert update_event.actor_user_id == auth_context.user_id
    assert update_event.entity_id == updated.id
    assert update_event.before_data["regime"] == "simples_anexo_iv"
    assert update_event.after_data["regime"] == "lucro_presumido"
    assert update_event.before_data["tax_rate"] == "12.5000"
    assert update_event.after_data["tax_rate"] == "14.2500"
    serialized = str(update_event.before_data) + str(update_event.after_data)
    assert all(
        secret not in serialized.lower()
        for secret in ("password", "token", "secret", "storage_path")
    )


def test_tax_reform_catalog_is_versioned_sourced_and_human_gated():
    catalog = tax_rule_catalog(as_of=date(2026, 8, 2))
    assert catalog["schemaVersion"] == 1
    assert catalog["mandatoryHumanGate"] == "contador"
    assert [item["year"] for item in catalog["timeline"]] == [
        2026,
        2027,
        2029,
        2030,
        2031,
        2032,
        2033,
    ]
    assert all(item["sourceUrl"].startswith("https://") for item in catalog["timeline"])
    assert "não" in catalog["disclaimer"].lower()


@pytest.mark.parametrize(
    ("as_of", "expected_offset"),
    [
        (date(2026, 7, 5), 180),
        (date(2026, 10, 3), 90),
        (date(2026, 12, 2), 30),
        (date(2026, 12, 25), 7),
    ],
)
def test_tax_reform_review_windows_are_versioned_and_do_not_backfill_noise(
    as_of, expected_offset
):
    profile = SimpleNamespace(version=7)
    due = due_tax_reform_reminders(profile, as_of=as_of)
    assert len(due) == 1
    assert due[0]["milestoneKey"] == "cbs"
    assert due[0]["offsetDays"] == expected_offset
    assert due[0]["profileVersion"] == 7
    assert due[0]["catalogVersion"] == 1


def test_tax_alerts_never_silently_treat_missing_profile_as_zero():
    assert tax_profile_alerts(None, as_of=date(2026, 8, 2))[0]["code"] == "tax_profile_missing"
    alerts = tax_profile_alerts(
        SimpleNamespace(regime="simples_anexo_iv", sources=[{"reference": "contador"}]),
        as_of=date(2026, 8, 2),
    )
    assert {item["code"] for item in alerts} >= {
        "cpp_outside_das_review",
        "reform_test_year",
        "reform_transition_pending",
    }


@pytest.mark.asyncio
async def test_tax_profile_rejects_credential_shaped_payload(db_session, auth_context):
    payload = TaxProfileWrite(
        regime="other",
        effective_from=date(2026, 1, 1),
        sources=[{"type": "manual", "api_token": "must-not-be-stored"}],
    )
    with pytest.raises(FinanceDimensionValidationError, match="segredos"):
        await FinanceDimensionalService(db_session).upsert_tax_profile(
            auth_context.workspace_id, payload
        )
