"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-09-12
"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TEMPLATE_ENUM = sa.Enum(
    "headline_link",
    "headline_link_truncated",
    "headline_link_thread",
    "headline_link_full",
    name="template",
)
VISIBILITY_ENUM = sa.Enum("owner_only", "group", "public", name="visibility")


def upgrade():
    op.create_table(
        "user_cache",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("display_name", sa.String(), nullable=True),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("last_refreshed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "slack_channel_cache",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("last_refreshed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "rss_watch",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("feed_url", sa.String(), nullable=False),
        sa.Column(
            "slack_channel_id",
            sa.String(),
            sa.ForeignKey("slack_channel_cache.id"),
            nullable=False,
        ),
        sa.Column("template", TEMPLATE_ENUM, nullable=False),
        sa.Column(
            "visibility", VISIBILITY_ENUM, nullable=False, server_default="owner_only"
        ),
        sa.Column("owning_group", sa.String(), nullable=True),
        sa.Column(
            "created_by_sub",
            sa.String(),
            sa.ForeignKey("user_cache.id"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("check_interval_seconds", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "consecutive_failure_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "auto_pause_after_failures", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_entry_id", sa.String(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
    )
    op.create_table(
        "api_token",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("token_hash", sa.String(), nullable=False, unique=True),
        sa.Column(
            "owner_sub", sa.String(), sa.ForeignKey("user_cache.id"), nullable=False
        ),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade():
    op.drop_table("api_token")
    op.drop_table("rss_watch")
    op.drop_table("slack_channel_cache")
    op.drop_table("user_cache")
    VISIBILITY_ENUM.drop(op.get_bind(), checkfirst=True)
    TEMPLATE_ENUM.drop(op.get_bind(), checkfirst=True)
