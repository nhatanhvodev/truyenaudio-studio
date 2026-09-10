from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "voice_preview_jobs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("preset_id", sa.String(length=64), nullable=False),
        sa.Column("text_kind", sa.String(length=16), nullable=False),
        sa.Column("text_sha256", sa.String(length=64), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("cache_key", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=True),
        sa.Column("relative_path", sa.Text(), nullable=True),
        sa.Column("audio_sha256", sa.String(length=64), nullable=True),
        sa.Column("duration_ms", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.Column("updated_at", sa.String(length=32), nullable=False),
        sa.CheckConstraint(
            "status IN ('QUEUED', 'RUNNING', 'READY', 'FAILED', 'CANCELLED')",
            name="status_enum",
        ),
        sa.CheckConstraint("text_kind IN ('SAMPLE', 'CUSTOM')", name="text_kind_enum"),
        sa.CheckConstraint(
            "text_sha256 IS NULL OR "
            "(length(text_sha256) = 64 AND text_sha256 NOT GLOB '*[^0-9a-f]*')",
            name="text_sha256_lowercase_sha256",
        ),
        sa.CheckConstraint(
            "cache_key IS NULL OR "
            "(length(cache_key) = 64 AND cache_key NOT GLOB '*[^0-9a-f]*')",
            name="cache_key_lowercase_sha256",
        ),
        sa.CheckConstraint(
            "audio_sha256 IS NULL OR "
            "(length(audio_sha256) = 64 AND audio_sha256 NOT GLOB '*[^0-9a-f]*')",
            name="audio_sha256_lowercase_sha256",
        ),
        sa.UniqueConstraint("cache_key", name="uq_voice_preview_jobs_cache_key"),
    )
    op.create_index(
        "ix_voice_preview_jobs_status_updated_at",
        "voice_preview_jobs",
        ["status", "updated_at"],
    )


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE voice_preview_jobs"))
