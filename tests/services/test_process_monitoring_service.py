from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import app.services.process_monitoring_service as process_monitoring_service_module
from app.core.deps import AuthContext
from app.models.billing import BillingInterval, Plan, PlanTier, Subscription, SubscriptionStatus
from app.models.client import Client
from app.models.entitlement import EntitlementKey, PlanEntitlement
from app.models.klm_operations import KlmAuditEvent
from app.models.legal_exception import ExceptionCenterStatus, ExceptionKind, LegalException
from app.models.matter import Matter, PracticeArea
from app.models.notification import Notification
from app.models.process_monitoring import (
    DeadlineStatus,
    MonitoredProcessStatus,
    ProcessPublicationSource,
)
from app.services.entitlement_service import CurrentStockLimitExceededError
from app.services.prazo_calculator import TipoContagem
from app.services.process_monitoring_service import (
    ProcessMonitoringDeadlineStateError,
    ProcessMonitoringDuplicateProcessError,
    ProcessMonitoringInvalidCnjError,
    ProcessMonitoringModuleNotContractedError,
    ProcessMonitoringNotFoundError,
    ProcessMonitoringService,
)

VALID_CNJ_1 = "0001234-37.2026.8.07.0001"
VALID_CNJ_2 = "0007654-13.2026.8.07.0016"


async def _seed_plan(
    db: AsyncSession,
    workspace_id,
    *,
    limit_quantity: int | None,
    enabled: bool = True,
    legal_suite_enabled: bool | None = True,
) -> None:
    plan = Plan(
        name=f"Plano monitoramento {uuid4().hex[:8]}",
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
    entitlements = [
        PlanEntitlement(
            plan_id=plan.id,
            entitlement=EntitlementKey.monitored_processes,
            enabled=enabled,
            limit_quantity=limit_quantity,
            hard_limit=True,
        )
    ]
    if legal_suite_enabled is not None:
        entitlements.append(
            PlanEntitlement(
                plan_id=plan.id,
                entitlement=EntitlementKey.legal_suite,
                enabled=legal_suite_enabled,
                limit_quantity=None,
                hard_limit=True,
            )
        )
    db.add_all(entitlements)
    await db.flush()


async def _seed_plan_without_module(db: AsyncSession, workspace_id) -> None:
    """Assinatura ativa, mas o plano nunca configurou `monitored_processes`."""
    plan = Plan(
        name=f"Plano sem monitoramento {uuid4().hex[:8]}",
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
                entitlement=EntitlementKey.tokens,
                enabled=True,
                limit_quantity=1000,
                hard_limit=True,
            ),
        ]
    )
    await db.flush()


# ── Gate do módulo (add-on) ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_register_process_without_subscription_is_not_contracted(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    service = ProcessMonitoringService(db_session)
    with pytest.raises(ProcessMonitoringModuleNotContractedError):
        await service.register_process(
            auth_context.workspace_id,
            numero_cnj=VALID_CNJ_1,
            created_by_user_id=auth_context.user_id,
        )


@pytest.mark.asyncio
async def test_register_process_with_plan_missing_entitlement_is_not_contracted(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    await _seed_plan_without_module(db_session, auth_context.workspace_id)
    service = ProcessMonitoringService(db_session)
    with pytest.raises(ProcessMonitoringModuleNotContractedError):
        await service.register_process(
            auth_context.workspace_id,
            numero_cnj=VALID_CNJ_1,
            created_by_user_id=auth_context.user_id,
        )


@pytest.mark.parametrize("legal_suite_enabled", [None, False])
@pytest.mark.asyncio
async def test_register_process_requires_enabled_legal_suite(
    db_session: AsyncSession,
    auth_context: AuthContext,
    legal_suite_enabled: bool | None,
) -> None:
    await _seed_plan(
        db_session,
        auth_context.workspace_id,
        limit_quantity=5,
        legal_suite_enabled=legal_suite_enabled,
    )
    with pytest.raises(ProcessMonitoringModuleNotContractedError):
        await ProcessMonitoringService(db_session).register_process(
            auth_context.workspace_id,
            numero_cnj=VALID_CNJ_1,
            created_by_user_id=auth_context.user_id,
        )


@pytest.mark.asyncio
async def test_register_process_with_zero_quota_is_not_contracted(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    await _seed_plan(db_session, auth_context.workspace_id, limit_quantity=0)
    service = ProcessMonitoringService(db_session)
    with pytest.raises(ProcessMonitoringModuleNotContractedError):
        await service.register_process(
            auth_context.workspace_id,
            numero_cnj=VALID_CNJ_1,
            created_by_user_id=auth_context.user_id,
        )


@pytest.mark.asyncio
async def test_list_publications_also_recuses_when_module_not_contracted(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    service = ProcessMonitoringService(db_session)
    with pytest.raises(ProcessMonitoringModuleNotContractedError):
        await service.list_publications(auth_context.workspace_id)


# ── Cadastro ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_register_process_rejects_invalid_cnj(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    await _seed_plan(db_session, auth_context.workspace_id, limit_quantity=5)
    service = ProcessMonitoringService(db_session)
    with pytest.raises(ProcessMonitoringInvalidCnjError):
        await service.register_process(
            auth_context.workspace_id,
            numero_cnj="0001234-56.2026.8.07.0001",  # dígito verificador errado
            created_by_user_id=auth_context.user_id,
        )


@pytest.mark.asyncio
async def test_register_process_rejects_duplicate_numero_cnj(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    await _seed_plan(db_session, auth_context.workspace_id, limit_quantity=5)
    service = ProcessMonitoringService(db_session)
    await service.register_process(
        auth_context.workspace_id,
        numero_cnj=VALID_CNJ_1,
        created_by_user_id=auth_context.user_id,
    )
    with pytest.raises(ProcessMonitoringDuplicateProcessError):
        await service.register_process(
            auth_context.workspace_id,
            numero_cnj=VALID_CNJ_1,
            created_by_user_id=auth_context.user_id,
        )


@pytest.mark.asyncio
async def test_register_process_quota_exceeded_returns_clean_error(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    await _seed_plan(db_session, auth_context.workspace_id, limit_quantity=1)
    service = ProcessMonitoringService(db_session)
    await service.register_process(
        auth_context.workspace_id,
        numero_cnj=VALID_CNJ_1,
        created_by_user_id=auth_context.user_id,
    )
    with pytest.raises(CurrentStockLimitExceededError) as captured:
        await service.register_process(
            auth_context.workspace_id,
            numero_cnj=VALID_CNJ_2,
            created_by_user_id=auth_context.user_id,
        )
    assert captured.value.current_quantity == 1
    assert captured.value.remaining_quantity == 0


@pytest.mark.asyncio
async def test_register_process_inherits_responsible_from_matter(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    await _seed_plan(db_session, auth_context.workspace_id, limit_quantity=5)
    client = Client(workspace_id=auth_context.workspace_id, name="Cliente Monitoramento")
    db_session.add(client)
    await db_session.flush()
    matter = Matter(
        workspace_id=auth_context.workspace_id,
        client_id=client.id,
        practice_area=PracticeArea.civel,
        demand_type="Cobrança",
        responsible_human_id=auth_context.user_id,
    )
    db_session.add(matter)
    await db_session.flush()

    service = ProcessMonitoringService(db_session)
    process = await service.register_process(
        auth_context.workspace_id,
        numero_cnj=VALID_CNJ_1,
        matter_id=matter.id,
        created_by_user_id=auth_context.user_id,
    )
    assert process.responsible_user_id == auth_context.user_id


# ── Pausa / retomada ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pause_and_resume_process_round_trip(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    await _seed_plan(db_session, auth_context.workspace_id, limit_quantity=5)
    service = ProcessMonitoringService(db_session)
    process = await service.register_process(
        auth_context.workspace_id,
        numero_cnj=VALID_CNJ_1,
        created_by_user_id=auth_context.user_id,
    )

    paused = await service.pause_process(auth_context.workspace_id, process.id)
    assert paused.status == MonitoredProcessStatus.pausado

    resumed = await service.resume_process(auth_context.workspace_id, process.id)
    assert resumed.status == MonitoredProcessStatus.ativo


@pytest.mark.asyncio
async def test_resume_process_respects_quota(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    await _seed_plan(db_session, auth_context.workspace_id, limit_quantity=1)
    service = ProcessMonitoringService(db_session)
    process_a = await service.register_process(
        auth_context.workspace_id,
        numero_cnj=VALID_CNJ_1,
        created_by_user_id=auth_context.user_id,
    )
    await service.pause_process(auth_context.workspace_id, process_a.id)
    process_b = await service.register_process(
        auth_context.workspace_id,
        numero_cnj=VALID_CNJ_2,
        created_by_user_id=auth_context.user_id,
    )
    assert process_b.status == MonitoredProcessStatus.ativo

    with pytest.raises(CurrentStockLimitExceededError):
        await service.resume_process(auth_context.workspace_id, process_a.id)


# ── Ingestão idempotente (dedupe) ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_ingest_publication_is_idempotent_by_source_and_external_id(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    await _seed_plan(db_session, auth_context.workspace_id, limit_quantity=5)
    service = ProcessMonitoringService(db_session)
    await service.register_process(
        auth_context.workspace_id,
        numero_cnj=VALID_CNJ_1,
        created_by_user_id=auth_context.user_id,
    )

    kwargs = dict(
        fonte=ProcessPublicationSource.djen,
        external_id="900000001",
        numero_cnj=VALID_CNJ_1,
        conteudo="Fica a parte intimada para contestar em 15 dias úteis.",
        published_at=datetime(2026, 7, 20, tzinfo=UTC),
    )
    first, created_first = await service.ingest_publication(auth_context.workspace_id, **kwargs)
    await db_session.flush()
    second, created_second = await service.ingest_publication(auth_context.workspace_id, **kwargs)

    assert created_first is True
    assert created_second is False
    assert first.id == second.id

    publications = await service.list_publications(auth_context.workspace_id)
    assert len(publications) == 1


@pytest.mark.asyncio
async def test_ingest_publication_reprocessing_fixture_twice_yields_no_duplicate(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    import json
    from pathlib import Path

    await _seed_plan(db_session, auth_context.workspace_id, limit_quantity=5)
    await service_register_all_fixture_processes(db_session, auth_context)

    fixture_path = (
        Path(__file__).resolve().parents[2] / "tests/fixtures/djen/comunica_pje_sample.json"
    )
    payload = json.loads(fixture_path.read_text())

    service = ProcessMonitoringService(db_session)
    for _ in range(2):
        for item in payload["items"]:
            await service.ingest_publication(
                auth_context.workspace_id,
                fonte=ProcessPublicationSource.djen,
                external_id=str(item["id"]),
                numero_cnj=item["numero_processo"],
                conteudo=item["texto"],
                published_at=datetime.fromisoformat(item["data_disponibilizacao"]).replace(
                    tzinfo=UTC
                ),
            )

    publications = await service.list_publications(auth_context.workspace_id)
    assert len(publications) == len(payload["items"])


async def service_register_all_fixture_processes(db_session, auth_context) -> None:
    service = ProcessMonitoringService(db_session)
    for numero in (VALID_CNJ_1, VALID_CNJ_2):
        await service.register_process(
            auth_context.workspace_id,
            numero_cnj=numero,
            created_by_user_id=auth_context.user_id,
        )


# ── Prazo — sugerido, nunca fatal sem confirmação ─────────────────────────


@pytest.mark.asyncio
async def test_ingest_publication_with_deadline_creates_prazo_sugerido_and_notification(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    await _seed_plan(db_session, auth_context.workspace_id, limit_quantity=5)
    service = ProcessMonitoringService(db_session)
    await service.register_process(
        auth_context.workspace_id,
        numero_cnj=VALID_CNJ_1,
        created_by_user_id=auth_context.user_id,
        responsible_user_id=auth_context.user_id,
    )

    publication, created = await service.ingest_publication(
        auth_context.workspace_id,
        fonte=ProcessPublicationSource.djen,
        external_id="deadline-1",
        numero_cnj=VALID_CNJ_1,
        conteudo="Fica intimada a parte requerida para contestar.",
        published_at=datetime(2026, 8, 3, tzinfo=UTC),
        tipo_ato="contestacao",
        dias=15,
        tipo_contagem=TipoContagem.dias_uteis,
    )

    assert created is True
    assert publication.prazo_status == DeadlineStatus.prazo_sugerido
    assert publication.data_fatal is not None
    assert publication.urgencia == "critica"
    assert publication.memoria_calculo  # memória de cálculo persistida

    notifications = (
        await db_session.execute(
            Notification.__table__.select().where(
                Notification.workspace_id == auth_context.workspace_id
            )
        )
    ).fetchall()
    assert len(notifications) == 1


@pytest.mark.asyncio
async def test_confirm_deadline_requires_a_suggested_deadline(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    await _seed_plan(db_session, auth_context.workspace_id, limit_quantity=5)
    service = ProcessMonitoringService(db_session)
    await service.register_process(
        auth_context.workspace_id,
        numero_cnj=VALID_CNJ_1,
        created_by_user_id=auth_context.user_id,
    )
    publication, _ = await service.ingest_publication(
        auth_context.workspace_id,
        fonte=ProcessPublicationSource.djen,
        external_id="no-deadline-1",
        numero_cnj=VALID_CNJ_1,
        conteudo="Certidão de decurso de prazo, sem novo prazo gerado.",
        published_at=datetime(2026, 8, 3, tzinfo=UTC),
    )
    with pytest.raises(ProcessMonitoringDeadlineStateError):
        await service.confirm_deadline(
            auth_context.workspace_id, publication.id, user_id=auth_context.user_id
        )


@pytest.mark.asyncio
async def test_confirm_deadline_is_exactly_once_and_audited(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    await _seed_plan(db_session, auth_context.workspace_id, limit_quantity=5)
    service = ProcessMonitoringService(db_session)
    await service.register_process(
        auth_context.workspace_id,
        numero_cnj=VALID_CNJ_1,
        created_by_user_id=auth_context.user_id,
    )
    publication, _ = await service.ingest_publication(
        auth_context.workspace_id,
        fonte=ProcessPublicationSource.djen,
        external_id="deadline-confirm-1",
        numero_cnj=VALID_CNJ_1,
        conteudo="Fica intimada a parte requerida.",
        published_at=datetime(2026, 8, 3, tzinfo=UTC),
        dias=5,
        tipo_contagem=TipoContagem.dias_uteis,
    )
    assert publication.prazo_status == DeadlineStatus.prazo_sugerido

    confirmed = await service.confirm_deadline(
        auth_context.workspace_id, publication.id, user_id=auth_context.user_id
    )
    assert confirmed.prazo_status == DeadlineStatus.confirmado
    assert confirmed.confirmed_by_user_id == auth_context.user_id
    assert confirmed.confirmed_at is not None

    # Replay idempotente — mesma confirmação de novo não é erro.
    replay = await service.confirm_deadline(
        auth_context.workspace_id, publication.id, user_id=auth_context.user_id
    )
    assert replay.prazo_status == DeadlineStatus.confirmado

    # Não pode dispensar depois de confirmado.
    with pytest.raises(ProcessMonitoringDeadlineStateError):
        await service.dismiss_deadline(
            auth_context.workspace_id, publication.id, user_id=auth_context.user_id
        )

    audit_rows = (
        await db_session.execute(
            KlmAuditEvent.__table__.select().where(
                KlmAuditEvent.entity_id == publication.id,
                KlmAuditEvent.event_type == "process_publication_deadline_confirmed",
            )
        )
    ).fetchall()
    assert len(audit_rows) == 1


@pytest.mark.asyncio
async def test_dismiss_deadline_transitions_and_blocks_confirm_after(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    await _seed_plan(db_session, auth_context.workspace_id, limit_quantity=5)
    service = ProcessMonitoringService(db_session)
    await service.register_process(
        auth_context.workspace_id,
        numero_cnj=VALID_CNJ_1,
        created_by_user_id=auth_context.user_id,
    )
    publication, _ = await service.ingest_publication(
        auth_context.workspace_id,
        fonte=ProcessPublicationSource.djen,
        external_id="deadline-dismiss-1",
        numero_cnj=VALID_CNJ_1,
        conteudo="Ciência de despacho sem ônus imediato.",
        published_at=datetime(2026, 8, 3, tzinfo=UTC),
        dias=5,
        tipo_contagem=TipoContagem.dias_uteis,
    )

    dismissed = await service.dismiss_deadline(
        auth_context.workspace_id, publication.id, user_id=auth_context.user_id
    )
    assert dismissed.prazo_status == DeadlineStatus.dispensado

    with pytest.raises(ProcessMonitoringDeadlineStateError):
        await service.confirm_deadline(
            auth_context.workspace_id, publication.id, user_id=auth_context.user_id
        )


# ── Alerta WhatsApp — só com o gate de entrega externa aberto ────────────


@pytest.mark.asyncio
async def test_whatsapp_alert_is_skipped_when_external_delivery_disabled(
    db_session: AsyncSession, auth_context: AuthContext, monkeypatch
) -> None:
    monkeypatch.setattr(
        process_monitoring_service_module, "external_delivery_allowed", lambda: False
    )
    send_text_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(
        process_monitoring_service_module.PlatformWhatsAppAlertService,
        "send_text",
        send_text_mock,
    )
    await _seed_plan(db_session, auth_context.workspace_id, limit_quantity=5)
    service = ProcessMonitoringService(db_session)
    await service.register_process(
        auth_context.workspace_id,
        numero_cnj=VALID_CNJ_1,
        created_by_user_id=auth_context.user_id,
        responsible_user_id=auth_context.user_id,
    )
    await service.ingest_publication(
        auth_context.workspace_id,
        fonte=ProcessPublicationSource.djen,
        external_id="whatsapp-gate-closed",
        numero_cnj=VALID_CNJ_1,
        conteudo="Nova publicação.",
        published_at=datetime(2026, 8, 3, tzinfo=UTC),
    )
    send_text_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_whatsapp_alert_is_sent_when_external_delivery_enabled(
    db_session: AsyncSession, auth_context: AuthContext, monkeypatch
) -> None:
    monkeypatch.setattr(
        process_monitoring_service_module, "external_delivery_allowed", lambda: True
    )
    send_text_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(
        process_monitoring_service_module.PlatformWhatsAppAlertService,
        "send_text",
        send_text_mock,
    )
    await _seed_plan(db_session, auth_context.workspace_id, limit_quantity=5)

    # Advogado responsável precisa de telefone cadastrado para receber o alerta.
    from app.models.user import User

    responsible = await db_session.get(User, auth_context.user_id)
    responsible.phone = "+5561999999999"
    await db_session.flush()

    service = ProcessMonitoringService(db_session)
    await service.register_process(
        auth_context.workspace_id,
        numero_cnj=VALID_CNJ_1,
        created_by_user_id=auth_context.user_id,
        responsible_user_id=auth_context.user_id,
    )
    await service.ingest_publication(
        auth_context.workspace_id,
        fonte=ProcessPublicationSource.djen,
        external_id="whatsapp-gate-open",
        numero_cnj=VALID_CNJ_1,
        conteudo="Nova publicação.",
        published_at=datetime(2026, 8, 3, tzinfo=UTC),
    )
    send_text_mock.assert_awaited_once()
    call = send_text_mock.await_args
    assert call is not None
    assert call.kwargs["idempotency_key"].startswith("process-publication-alert:")
    assert call.kwargs["source_id"].startswith("process-publication:")
    assert call.kwargs["requested_by_user_id"] == auth_context.user_id


# ── Central de Exceções — publicação sem vínculo confiável ────────────────


@pytest.mark.asyncio
async def test_ingest_publication_without_monitored_process_opens_exception(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    """Antes disto, uma publicação sem MonitoredProcess correspondente
    ficava muda — nem log, nem notificação. Agora abre uma exceção na
    Central de Exceções (Plano Mestre §6/§3)."""
    await _seed_plan(db_session, auth_context.workspace_id, limit_quantity=5)
    service = ProcessMonitoringService(db_session)

    publication, created = await service.ingest_publication(
        auth_context.workspace_id,
        fonte=ProcessPublicationSource.djen,
        external_id="unlinked-001",
        numero_cnj="0009999-99.2026.8.07.0099",
        conteudo="Publicação de um processo nunca cadastrado.",
        published_at=datetime(2026, 7, 20, tzinfo=UTC),
    )
    assert created is True
    assert publication.monitored_process_id is None

    exceptions = (
        await db_session.scalars(
            select_legal_exceptions(auth_context.workspace_id)
        )
    ).all()
    assert len(exceptions) == 1
    exception = exceptions[0]
    assert exception.exception_kind == ExceptionKind.publicacao_sem_vinculo_confiavel
    assert exception.status == ExceptionCenterStatus.aberta
    assert exception.origin == "process_monitoring.ingest_publication"
    assert exception.dedupe_key == "djen:unlinked-001"
    assert exception.payload["numeroCnj"] == "0009999-99.2026.8.07.0099"


@pytest.mark.asyncio
async def test_ingest_publication_without_monitored_process_reingest_does_not_duplicate_exception(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    await _seed_plan(db_session, auth_context.workspace_id, limit_quantity=5)
    service = ProcessMonitoringService(db_session)

    kwargs = dict(
        fonte=ProcessPublicationSource.djen,
        external_id="unlinked-002",
        numero_cnj="0008888-88.2026.8.07.0088",
        conteudo="Publicação de um processo nunca cadastrado.",
        published_at=datetime(2026, 7, 20, tzinfo=UTC),
    )
    await service.ingest_publication(auth_context.workspace_id, **kwargs)
    await db_session.flush()
    await service.ingest_publication(auth_context.workspace_id, **kwargs)

    exceptions = (
        await db_session.scalars(select_legal_exceptions(auth_context.workspace_id))
    ).all()
    assert len(exceptions) == 1


def select_legal_exceptions(workspace_id):
    from sqlalchemy import select as _select

    return _select(LegalException).where(LegalException.workspace_id == workspace_id)


# ── Sprint 4 PR B2: captura da OAB e edição ──────────────────────────────


async def _usuario_de_fora(db: AsyncSession):
    from datetime import UTC, datetime

    from app.models.user import AgentType, User, UserRole

    user = User(
        name="Fora do workspace",
        email=f"fora-{uuid4().hex[:8]}@example.com",
        agent_type=AgentType.human,
        role=UserRole.admin,
        initials="FW",
        color="#111827",
        is_active=True,
        is_internal_staff=False,
        email_verified_at=datetime.now(UTC),
    )
    db.add(user)
    await db.flush()
    return user


@pytest.mark.asyncio
async def test_cadastro_grava_oab_canonica_e_recusa_letra(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    from app.services.process_monitoring_errors import ProcessMonitoringInvalidOabError

    await _seed_plan(db_session, auth_context.workspace_id, limit_quantity=10)
    service = ProcessMonitoringService(db_session)
    processo = await service.register_process(
        auth_context.workspace_id, numero_cnj=VALID_CNJ_1,
        created_by_user_id=auth_context.user_id, oab_vinculada="OAB/DF 068.812",
    )
    assert processo.oab_vinculada == "68812/DF"

    with pytest.raises(ProcessMonitoringInvalidOabError):
        await service.register_process(
            auth_context.workspace_id, numero_cnj=VALID_CNJ_2,
            created_by_user_id=auth_context.user_id, oab_vinculada="68812A/DF",
        )


@pytest.mark.asyncio
async def test_responsavel_tem_de_ser_membro_ativo_no_cadastro_e_na_edicao(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    """O cadastro nunca validava; `_notify_new_publication` manda WhatsApp para esse id."""
    await _seed_plan(db_session, auth_context.workspace_id, limit_quantity=10)
    service = ProcessMonitoringService(db_session)
    intruso = await _usuario_de_fora(db_session)

    with pytest.raises(ProcessMonitoringNotFoundError):
        await service.register_process(
            auth_context.workspace_id, numero_cnj=VALID_CNJ_1,
            created_by_user_id=auth_context.user_id, responsible_user_id=intruso.id,
        )
    processo = await service.register_process(
        auth_context.workspace_id, numero_cnj=VALID_CNJ_1,
        created_by_user_id=auth_context.user_id, responsible_user_id=auth_context.user_id,
    )
    with pytest.raises(ProcessMonitoringNotFoundError):
        await service.update_process(
            auth_context.workspace_id, processo.id, campos={"responsible_user_id": intruso.id}
        )
    await service.update_process(
        auth_context.workspace_id, processo.id, campos={"responsible_user_id": None}
    )
    assert processo.responsible_user_id is None


@pytest.mark.asyncio
async def test_edicao_parcial_e_regra_do_last_error(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    """Quatro cenários em que "limpa quando a OAB vem" mentiria."""
    await _seed_plan(db_session, auth_context.workspace_id, limit_quantity=10)
    service = ProcessMonitoringService(db_session)
    processo = await service.register_process(
        auth_context.workspace_id, numero_cnj=VALID_CNJ_1,
        created_by_user_id=auth_context.user_id, oab_vinculada="68812/DF",
    )
    status_inicial = processo.status

    # 1. erro do DataJud não é da OAB: trocar a OAB não pode apagá-lo
    processo.last_error = "DataJud: HTTP 404 em tjdft"
    await service.update_process(
        auth_context.workspace_id, processo.id, campos={"oab_vinculada": "123456/SP"}
    )
    assert processo.oab_vinculada == "123456/SP"
    assert processo.last_error == "DataJud: HTTP 404 em tjdft"

    # 2. erro do DJEN com a MESMA OAB de novo: nada mudou, o 400 vai voltar
    processo.last_error = "DJEN: HTTP 400 para a OAB 123456/SP"
    await service.update_process(
        auth_context.workspace_id, processo.id, campos={"oab_vinculada": "OAB/SP 123.456"}
    )
    assert processo.last_error == "DJEN: HTTP 400 para a OAB 123456/SP"

    # 3. OAB mudou de fato: o erro do DJEN sai
    await service.update_process(
        auth_context.workspace_id, processo.id, campos={"oab_vinculada": "68812/DF"}
    )
    assert processo.last_error is None

    # 4. só o caso mudou: OAB e erro intocados
    processo.last_error = "DJEN: HTTP 400 para a OAB 68812/DF"
    await service.update_process(
        auth_context.workspace_id, processo.id, campos={"matter_id": None}
    )
    assert processo.oab_vinculada == "68812/DF"
    assert processo.last_error == "DJEN: HTTP 400 para a OAB 68812/DF"

    # null explícito limpa a OAB (e o erro do DJEN, porque a OAB mudou)
    await service.update_process(
        auth_context.workspace_id, processo.id, campos={"oab_vinculada": None}
    )
    assert processo.oab_vinculada is None
    assert processo.last_error is None
    assert processo.status == status_inicial  # PATCH nunca mexe em status (S66)


@pytest.mark.asyncio
async def test_edicao_recusa_caso_de_outro_workspace(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    await _seed_plan(db_session, auth_context.workspace_id, limit_quantity=10)
    service = ProcessMonitoringService(db_session)
    processo = await service.register_process(
        auth_context.workspace_id, numero_cnj=VALID_CNJ_1, created_by_user_id=auth_context.user_id,
    )
    with pytest.raises(ProcessMonitoringNotFoundError):
        await service.update_process(
            auth_context.workspace_id, processo.id, campos={"matter_id": uuid4()}
        )


@pytest.mark.asyncio
async def test_membro_suspenso_e_recusado_quando_explicito_e_ignorado_quando_herdado_do_caso(
    db_session: AsyncSession, auth_context: AuthContext
) -> None:
    """Revisão do #35: nenhum fluxo de suspensão reatribui o caso; o cadastro não pode
    cair por um responsável que o usuário nem preencheu."""
    from app.models.client import Client
    from app.models.matter import Matter
    from app.models.workspace_membership import MembershipStatus, WorkspaceMembership, WorkspaceRole

    await _seed_plan(db_session, auth_context.workspace_id, limit_quantity=10)
    service = ProcessMonitoringService(db_session)
    suspenso = await _usuario_de_fora(db_session)
    db_session.add(
        WorkspaceMembership(
            workspace_id=auth_context.workspace_id, user_id=suspenso.id,
            role=WorkspaceRole.operator, status=MembershipStatus.suspended, is_default=False,
        )
    )
    cliente = Client(workspace_id=auth_context.workspace_id, name="Cliente do caso")
    db_session.add(cliente)
    await db_session.flush()
    caso = Matter(
        workspace_id=auth_context.workspace_id, client_id=cliente.id, demand_type="Demanda",
        responsible_human_id=suspenso.id,
    )
    db_session.add(caso)
    await db_session.flush()

    with pytest.raises(ProcessMonitoringNotFoundError):
        await service.register_process(
            auth_context.workspace_id, numero_cnj=VALID_CNJ_1,
            created_by_user_id=auth_context.user_id, responsible_user_id=suspenso.id,
        )
    herdado = await service.register_process(
        auth_context.workspace_id, numero_cnj=VALID_CNJ_1,
        created_by_user_id=auth_context.user_id, matter_id=caso.id,
    )
    assert herdado.matter_id == caso.id
    assert herdado.responsible_user_id is None  # caiu para "sem responsável", com log
