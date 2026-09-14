from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_current_workspace_id, get_db, require_permission
from app.schemas.finance_executive import ExecutiveAnalyticsRead
from app.services.finance_executive_service import FinanceExecutiveService

router = APIRouter(prefix="/finance/v1/executive", tags=["Financeiro — Executivo"])


@router.get("/analytics", response_model=ExecutiveAnalyticsRead)
async def analytics(
    workspace_id: UUID = Depends(get_current_workspace_id),
    _: AuthContext = Depends(require_permission("finance", "view")),
    db: AsyncSession = Depends(get_db),
):
    return await FinanceExecutiveService(db).analytics(workspace_id)
