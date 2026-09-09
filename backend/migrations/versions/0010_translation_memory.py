from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "translation_memory",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("project_id", sa.String(length=36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("source_text", sa.Text(), nullable=False),
        sa.Column("target_text", sa.Text(), nullable=False),
        sa.Column("source_language", sa.String(length=32), nullable=False),
        sa.Column("target_language", sa.String(length=32), nullable=False),
        sa.Column("style_revision_id", sa.String(length=36), nullable=True),
        sa.Column("glossary_hash", sa.String(length=64), nullable=True),
        sa.Column("approved_run_id", sa.String(length=36), sa.ForeignKey("translation_runs.id"), nullable=False),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.Column("updated_at", sa.String(length=32), nullable=False),
        sa.CheckConstraint(
            "source_hash IS NULL OR (length(source_hash) = 64 AND source_hash NOT GLOB '*[^0-9a-f]*')",
            name="source_hash_lowercase_sha256",
        ),
        sa.CheckConstraint(
            "glossary_hash IS NULL OR (length(glossary_hash) = 64 AND glossary_hash NOT GLOB '*[^0-9a-f]*')",
            name="glossary_hash_lowercase_sha256",
        ),
        sa.Index("ix_translation_memory_project_source", "project_id", "source_hash"),
    )


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE translation_memory"))
