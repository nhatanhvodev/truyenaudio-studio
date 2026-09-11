from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the optional user-supplied vector index table (task X06).

    Additive only: one new table, no column touched on any existing table, so a
    data root upgraded from any earlier revision keeps every row and simply gains
    an empty memory_indexes table. The vector path stays off until an artifact is
    imported, which is why the table alone never enables retrieval.
    """
    op.create_table(
        "memory_indexes",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("project_id", sa.String(length=36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("embedding_model_id", sa.String(length=255), nullable=False),
        sa.Column("embedding_model_revision", sa.String(length=255), nullable=True),
        sa.Column("dimension", sa.Integer(), nullable=False),
        sa.Column("license", sa.Text(), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("data_source", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("disabled", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("entry_count", sa.Integer(), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("index_hash", sa.String(length=64), nullable=False),
        sa.Column("artifact_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.Column("updated_at", sa.String(length=32), nullable=False),
        sa.CheckConstraint("status IN ('READY', 'STALE', 'FAILED')", name="status_enum"),
        sa.CheckConstraint("data_source IN ('USER_SUPPLIED')", name="data_source_enum"),
        sa.CheckConstraint(
            "source_hash IS NULL OR "
            "(length(source_hash) = 64 AND source_hash NOT GLOB '*[^0-9a-f]*')",
            name="source_hash_lowercase_sha256",
        ),
        sa.CheckConstraint(
            "index_hash IS NULL OR "
            "(length(index_hash) = 64 AND index_hash NOT GLOB '*[^0-9a-f]*')",
            name="index_hash_lowercase_sha256",
        ),
        sa.UniqueConstraint("project_id", name="uq_memory_indexes_project_id"),
    )
    op.create_index(
        "ix_memory_indexes_status_updated_at",
        "memory_indexes",
        ["status", "updated_at"],
    )


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE memory_indexes"))
