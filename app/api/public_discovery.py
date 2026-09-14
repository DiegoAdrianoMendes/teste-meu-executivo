"""
Saídas públicas para indexação e consumo por bots.
"""
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.services.blog_service import BlogService

router = APIRouter(tags=["Public Discovery"])


@router.get("/sitemap.xml", response_class=Response, summary="Sitemap XML")
async def sitemap_xml(db: AsyncSession = Depends(get_db)) -> Response:
    content = await BlogService(db).render_sitemap_xml()
    return Response(content=content, media_type="application/xml")


@router.get("/robots.txt", response_class=PlainTextResponse, summary="Robots")
async def robots_txt(db: AsyncSession = Depends(get_db)) -> PlainTextResponse:
    return PlainTextResponse(await BlogService(db).render_robots_txt())


@router.get("/rss.xml", response_class=Response, summary="RSS do blog")
async def rss_xml(db: AsyncSession = Depends(get_db)) -> Response:
    return Response(content=await BlogService(db).render_rss_xml(), media_type="application/rss+xml")


@router.get("/llms.txt", response_class=PlainTextResponse, summary="Descoberta para LLMs")
async def llms_txt(db: AsyncSession = Depends(get_db)) -> PlainTextResponse:
    return PlainTextResponse(await BlogService(db).render_llms_txt())


@router.get("/blog/{slug}.md", response_class=PlainTextResponse, summary="Versão markdown do post")
async def blog_post_markdown(slug: str, db: AsyncSession = Depends(get_db)) -> PlainTextResponse:
    return PlainTextResponse(await BlogService(db).render_post_markdown(slug))


@router.get("/blog/{slug}.json", response_class=JSONResponse, summary="Versão JSON do post")
async def blog_post_json(slug: str, db: AsyncSession = Depends(get_db)) -> JSONResponse:
    return JSONResponse(await BlogService(db).get_post_json_payload(slug))
