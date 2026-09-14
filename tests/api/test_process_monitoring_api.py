from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.billing import BillingInterval, Plan, PlanTier, Subscription, SubscriptionStatus
from app.models.entitlement import EntitlementKey, PlanEntitlement
from app.models.process_monitoring import ProcessPublicationSource
from app.services.prazo_calculator import TipoContagem
from app.services.process_monitoring_service import ProcessMonitoringService

VALID_CNJ_1 = "0001234-37.2026.8.07.0001"
VALID_CNJ_2 = "0007654-13.2026.8.07.0016"


async def _seed_plan(db: AsyncSession, workspace_id, *, limit_quantity: int | None = 5) -> None:
    plan = Plan(
        name=f"Plano API monitoramento {uuid4().hex[:8]}",
        tier=PlanTier.enterprise,
        price_monthly_cents=0,
        price_annually_cents=0,
        max_users=100,
        max_matters=None,
        max_tokens_per_month=1_000_000,
        max_ai_agents=100,
        features={},
        is_active=True,
        is_public=False,
        sort_order=1,
    )
    db.add(plan)
    await db.flush()
    db.add(
        Subscription(
            workspace_id=workspace_id,
            plan_id=plan.id,
            status=SubscriptionStatus.active,
            interval=BillingInterval.monthly,
            amount_cents=0,
            currency="BRL",
        )
    )
    db.add_all(
        [
            PlanEntitlement(
                plan_id=plan.id,
                entitlement=EntitlementKey.legal_suite,
                enabled=True,
                limit_quantity=None,
                hard_limit=True,
            ),
            PlanEntitlement(
                plan_id=plan.id,
                entitlement=EntitlementKey.monitored_processes,
                enabled=True,
                limit_quantity=limit_quantity,
                hard_limit=True,
            ),
        ]
    )
    await db.flush()
    await db.commit()


@pytest.mark.asyncio
@pytest.mark.usefixtures("active_subscription")
async def test_create_process_without_module_contracted_is_clean_403(
    client, db_session: AsyncSession, auth_context
) -> None:
    response = await client.post(
        "/api/v1/processos", json={"numeroCnj": VALID_CNJ_1}
    )
    assert response.status_code == 403, response.text
    assert response.json()["detail"]["code"] == "PROC-MON-001"


@pytest.mark.asyncio
async def test_create_list_pause_resume_process_flow(
    client, db_session: AsyncSession, auth_context
) -> None:
    await _seed_plan(db_session, auth_context.workspace_id)

    create_response = await client.post(
        "/api/v1/processos", json={"numeroCnj": VALID_CNJ_1, "tribunal": "TJDFT"}
    )
    assert create_response.status_code == 201, create_response.text
    process = create_response.json()
    assert process["status"] == "ativo"
    assert process["numeroCnj"] == VALID_CNJ_1

    list_response = await client.get("/api/v1/processos")
    assert list_response.status_code == 200
    assert len(list_response.json()) == 1

    pause_response = await client.post(f"/api/v1/processos/{process['id']}/pausar")
    assert pause_response.status_code == 200
    assert pause_response.json()["status"] == "pausado"

    resume_response = await client.post(f"/api/v1/processos/{process['id']}/retomar")
    assert resume_response.status_code == 200
    assert resume_response.json()["status"] == "ativo"


@pytest.mark.asyncio
async def test_create_process_invalid_cnj_returns_422(
    client, db_session: AsyncSession, auth_context
) -> None:
    await _seed_plan(db_session, auth_context.workspace_id)
    response = await client.post(
        "/api/v1/processos", json={"numeroCnj": "0001234-56.2026.8.07.0001"}
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "PROC-MON-002"


@pytest.mark.asyncio
async def test_create_process_quota_exceeded_returns_409(
    client, db_session: AsyncSession, auth_context
) -> None:
    await _seed_plan(db_session, auth_context.workspace_id, limit_quantity=1)
    first = await client.post("/api/v1/processos", json={"numeroCnj": VALID_CNJ_1})
    assert first.status_code == 201

    second = await client.post("/api/v1/processos", json={"numeroCnj": VALID_CNJ_2})
    assert second.status_code == 409, second.text
    assert second.json()["detail"]["code"] == "ENT-003"


@pytest.mark.asyncio
async def test_list_publications_filter_by_urgencia_and_confirm_deadline(
    client, db_session: AsyncSession, auth_context
) -> None:
    await _seed_plan(db_session, auth_context.workspace_id)
    create_response = await client.post(
        "/api/v1/processos", json={"numeroCnj": VALID_CNJ_1}
    )
    process_id = create_response.json()["id"]

    service = ProcessMonitoringService(db_session)
    publication, _ = await service.ingest_publication(
        auth_context.workspace_id,
        fonte=ProcessPublicationSource.djen,
        external_id="api-test-1",
        numero_cnj=VALID_CNJ_1,
        conteudo="Fica intimada a parte requerida para contestar.",
        published_at=datetime(2026, 8, 3, tzinfo=UTC),
        tipo_ato="contestacao",
        dias=15,
        tipo_contagem=TipoContagem.dias_uteis,
    )
    await db_session.commit()

    list_response = await client.get(
        "/api/v1/processos/publicacoes", params={"urgencia": "critica"}
    )
    assert list_response.status_code == 200, list_response.text
    payload = list_response.json()
    assert len(payload) == 1
    assert payload[0]["prazoStatus"] == "prazo_sugerido"

    empty_response = await client.get(
        "/api/v1/processos/publicacoes", params={"urgencia": "baixa"}
    )
    assert empty_response.json() == []

    confirm_response = await client.post(
        f"/api/v1/processos/publicacoes/{publication.id}/confirmar-prazo", json={}
    )
    assert confirm_response.status_code == 200, confirm_response.text
    confirmed = confirm_response.json()
    assert confirmed["prazoStatus"] == "confirmado"
    assert confirmed["confirmedByUserId"] == str(auth_context.user_id)

    # Segunda confirmação é replay idempotente, não erro.
    replay_response = await client.post(
        f"/api/v1/processos/publicacoes/{publication.id}/confirmar-prazo", json={}
    )
    assert replay_response.status_code == 200
    assert replay_response.json()["prazoStatus"] == "confirmado"

    dismiss_after_confirm = await client.post(
        f"/api/v1/processos/publicacoes/{publication.id}/dispensar-prazo", json={}
    )
    assert dismiss_after_confirm.status_code == 409
    assert dismiss_after_confirm.json()["detail"]["code"] == "PROC-MON-005"

    assert process_id  # sanity: usado para criar o publication acima


@pytest.mark.asyncio
async def test_process_not_found_returns_404(
    client, db_session: AsyncSession, auth_context
) -> None:
    await _seed_plan(db_session, auth_context.workspace_id)
    response = await client.post(f"/api/v1/processos/{uuid4()}/pausar")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "PROC-MON-004"


@pytest.mark.asyncio
@pytest.mark.usefixtures("active_subscription")
async def test_list_deadlines_endpoint_without_module_contracted_is_clean_403(
    client, db_session: AsyncSession, auth_context
) -> None:
    response = await client.get("/api/v1/processos/prazos")
    assert response.status_code == 403, response.text
    assert response.json()["detail"]["code"] == "PROC-MON-001"


@pytest.mark.asyncio
async def test_list_deadlines_endpoint_joins_only_publications_with_prazo_ordered_by_data_fatal(
    client, db_session: AsyncSession, auth_context
) -> None:
    """GET /processos/prazos — só publicações com prazo calculado entram no
    feed (ao contrário de /processos/publicacoes, que traz tudo); a ordem é
    por data fatal mais próxima primeiro."""
    await _seed_plan(db_session, auth_context.workspace_id)
    await client.post("/api/v1/processos", json={"numeroCnj": VALID_CNJ_1})

    service = ProcessMonitoringService(db_session)
    # Publicação sem prazo (mero andamento) — não deve aparecer no feed.
    await service.ingest_publication(
        auth_context.workspace_id,
        fonte=ProcessPublicationSource.datajud,
        external_id="prazos-test-sem-prazo",
        numero_cnj=VALID_CNJ_1,
        conteudo="Andamento processual sem prazo associado.",
        published_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    # Prazo com data fatal mais distante.
    far, _ = await service.ingest_publication(
        auth_context.workspace_id,
        fonte=ProcessPublicationSource.djen,
        external_id="prazos-test-longe",
        numero_cnj=VALID_CNJ_1,
        conteudo="Fica intimada a parte para manifestação em 30 dias.",
        published_at=datetime(2026, 8, 2, tzinfo=UTC),
        tipo_ato="manifestacao",
        dias=30,
        tipo_contagem=TipoContagem.dias_corridos,
    )
    # Prazo com data fatal mais próxima.
    near, _ = await service.ingest_publication(
        auth_context.workspace_id,
        fonte=ProcessPublicationSource.djen,
        external_id="prazos-test-perto",
        numero_cnj=VALID_CNJ_1,
        conteudo="Fica intimada a parte requerida para contestar.",
        published_at=datetime(2026, 8, 3, tzinfo=UTC),
        tipo_ato="contestacao",
        dias=5,
        tipo_contagem=TipoContagem.dias_uteis,
    )
    await db_session.commit()

    response = await client.get("/api/v1/processos/prazos")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload) == 2
    assert [item["id"] for item in payload] == [str(near.id), str(far.id)]
    assert all(item["prazoStatus"] == "prazo_sugerido" for item in payload)

    await client.post(f"/api/v1/processos/publicacoes/{near.id}/confirmar-prazo", json={})

    confirmed_only = await client.get(
        "/api/v1/processos/prazos", params={"status": "confirmado"}
    )
    assert confirmed_only.status_code == 200
    confirmed_payload = confirmed_only.json()
    assert len(confirmed_payload) == 1
    assert confirmed_payload[0]["id"] == str(near.id)

    pending_only = await client.get(
        "/api/v1/processos/prazos", params={"status": "prazo_sugerido"}
    )
    assert pending_only.status_code == 200
    pending_payload = pending_only.json()
    assert len(pending_payload) == 1
    assert pending_payload[0]["id"] == str(far.id)


# ── Sprint 4 PR B2: PATCH e OAB canônica ─────────────────────────────────


@pytest.mark.asyncio
async def test_post_grava_oab_canonica_e_recusa_letra_com_codigo(
    client, db_session: AsyncSession, auth_context
) -> None:
    await _seed_plan(db_session, auth_context.workspace_id)

    ok = await client.post(
        "/api/v1/processos", json={"numeroCnj": VALID_CNJ_1, "oabVinculada": "OAB/DF 068.812"}
    )
    assert ok.status_code == 201, ok.text
    assert ok.json()["oabVinculada"] == "68812/DF"

    ruim = await client.post(
        "/api/v1/processos", json={"numeroCnj": VALID_CNJ_2, "oabVinculada": "68812A/DF"}
    )
    assert ruim.status_code == 422, ruim.text
    assert ruim.json()["detail"]["code"] == "PROC-MON-006"
    assert "letra" in ruim.json()["detail"]["message"]


@pytest.mark.asyncio
async def test_patch_edita_oab_com_semantica_parcial(
    client, db_session: AsyncSession, auth_context
) -> None:
    await _seed_plan(db_session, auth_context.workspace_id)
    criado = (await client.post("/api/v1/processos", json={"numeroCnj": VALID_CNJ_1})).json()
    assert criado["oabVinculada"] is None

    editado = await client.patch(
        f"/api/v1/processos/{criado['id']}", json={"oabVinculada": "oab/df 68.812"}
    )
    assert editado.status_code == 200, editado.text
    assert editado.json()["oabVinculada"] == "68812/DF"
    assert editado.json()["status"] == "ativo"

    vazio = await client.patch(f"/api/v1/processos/{criado['id']}", json={})
    assert vazio.status_code == 200
    assert vazio.json()["oabVinculada"] == "68812/DF"  # corpo vazio é idempotente

    limpo = await client.patch(f"/api/v1/processos/{criado['id']}", json={"oabVinculada": None})
    assert limpo.status_code == 200
    assert limpo.json()["oabVinculada"] is None


@pytest.mark.asyncio
async def test_patch_recusa_oab_invalida_chave_desconhecida_tribunal_e_id_inexistente(
    client, db_session: AsyncSession, auth_context
) -> None:
    await _seed_plan(db_session, auth_context.workspace_id)
    criado = (await client.post("/api/v1/processos", json={"numeroCnj": VALID_CNJ_1})).json()

    invalida = await client.patch(
        f"/api/v1/processos/{criado['id']}", json={"oabVinculada": "123456/XX"}
    )
    assert invalida.status_code == 422
    assert invalida.json()["detail"]["code"] == "PROC-MON-006"

    # erro de digitação na chave não pode virar 200 sem efeito
    chave_errada = await client.patch(
        f"/api/v1/processos/{criado['id']}", json={"oabvinculada": "1/DF"}
    )
    assert chave_errada.status_code == 422

    # tribunal não é editável: o worker deriva do número a cada tick
    tribunal = await client.patch(f"/api/v1/processos/{criado['id']}", json={"tribunal": "tjsp"})
    assert tribunal.status_code == 422

    sumido = await client.patch(f"/api/v1/processos/{uuid4()}", json={"oabVinculada": "1/DF"})
    assert sumido.status_code == 404
    assert sumido.json()["detail"]["code"] == "PROC-MON-004"
