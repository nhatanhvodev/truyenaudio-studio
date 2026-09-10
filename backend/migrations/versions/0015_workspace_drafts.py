from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspace_drafts",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("project_id", sa.String(length=36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("chapter_id", sa.String(length=36), sa.ForeignKey("chapters.id"), nullable=False),
        sa.Column(
            "base_revision_id",
            sa.String(length=36),
            sa.ForeignKey("source_revisions.id"),
            nullable=False,
        ),
        sa.Column("content_json", sa.JSON(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.Column("updated_at", sa.String(length=32), nullable=False),
        sa.UniqueConstraint(
            "project_id",
            "chapter_id",
            "base_revision_id",
            name="uq_workspace_drafts_scope",
        ),
    )


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE workspace_drafts"))
