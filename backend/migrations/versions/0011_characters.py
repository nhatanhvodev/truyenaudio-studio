from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "characters",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("project_id", sa.String(length=36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.Column("updated_at", sa.String(length=32), nullable=False),
    )
    op.create_table(
        "character_revisions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("character_id", sa.String(length=36), sa.ForeignKey("characters.id"), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("supersedes_id", sa.String(length=36), sa.ForeignKey("character_revisions.id"), nullable=True),
        sa.Column("canonical_name", sa.Text(), nullable=False),
        sa.Column("aliases_json", sa.JSON(), nullable=True),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("role", sa.String(length=128), nullable=True),
        sa.Column("gender", sa.String(length=16), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "evidence_source_revision_id",
            sa.String(length=36),
            sa.ForeignKey("source_revisions.id"),
            nullable=True,
        ),
        sa.Column("evidence_segment_ids_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.Column("updated_at", sa.String(length=32), nullable=False),
        sa.UniqueConstraint("character_id", "revision_no", name="uq_character_revisions_id_revision"),
    )
    op.create_table(
        "character_relationships",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("project_id", sa.String(length=36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("from_character_id", sa.String(length=36), sa.ForeignKey("characters.id"), nullable=False),
        sa.Column("to_character_id", sa.String(length=36), sa.ForeignKey("characters.id"), nullable=False),
        sa.Column("from_ordinal", sa.Integer(), nullable=False),
        sa.Column("to_ordinal", sa.Integer(), nullable=True),
        sa.Column("addressing_json", sa.JSON(), nullable=True),
        sa.Column(
            "evidence_source_revision_id",
            sa.String(length=36),
            sa.ForeignKey("source_revisions.id"),
            nullable=True,
        ),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.Column("updated_at", sa.String(length=32), nullable=False),
        sa.CheckConstraint(
            "to_ordinal IS NULL OR from_ordinal <= to_ordinal",
            name="character_relationship_ordinal_order",
        ),
        sa.Index("ix_character_relationships_project_ordinal", "project_id", "from_ordinal"),
    )


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE character_relationships"))
    op.execute(sa.text("DROP TABLE character_revisions"))
    op.execute(sa.text("DROP TABLE characters"))
