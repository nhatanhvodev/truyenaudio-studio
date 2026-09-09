from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("story_memory_entries") as batch_op:
        batch_op.add_column(
            sa.Column("status", sa.String(length=16), server_default="APPROVED", nullable=False)
        )
        batch_op.add_column(
            sa.Column(
                "source_run_id",
                sa.String(length=36),
                sa.ForeignKey("translation_runs.id"),
                nullable=True,
            )
        )
        batch_op.add_column(sa.Column("evidence_segment_ids_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("story_memory_entries") as batch_op:
        batch_op.drop_column("evidence_segment_ids_json")
        batch_op.drop_column("source_run_id")
        batch_op.drop_column("status")
