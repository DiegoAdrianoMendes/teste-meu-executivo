"""
Endpoints públicos do site institucional.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.schemas.public_site import PublicHomePayloadRead, PublicLegalDocumentRead, PublicSiteSettingsRead
from app.services.public_site_service import PublicSiteService

router = APIRouter(prefix="/public", tags=["Public Site"])


@router.get("/site", response_model=PublicSiteSettingsRead, summary="Configurações públicas do site")
async def get_public_site_settings(db: AsyncSession = Depends(get_db)) -> PublicSiteSettingsRead:
    return await PublicSiteService(db).get_public_site_settings()


@router.get("/home", response_model=PublicHomePayloadRead, summary="Conteúdo publicado da home")
async def get_public_home(db: AsyncSession = Depends(get_db)) -> PublicHomePayloadRead:
    return await PublicSiteService(db).get_public_home()


@router.get("/legal/{kind}", response_model=PublicLegalDocumentRead, summary="Documento legal publicado")
async def get_public_legal_document(kind: str, db: AsyncSession = Depends(get_db)) -> PublicLegalDocumentRead:
    return await PublicSiteService(db).get_public_legal(kind)
