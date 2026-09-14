"""Home operacional da Central Blackbox, composta conforme o papel interno."""

from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.core.platform_access import require_platform_permission
from app.models.user import User
from app.schemas.platform_dashboard import PlatformDashboardHome
from app.services.platform_dashboard_service import PlatformDashboardService

router = APIRouter(prefix="/platform/dashboard", tags=["Platform Dashboard"])
_dashboard_view = require_platform_permission("dashboard", "view")


@router.get("/home", response_model=PlatformDashboardHome)
async def get_platform_dashboard_home(
    scope: Literal["mine", "team", "platform"] = Query(default="mine"),
    period_days: int = Query(default=30, alias="periodDays", ge=1, le=365),
    current_user: User = Depends(_dashboard_view),
    db: AsyncSession = Depends(get_db),
) -> PlatformDashboardHome:
    """Retorna KPIs, fila e alertas sem incorporar conteudo privado de tenants."""

    return await PlatformDashboardService(db).home(
        user=current_user,
        scope=scope,
        period_days=period_days,
    )
