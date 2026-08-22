from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "event_log",
        sa.Column("sequence_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("entity_type", sa.String(16), nullable=False),
        sa.Column("entity_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.UniqueConstraint("entity_type", "entity_id", name="uq_event_log_entity"),
    )


def downgrade() -> None:
    op.drop_table("event_log")
