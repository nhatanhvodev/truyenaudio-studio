from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("glossary_entries") as batch_op:
        batch_op.add_column(sa.Column("description", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("forbidden_forms", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("evidence", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("scope_from_ordinal", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("scope_to_ordinal", sa.Integer(), nullable=True))
        batch_op.create_check_constraint(
            "glossary_scope_order",
            "scope_from_ordinal IS NULL OR scope_to_ordinal IS NULL OR scope_from_ordinal <= scope_to_ordinal",
        )


def downgrade() -> None:
    with op.batch_alter_table("glossary_entries") as batch_op:
        batch_op.drop_constraint("glossary_scope_order", type_="check")
        batch_op.drop_column("scope_to_ordinal")
        batch_op.drop_column("scope_from_ordinal")
        batch_op.drop_column("evidence")
        batch_op.drop_column("forbidden_forms")
        batch_op.drop_column("description")
