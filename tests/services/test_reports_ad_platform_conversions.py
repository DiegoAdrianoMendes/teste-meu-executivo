"""Fonte única de verdade: reports_service e tracking_summary não podem divergir.

Cobre o achado da auditoria de tracking: `ReportsService._ad_platform_conversions`
usava uma heurística de substring ("ads" no haystack) para classificar
plataforma, podendo divergir do motor canônico de atribuição
(`acquisition_attribution_service.extract_attribution`) consumido pela página
Rastreamento (`KlmOperationsService.tracking_summary`). Este teste semeia o
mesmo dado de aquisição e prova que as duas leituras concordam.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db.base import Base
from app.models.workspace import Workspace, WorkspaceSetupMode, WorkspaceStatus
from app.schemas.klm_operations import AcquisitionEventCreate
from app.services.klm_operations_service import KlmOperationsService
from app.services.reports_service import ReportsService


@pytest_asyncio.fixture
async def session_factory(tmp_path):
    database_path = tmp_path / "reports-ad-platform-conversions.sqlite3"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{database_path}",
        poolclass=NullPool,
        connect_args={"timeout": 10},
    )
    async with engine.begin() as connection:
        await connection.exec_driver_sql("PRAGMA journal_mode=WAL")
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


async def _seed_workspace(factory) -> "UUID":  # noqa: F821 - typed for readability only
    async with factory() as db:
        workspace = Workspace(
            name="Reports Ad Platform",
            slug="reports-ad-platform",
            status=WorkspaceStatus.active,
            setup_mode=WorkspaceSetupMode.self_service,
            is_active=True,
            settings={},
        )
        db.add(workspace)
        await db.commit()
        return workspace.id


async def _seed_conversions(factory, workspace_id) -> None:
    """Grava dois eventos de conversão com evidência canônica de plataforma.

    Um via click id do Google Ads (gclid) e outro via provider explícito do
    Meta Ads em `event_metadata`, exatamente como `extract_attribution`
    espera — sem depender de nenhuma string mágica tipo "ads" no lead_source.
    """
    async with factory() as db:
        service = KlmOperationsService(db)
        await service.record_acquisition_event(
            workspace_id,
            None,
            AcquisitionEventCreate(
                event_name="client",
                channel="google_search",
                source="google",
                dedupe_key="conv:google:1",
                value=1500.0,
                click_ids={"gclid": "test-gclid-123"},
                event_metadata={"campaignName": "Busca Institucional"},
            ),
        )
        await service.record_acquisition_event(
            workspace_id,
            None,
            AcquisitionEventCreate(
                event_name="client",
                channel="instagram",
                source="instagram_organic",
                dedupe_key="conv:meta:1",
                value=900.0,
                event_metadata={"provider": "meta_ads", "campaignName": "Stories Q3"},
            ),
        )
        await db.commit()


@pytest.mark.asyncio
async def test_ad_platform_conversions_match_tracking_summary_attribution(session_factory):
    workspace_id = await _seed_workspace(session_factory)
    await _seed_conversions(session_factory, workspace_id)

    async with session_factory() as db:
        tracking = await KlmOperationsService(db).tracking_summary(workspace_id)

    async with session_factory() as db:
        reports_summary = await ReportsService(db).get_summary(workspace_id)

    canonical_conversions_by_provider: dict[str, int] = {}
    canonical_revenue_by_provider: dict[str, float] = {}
    for row in tracking["attribution_rows"]:
        if not row.get("provider"):
            continue
        canonical_conversions_by_provider[row["provider"]] = (
            canonical_conversions_by_provider.get(row["provider"], 0) + int(row["clients"])
        )
        canonical_revenue_by_provider[row["provider"]] = round(
            canonical_revenue_by_provider.get(row["provider"], 0.0)
            + float(row["attributable_revenue"]),
            2,
        )

    assert canonical_conversions_by_provider == {"google_ads": 1, "meta_ads": 1}

    reports_conversions_by_provider: dict[str, int] = {}
    reports_revenue_by_provider: dict[str, float] = {}
    for item in reports_summary.ad_platform_conversions:
        reports_conversions_by_provider[item.platform] = (
            reports_conversions_by_provider.get(item.platform, 0) + item.conversions
        )
        reports_revenue_by_provider[item.platform] = round(
            reports_revenue_by_provider.get(item.platform, 0.0) + item.conversion_value, 2
        )

    # A prova de coerência: mesmo dado semeado -> mesmo número nos dois caminhos.
    assert reports_conversions_by_provider == canonical_conversions_by_provider
    assert reports_revenue_by_provider == canonical_revenue_by_provider


@pytest.mark.asyncio
async def test_ad_platform_conversions_ignores_unattributed_events(session_factory):
    """Evento de conversão sem evidência de plataforma não deve inventar um provider."""
    workspace_id = await _seed_workspace(session_factory)
    async with session_factory() as db:
        await KlmOperationsService(db).record_acquisition_event(
            workspace_id,
            None,
            AcquisitionEventCreate(
                event_name="client",
                channel="whatsapp",
                source="indicacao",
                dedupe_key="conv:organic:1",
                value=500.0,
            ),
        )
        await db.commit()

    async with session_factory() as db:
        reports_summary = await ReportsService(db).get_summary(workspace_id)

    assert reports_summary.ad_platform_conversions == []
