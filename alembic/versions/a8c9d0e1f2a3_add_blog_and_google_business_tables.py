"""add blog and google business tables

Revision ID: a8c9d0e1f2a3
Revises: f7b8c9d0e1f2
Create Date: 2026-04-05 18:40:00.000000
"""
from alembic import op
import sqlalchemy as sa

from app.db.base import FlexibleJSON, FlexibleUUID


# revision identifiers, used by Alembic.
revision = "a8c9d0e1f2a3"
down_revision = "f7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "blog_posts",
        sa.Column("slug", sa.String(length=180), nullable=False),
        sa.Column("title", sa.String(length=220), nullable=False),
        sa.Column("subtitle", sa.Text(), nullable=True),
        sa.Column("excerpt", sa.Text(), nullable=True),
        sa.Column("author_name", sa.String(length=140), nullable=False),
        sa.Column("author_role", sa.String(length=140), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("featured", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("content_blocks", FlexibleJSON(), nullable=False),
        sa.Column("cover_media", FlexibleJSON(), nullable=False),
        sa.Column("og_media", FlexibleJSON(), nullable=False),
        sa.Column("categories", FlexibleJSON(), nullable=False),
        sa.Column("tags", FlexibleJSON(), nullable=False),
        sa.Column("seo", FlexibleJSON(), nullable=False),
        sa.Column("indexing", FlexibleJSON(), nullable=False),
        sa.Column("seo_audit", FlexibleJSON(), nullable=False),
        sa.Column("ai_config", FlexibleJSON(), nullable=False),
        sa.Column("reading_time_minutes", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", FlexibleUUID(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("updated_by_user_id", FlexibleUUID(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_blog_posts_slug"), "blog_posts", ["slug"], unique=True)
    op.create_index(op.f("ix_blog_posts_status"), "blog_posts", ["status"], unique=False)
    op.create_index(op.f("ix_blog_posts_featured"), "blog_posts", ["featured"], unique=False)
    op.create_index(op.f("ix_blog_posts_published_at"), "blog_posts", ["published_at"], unique=False)
    op.create_index(op.f("ix_blog_posts_scheduled_for"), "blog_posts", ["scheduled_for"], unique=False)
    op.create_index(op.f("ix_blog_posts_created_by_user_id"), "blog_posts", ["created_by_user_id"], unique=False)
    op.create_index(op.f("ix_blog_posts_updated_by_user_id"), "blog_posts", ["updated_by_user_id"], unique=False)

    op.create_table(
        "site_media_assets",
        sa.Column("key", sa.String(length=180), nullable=False),
        sa.Column("label", sa.String(length=180), nullable=False),
        sa.Column("mime_type", sa.String(length=120), nullable=False),
        sa.Column("file_kind", sa.String(length=40), nullable=False),
        sa.Column("extension", sa.String(length=20), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("s3_key", sa.String(length=500), nullable=True),
        sa.Column("public_url", sa.Text(), nullable=True),
        sa.Column("alt_text", sa.Text(), nullable=True),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("usage_metadata", FlexibleJSON(), nullable=False),
        sa.Column("generation_prompt", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", FlexibleUUID(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("updated_by_user_id", FlexibleUUID(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_site_media_assets_key"), "site_media_assets", ["key"], unique=True)
    op.create_index(op.f("ix_site_media_assets_status"), "site_media_assets", ["status"], unique=False)
    op.create_index(op.f("ix_site_media_assets_created_by_user_id"), "site_media_assets", ["created_by_user_id"], unique=False)
    op.create_index(op.f("ix_site_media_assets_updated_by_user_id"), "site_media_assets", ["updated_by_user_id"], unique=False)

    op.create_table(
        "google_business_profile_connections",
        sa.Column("business_name", sa.String(length=180), nullable=False),
        sa.Column("account_name", sa.String(length=180), nullable=True),
        sa.Column("location_id", sa.String(length=120), nullable=True),
        sa.Column("location_name", sa.String(length=180), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("profile_url", sa.Text(), nullable=True),
        sa.Column("client_id", sa.Text(), nullable=True),
        sa.Column("client_secret", sa.Text(), nullable=True),
        sa.Column("refresh_token", sa.Text(), nullable=True),
        sa.Column("access_token", sa.Text(), nullable=True),
        sa.Column("scopes", FlexibleJSON(), nullable=False),
        sa.Column("settings", FlexibleJSON(), nullable=False),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_by_user_id", FlexibleUUID(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_google_business_profile_connections_location_id"), "google_business_profile_connections", ["location_id"], unique=False)
    op.create_index(op.f("ix_google_business_profile_connections_status"), "google_business_profile_connections", ["status"], unique=False)
    op.create_index(op.f("ix_google_business_profile_connections_updated_by_user_id"), "google_business_profile_connections", ["updated_by_user_id"], unique=False)

    op.create_table(
        "google_business_publish_jobs",
        sa.Column("connection_id", FlexibleUUID(), sa.ForeignKey("google_business_profile_connections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("target_type", sa.String(length=40), nullable=False),
        sa.Column("target_id", FlexibleUUID(), nullable=True),
        sa.Column("title", sa.String(length=220), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload", FlexibleJSON(), nullable=False),
        sa.Column("provider_response", FlexibleJSON(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", FlexibleUUID(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("id", FlexibleUUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_google_business_publish_jobs_connection_id"), "google_business_publish_jobs", ["connection_id"], unique=False)
    op.create_index(op.f("ix_google_business_publish_jobs_target_type"), "google_business_publish_jobs", ["target_type"], unique=False)
    op.create_index(op.f("ix_google_business_publish_jobs_target_id"), "google_business_publish_jobs", ["target_id"], unique=False)
    op.create_index(op.f("ix_google_business_publish_jobs_status"), "google_business_publish_jobs", ["status"], unique=False)
    op.create_index(op.f("ix_google_business_publish_jobs_scheduled_for"), "google_business_publish_jobs", ["scheduled_for"], unique=False)
    op.create_index(op.f("ix_google_business_publish_jobs_created_by_user_id"), "google_business_publish_jobs", ["created_by_user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_google_business_publish_jobs_created_by_user_id"), table_name="google_business_publish_jobs")
    op.drop_index(op.f("ix_google_business_publish_jobs_scheduled_for"), table_name="google_business_publish_jobs")
    op.drop_index(op.f("ix_google_business_publish_jobs_status"), table_name="google_business_publish_jobs")
    op.drop_index(op.f("ix_google_business_publish_jobs_target_id"), table_name="google_business_publish_jobs")
    op.drop_index(op.f("ix_google_business_publish_jobs_target_type"), table_name="google_business_publish_jobs")
    op.drop_index(op.f("ix_google_business_publish_jobs_connection_id"), table_name="google_business_publish_jobs")
    op.drop_table("google_business_publish_jobs")

    op.drop_index(op.f("ix_google_business_profile_connections_updated_by_user_id"), table_name="google_business_profile_connections")
    op.drop_index(op.f("ix_google_business_profile_connections_status"), table_name="google_business_profile_connections")
    op.drop_index(op.f("ix_google_business_profile_connections_location_id"), table_name="google_business_profile_connections")
    op.drop_table("google_business_profile_connections")

    op.drop_index(op.f("ix_site_media_assets_updated_by_user_id"), table_name="site_media_assets")
    op.drop_index(op.f("ix_site_media_assets_created_by_user_id"), table_name="site_media_assets")
    op.drop_index(op.f("ix_site_media_assets_status"), table_name="site_media_assets")
    op.drop_index(op.f("ix_site_media_assets_key"), table_name="site_media_assets")
    op.drop_table("site_media_assets")

    op.drop_index(op.f("ix_blog_posts_updated_by_user_id"), table_name="blog_posts")
    op.drop_index(op.f("ix_blog_posts_created_by_user_id"), table_name="blog_posts")
    op.drop_index(op.f("ix_blog_posts_scheduled_for"), table_name="blog_posts")
    op.drop_index(op.f("ix_blog_posts_published_at"), table_name="blog_posts")
    op.drop_index(op.f("ix_blog_posts_featured"), table_name="blog_posts")
    op.drop_index(op.f("ix_blog_posts_status"), table_name="blog_posts")
    op.drop_index(op.f("ix_blog_posts_slug"), table_name="blog_posts")
    op.drop_table("blog_posts")
