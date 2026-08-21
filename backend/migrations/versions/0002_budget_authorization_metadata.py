from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("budget_authorizations", sa.Column("category", sa.String(32)))
    op.add_column("budget_authorizations", sa.Column("rate_card_ids_json", sa.JSON()))


def downgrade() -> None:
    op.drop_column("budget_authorizations", "rate_card_ids_json")
    op.drop_column("budget_authorizations", "category")
