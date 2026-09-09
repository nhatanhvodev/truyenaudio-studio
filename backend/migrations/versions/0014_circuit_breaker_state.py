from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "breaker_states",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("scope_key", sa.String(length=255), nullable=False, unique=True),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False),
        sa.Column("opened_at", sa.String(length=32), nullable=True),
        sa.Column("cooldown_until", sa.String(length=32), nullable=True),
        sa.Column("updated_at", sa.String(length=32), nullable=False),
    )


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE breaker_states"))
