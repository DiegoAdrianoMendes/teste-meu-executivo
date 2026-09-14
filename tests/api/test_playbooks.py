from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext
from app.models.storage_object import StorageAVStatus, StorageObject, StorageObjectState
from app.schemas.playbook import PlaybookCreate, PlaybookScope


pytestmark = pytest.mark.usefixtures("active_subscription")


def _payload() -> dict:
    return PlaybookCreate(
        name="Negativas de plano de saúde",
        description="Playbook comercial e jurídico local.",
        scope=PlaybookScope(
            areas=["negativas_plano_saude"],
            channels=["whatsapp"],
            stages=["triagem", "qualificacao", "proposta", "fechamento"],
            agents=["orquestrador", "sdr", "juridico", "closer", "hermes"],
            offers=["servicos_advocaticios"],
        ),
        autonomy_mode="ai_triage_human_meeting",
    ).model_dump(mode="json", by_alias=True)


@pytest.mark.asyncio
async def test_playbook_http_contract_covers_edit_simulate_publish_and_resolve(
    client: AsyncClient,
):
    created_response = await client.post("/api/v1/playbooks", json=_payload())
    assert created_response.status_code == 201, created_response.text
    created = created_response.json()
    playbook_id = created["id"]
    assert created["state"] == "draft"
    assert created["structuralCoverage"] == 100
    assert len(created["content"]["agents"]) == 20
    assert any(agent["key"] == "auditor" for agent in created["content"]["agents"])
    assert created["content"]["handoff"]["normalDispatcher"] == "orquestrador"
    assert "dailyBudgetBrl" not in created["content"]["policy"]
    assert created["content"]["commercial"]["minimumInitialFeeBrl"] == 4000

    listed = await client.get("/api/v1/playbooks")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [playbook_id]

    content = created["content"]
    content["identityVoice"]["tone"] = "Objetivo, humano e elegante."
    content["policy"]["dailyBudgetBrl"] = 999_999
    draft_body = {
        "name": created["name"],
        "description": created["description"],
        "scope": created["scope"],
        "content": content,
        "reason": "Ajuste testado pela API.",
        "expectedEtag": created["etag"],
    }
    saved_response = await client.put(
        f"/api/v1/playbooks/{playbook_id}/draft",
        json=draft_body,
        headers={"If-Match": f'"{created["etag"]}"'},
    )
    assert saved_response.status_code == 200, saved_response.text
    saved = saved_response.json()
    assert saved["currentVersion"] == 2
    assert saved["etag"] != created["etag"]
    assert "dailyBudgetBrl" not in saved["content"]["policy"]

    stale = await client.put(
        f"/api/v1/playbooks/{playbook_id}/draft",
        json=draft_body,
        headers={"If-Match": f'"{created["etag"]}"'},
    )
    assert stale.status_code == 412
    assert stale.json()["detail"]["code"] == "PB-412"

    simulation_response = await client.post(
        f"/api/v1/playbooks/{playbook_id}/simulations",
        json={
            "scenario": "Medicamento de alto custo",
            "customerMessage": "Meu plano negou o medicamento.",
            "context": {"channel": "whatsapp", "localOnly": True},
        },
    )
    assert simulation_response.status_code == 200, simulation_response.text
    simulation = simulation_response.json()
    assert simulation["version"] == 2
    assert simulation["externalActionsExecuted"] == 0
    assert "estimatedCostBrl" not in simulation
    assert any(source.startswith("digest:") for source in simulation["sources"])
    assert any(trace["rule"] == "policy.autonomy_mode" for trace in simulation["policyTrace"])

    for action in ("submit", "approve", "publish"):
        response = await client.post(
            f"/api/v1/playbooks/{playbook_id}/{action}",
            json={"reason": f"Ação {action} aprovada localmente."},
        )
        assert response.status_code == 200, response.text
    assert response.json()["state"] == "published"
    assert response.json()["publishedVersion"] == 2

    effective = await client.get(
        "/api/v1/playbooks/effective",
        params={
            "area": "negativas_plano_saude",
            "channel": "whatsapp",
            "stage": "triagem",
            "agent": "sdr",
            "offer": "servicos_advocaticios",
        },
    )
    assert effective.status_code == 200, effective.text
    assert effective.json()["id"] == playbook_id
    assert effective.json()["state"] == "published"

    metrics = await client.get(f"/api/v1/playbooks/{playbook_id}/metrics")
    assert metrics.status_code == 200
    assert metrics.json()["simulations"] == 1
    assert metrics.json()["passRate"] == 100
    assert metrics.json()["agentsCovered"] == 20
    assert "averageCostBrl" not in metrics.json()

    diff = await client.get(
        f"/api/v1/playbooks/{playbook_id}/diff",
        params={"from_version": 1, "to_version": 2},
    )
    assert diff.status_code == 200, diff.text
    assert "dailyBudgetBrl" not in diff.text

    rollback = await client.post(
        f"/api/v1/playbooks/{playbook_id}/rollback/2",
        json={"reason": "Abrir nova revisão da versão publicada."},
    )
    assert rollback.status_code == 200, rollback.text
    assert rollback.json()["currentVersion"] == 3
    assert rollback.json()["state"] == "draft"
    assert rollback.json()["publishedVersion"] == 2


@pytest.mark.asyncio
async def test_office_template_resolution_is_fail_closed_over_http(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_context: AuthContext,
):
    """Item 3 da Frente C sobre o transporte HTTP: sem modelo enviado, a
    resolução volta 409 com mensagem clara; com modelo enviado e publicado,
    resolve o `document_object_id`/`version` fixados pelo escritório."""
    created_response = await client.post("/api/v1/playbooks", json=_payload())
    assert created_response.status_code == 201, created_response.text
    created = created_response.json()
    playbook_id = created["id"]

    blocked = await client.get(
        "/api/v1/playbooks/office-templates/contrato_honorarios/resolve",
        params={
            "area": "negativas_plano_saude",
            "channel": "whatsapp",
            "stage": "triagem",
            "agent": "sdr",
            "offer": "servicos_advocaticios",
        },
    )
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["detail"]["code"] == "PB-409-OFFICE-TEMPLATE-MISSING"
    assert "Envie o modelo timbrado" in blocked.json()["detail"]["message"]

    object_id = uuid4()
    db_session.add(
        StorageObject(
            workspace_id=auth_context.workspace_id,
            object_id=object_id,
            version=1,
            original_filename="contrato-honorarios-klm.docx",
            declared_content_type=(
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            ),
            detected_content_type=(
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            ),
            byte_size=4096,
            checksum_sha256="c" * 64,
            storage_key=f"playbooks/office-templates/{object_id}/v1",
            state=StorageObjectState.available,
            av_status=StorageAVStatus.simulated_unverified,
        )
    )
    await db_session.flush()

    content = created["content"]
    for template in content["officeTemplates"]:
        if template["key"] == "contrato_honorarios":
            template["documentObjectId"] = str(object_id)
            template["documentObjectVersion"] = 1
            template["documentFilename"] = "contrato-honorarios-klm.docx"

    draft = await client.put(
        f"/api/v1/playbooks/{playbook_id}/draft",
        json={
            "name": created["name"],
            "description": created["description"],
            "scope": created["scope"],
            "content": content,
            "reason": "Envio do contrato de honorários timbrado.",
            "expectedEtag": created["etag"],
        },
        headers={"If-Match": f'"{created["etag"]}"'},
    )
    assert draft.status_code == 200, draft.text

    simulated = await client.post(
        f"/api/v1/playbooks/{playbook_id}/simulations",
        json={
            "scenario": "Verificação antes da aprovação.",
            "customerMessage": "Preciso entender o próximo passo.",
            "context": {"channel": "whatsapp", "stage": "triagem"},
        },
    )
    assert simulated.status_code == 200, simulated.text

    for action in ("submit", "approve", "publish"):
        response = await client.post(
            f"/api/v1/playbooks/{playbook_id}/{action}",
            json={"reason": f"Ação {action} aprovada localmente."},
        )
        assert response.status_code == 200, response.text

    resolved = await client.get(
        "/api/v1/playbooks/office-templates/contrato_honorarios/resolve",
        params={
            "area": "negativas_plano_saude",
            "channel": "whatsapp",
            "stage": "triagem",
            "agent": "sdr",
            "offer": "servicos_advocaticios",
        },
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["documentObjectId"] == str(object_id)
    assert resolved.json()["documentObjectVersion"] == 1

    still_blocked = await client.get(
        "/api/v1/playbooks/office-templates/peticao_inicial/resolve",
        params={
            "area": "negativas_plano_saude",
            "channel": "whatsapp",
            "stage": "triagem",
            "agent": "sdr",
            "offer": "servicos_advocaticios",
        },
    )
    assert still_blocked.status_code == 409, still_blocked.text
