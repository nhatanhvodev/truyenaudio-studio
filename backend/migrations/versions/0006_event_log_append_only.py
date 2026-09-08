from __future__ import annotations

from alembic import op


revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("event_log", recreate="always") as batch:
        batch.drop_constraint("uq_event_log_entity", type_="unique")
        batch.create_index("ix_event_log_entity_sequence", ["entity_type", "entity_id", "sequence_id"])


def downgrade() -> None:
    with op.batch_alter_table("event_log", recreate="always") as batch:
        batch.drop_index("ix_event_log_entity_sequence")
        batch.create_unique_constraint("uq_event_log_entity", ["entity_type", "entity_id"])
