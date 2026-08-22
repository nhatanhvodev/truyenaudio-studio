from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "uq_ready_export_manifest",
        "exports",
        ["chapter_id", "kind", "manifest_sha256"],
        unique=True,
        sqlite_where=sa.text("status = 'READY' AND manifest_sha256 IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_ready_export_manifest", table_name="exports")
