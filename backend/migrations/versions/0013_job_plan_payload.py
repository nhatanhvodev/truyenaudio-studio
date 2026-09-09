from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("plan_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "plan_json")
