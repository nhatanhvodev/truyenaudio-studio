from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add immutable execution provenance while preserving legacy rows."""
    op.create_table(
        "execution_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("schema_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("hash", sa.String(64), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.UniqueConstraint("kind", "hash", name="uq_execution_snapshots_kind_hash"),
        sa.CheckConstraint("kind IN ('model', 'prompt', 'context', 'plan')", name="kind_execution_snapshot_enum"),
        sa.CheckConstraint("schema_version = 1", name="schema_version_supported"),
        sa.CheckConstraint(
            "length(hash) = 64 AND hash NOT GLOB '*[^0-9a-f]*'",
            name="hash_lowercase_sha256",
        ),
    )

    op.add_column("provider_profiles", sa.Column("revision", sa.Integer(), server_default="1", nullable=False))

    with op.batch_alter_table("jobs", recreate="always") as batch:
        batch.add_column(sa.Column("plan_id", sa.String(36), nullable=True))
        batch.create_foreign_key("fk_jobs_plan_id_execution_snapshots", "execution_snapshots", ["plan_id"], ["id"])
        batch.drop_constraint("uq_jobs_idempotency_key", type_="unique")
        batch.create_unique_constraint(
            "uq_jobs_project_kind_idempotency", ["project_id", "kind", "idempotency_key"]
        )

    with op.batch_alter_table("job_attempts", recreate="always") as batch:
        batch.add_column(sa.Column("requested_model", sa.String(255), nullable=True))
        batch.add_column(sa.Column("actual_model", sa.String(255), nullable=True))
        batch.add_column(sa.Column("billing_state", sa.String(32), nullable=True))

    with op.batch_alter_table("usage_ledger", recreate="always") as batch:
        batch.add_column(sa.Column("attempt_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("entry_kind", sa.String(32), nullable=True))
        batch.create_foreign_key("fk_usage_ledger_attempt_id_job_attempts", "job_attempts", ["attempt_id"], ["id"])
        batch.create_unique_constraint("uq_usage_ledger_attempt_entry_kind", ["attempt_id", "entry_kind"])


def downgrade() -> None:
    with op.batch_alter_table("usage_ledger", recreate="always") as batch:
        batch.drop_constraint("uq_usage_ledger_attempt_entry_kind", type_="unique")
        batch.drop_constraint("fk_usage_ledger_attempt_id_job_attempts", type_="foreignkey")
        batch.drop_column("entry_kind")
        batch.drop_column("attempt_id")

    with op.batch_alter_table("job_attempts", recreate="always") as batch:
        batch.drop_column("billing_state")
        batch.drop_column("actual_model")
        batch.drop_column("requested_model")

    with op.batch_alter_table("jobs", recreate="always") as batch:
        batch.drop_constraint("uq_jobs_project_kind_idempotency", type_="unique")
        batch.drop_constraint("fk_jobs_plan_id_execution_snapshots", type_="foreignkey")
        batch.create_unique_constraint("uq_jobs_idempotency_key", ["idempotency_key"])
        batch.drop_column("plan_id")

    op.drop_column("provider_profiles", "revision")
    op.drop_table("execution_snapshots")
