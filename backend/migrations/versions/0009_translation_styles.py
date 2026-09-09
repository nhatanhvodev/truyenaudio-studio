from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "translation_styles",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("project_id", sa.String(length=36), sa.ForeignKey("projects.id"), nullable=True),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("supersedes_id", sa.String(length=36), sa.ForeignKey("translation_styles.id"), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("genre", sa.String(length=64), nullable=False),
        sa.Column("tone", sa.String(length=64), nullable=False),
        sa.Column("source_language", sa.String(length=32), nullable=False),
        sa.Column("target_language", sa.String(length=32), nullable=False),
        sa.Column("user_instruction", sa.Text(), nullable=False),
        sa.Column("prompt_template_version", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.Column("updated_at", sa.String(length=32), nullable=False),
        sa.Index("ix_translation_styles_project_active", "project_id", "revision_no"),
    )


def downgrade() -> None:
    op.drop_table("translation_styles")
