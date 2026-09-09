from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("budget_authorizations", sa.Column("provider_profile_id", sa.String(36), nullable=True))
    op.add_column("budget_authorizations", sa.Column("provider_profile_revision", sa.Integer(), nullable=True))
    op.add_column("budget_authorizations", sa.Column("cloud_consent_id", sa.String(36), nullable=True))
    op.add_column("budget_authorizations", sa.Column("stage", sa.String(64), nullable=True))
    op.add_column("budget_authorizations", sa.Column("plan_hash", sa.String(64), nullable=True))
    op.add_column("budget_authorizations", sa.Column("quote_hash", sa.String(64), nullable=True))
    op.create_index(
        "ix_budget_authorizations_profile_stage",
        "budget_authorizations",
        ["provider_profile_id", "stage"],
    )


def downgrade() -> None:
    op.drop_index("ix_budget_authorizations_profile_stage", table_name="budget_authorizations")
    op.drop_column("budget_authorizations", "quote_hash")
    op.drop_column("budget_authorizations", "plan_hash")
    op.drop_column("budget_authorizations", "stage")
    op.drop_column("budget_authorizations", "cloud_consent_id")
    op.drop_column("budget_authorizations", "provider_profile_revision")
    op.drop_column("budget_authorizations", "provider_profile_id")
