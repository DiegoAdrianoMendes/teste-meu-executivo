"""
Endpoints públicos do blog.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.schemas.blog import BlogCategoryPayloadRead, BlogIndexRead, BlogPostRead, BlogPostSummaryRead
from app.services.blog_service import BlogService

router = APIRouter(prefix="/public/blog", tags=["Public Blog"])


@router.get("", response_model=BlogIndexRead, summary="Index público do blog")
async def get_public_blog_index(db: AsyncSession = Depends(get_db)) -> BlogIndexRead:
    return await BlogService(db).get_public_blog_index()


@router.get("/posts", response_model=list[BlogPostSummaryRead], summary="Lista de posts públicos")
async def list_public_blog_posts(
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> list[BlogPostSummaryRead]:
    return await BlogService(db).list_public_posts(limit=limit)


@router.get("/category/{category_slug}", response_model=BlogCategoryPayloadRead, summary="Posts públicos por categoria")
async def get_public_blog_category(
    category_slug: str,
    db: AsyncSession = Depends(get_db),
) -> BlogCategoryPayloadRead:
    return await BlogService(db).get_public_posts_by_category(category_slug)


@router.get("/{slug}", response_model=BlogPostRead, summary="Post público do blog")
async def get_public_blog_post(slug: str, db: AsyncSession = Depends(get_db)) -> BlogPostRead:
    return await BlogService(db).get_public_post(slug)
