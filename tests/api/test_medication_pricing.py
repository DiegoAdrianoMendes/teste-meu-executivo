from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.models.client import Client
from app.models.klm_operations import KlmAuditEvent
from app.models.matter import Matter, PracticeArea
from app.models.medication import MatterTreatmentEstimate, MedicationReference
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.services.medication_pricing_service import MedicationPricingService


pytestmark = pytest.mark.usefixtures("active_subscription")


async def _matter(db_session, workspace_id, *, name: str = "Cliente CMED") -> Matter:
    person = Client(workspace_id=workspace_id, name=name, email=f"{uuid4()}@example.com")
    db_session.add(person)
    await db_session.flush()
    matter = Matter(
        workspace_id=workspace_id,
        client_id=person.id,
        practice_area=PracticeArea.saude_suplementar,
        demand_type="Negativa de medicamento",
        health_object_name="Zolgensma",
    )
    db_session.add(matter)
    await db_session.flush()
    return matter


@pytest.mark.asyncio
async def test_packaged_catalog_is_deduplicated_seeded_and_searchable(
    client, db_session
):
    service = MedicationPricingService(db_session)
    assert await service.seed_catalog() == 175
    assert await service.seed_catalog() == 0
    assert int(await db_session.scalar(select(func.count(MedicationReference.id))) or 0) == 175
    assert int(
        await db_session.scalar(
            select(func.count(MedicationReference.id)).where(
                MedicationReference.oncology.is_(True)
            )
        )
        or 0
    ) == 100

    status_response = await client.get("/api/v1/medications/catalog/status")
    assert status_response.status_code == 200, status_response.text
    status_payload = status_response.json()
    assert status_payload["recordCount"] == 175
    assert status_payload["oncologyCount"] == 100
    assert status_payload["priceUnit"] == "apresentacao_cmed"
    assert status_payload["sourceSha256"] == (
        "4cc670c6ab018c2da526c1b087a2be80df543bfcb7d559d45f6165824a733374"
    )

    search_response = await client.get(
        "/api/v1/medications/catalog", params={"q": "onasemnogeno"}
    )
    assert search_response.status_code == 200, search_response.text
    assert [item["productName"] for item in search_response.json()] == ["ZOLGENSMA"]


@pytest.mark.asyncio
async def test_treatment_cost_requires_explicit_cycles_and_is_bound_to_the_matter(
    client, db_session, auth_context
):
    service = MedicationPricingService(db_session)
    await service.seed_catalog()
    matter = await _matter(db_session, auth_context.workspace_id)
    medication = await db_session.scalar(
        select(MedicationReference).where(MedicationReference.product_name == "ZOLGENSMA")
    )
    assert medication is not None

    response = await client.put(
        f"/api/v1/medications/matters/{matter.id}/treatment-estimates",
        json={
            "medicationReferenceId": str(medication.id),
            "presentationsPerCycle": 1,
            "cycleCount": 2,
            "cycleIntervalDays": 30,
            "treatmentDurationDays": 60,
            "prescriptionReference": "Prescrição médica, página 2",
            "reviewStatus": "revisado",
            "applyToMatterEconomicValue": True,
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["costPerPresentation"] == pytest.approx(6_068_815.22)
    assert payload["totalTreatmentCost"] == pytest.approx(12_137_630.44)
    assert payload["calculationFormula"].endswith("2 ciclo(s)")
    assert payload["medication"]["sourceRows"][0]["sheet"] == "Top 120 geral"
    await db_session.refresh(matter)
    assert float(matter.economic_value_annual) == pytest.approx(12_137_630.44)
    assert int(
        await db_session.scalar(
            select(func.count(MatterTreatmentEstimate.id)).where(
                MatterTreatmentEstimate.matter_id == matter.id
            )
        )
        or 0
    ) == 1
    assert int(
        await db_session.scalar(
            select(func.count(KlmAuditEvent.id)).where(
                KlmAuditEvent.workspace_id == auth_context.workspace_id,
                KlmAuditEvent.entity_id == matter.id,
                KlmAuditEvent.event_type == "matter_treatment_estimate_updated",
            )
        )
        or 0
    ) == 1

    invalid = await client.put(
        f"/api/v1/medications/matters/{matter.id}/treatment-estimates",
        json={
            "medicationReferenceId": str(medication.id),
            "presentationsPerCycle": 0,
            "cycleCount": 0,
            "prescriptionReference": "Ausente",
        },
    )
    assert invalid.status_code == 422


@pytest.mark.asyncio
async def test_treatment_endpoint_fails_closed_for_another_workspace(
    client, db_session, auth_context
):
    service = MedicationPricingService(db_session)
    await service.seed_catalog()
    outside = Workspace(
        name="Outro workspace",
        slug=f"outro-{uuid4()}",
        status=WorkspaceStatus.active,
        setup_mode=WorkspaceSetupMode.self_service,
        is_active=True,
        settings={},
    )
    db_session.add(outside)
    await db_session.flush()
    outside_matter = await _matter(db_session, outside.id, name="Cliente de fora")
    medication = await db_session.scalar(select(MedicationReference).limit(1))

    response = await client.put(
        f"/api/v1/medications/matters/{outside_matter.id}/treatment-estimates",
        json={
            "medicationReferenceId": str(medication.id),
            "presentationsPerCycle": 1,
            "cycleCount": 1,
            "prescriptionReference": "Documento externo",
        },
    )
    assert response.status_code == 404
    assert int(await db_session.scalar(select(func.count(MatterTreatmentEstimate.id))) or 0) == 0


@pytest.mark.asyncio
async def test_pricing_policy_enforces_minimum_and_allows_explicit_success_only(
    client, db_session, auth_context
):
    matter = await _matter(db_session, auth_context.workspace_id)
    policy = await client.get("/api/v1/medications/pricing-policy")
    assert policy.status_code == 200, policy.text
    assert policy.json()["minimumInitialFee"] == 4_000
    assert policy.json()["maximumInitialFee"] is None

    below_minimum = await client.patch(
        f"/api/v1/klm/matters/{matter.id}/financial-projection",
        json={
            "fixedFee": 3_999,
            "successPercentage": 20,
            "economicValueAnnual": 100_000,
            "probability": 80,
            "economicObligation": True,
            "reviewStatus": "rascunho",
        },
    )
    assert below_minimum.status_code == 409
    assert "entrada mínima" in below_minimum.json()["detail"].lower()

    success_only = await client.patch(
        f"/api/v1/klm/matters/{matter.id}/financial-projection",
        json={
            "fixedFee": 0,
            "successPercentage": 100,
            "economicValueAnnual": 0,
            "probability": 100,
            "economicObligation": False,
            "paymentMode": "success_only",
            "successBaseType": "moral_damages",
            "moralDamagesEstimate": 30_000,
            "injunctionMilestoneFee": 0,
            "obligationFee": 0,
            "reviewStatus": "revisado",
        },
    )
    assert success_only.status_code == 200, success_only.text
    assert success_only.json()["paymentMode"] == "success_only"
    assert success_only.json()["likely"] == pytest.approx(30_000)
