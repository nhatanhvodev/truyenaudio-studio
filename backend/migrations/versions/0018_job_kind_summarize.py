from __future__ import annotations

from alembic import op


revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


# The jobs.kind check constraint is a frozen snapshot of JobKind per migration
# (see 0001_canonical_schema). SUMMARIZE is appended, so every existing kind
# keeps its position and the constraint stays a strict whitelist.
_JOB_KINDS_BEFORE = (
    "IMPORT",
    "TRANSLATE",
    "REVIEW",
    "REPAIR_TRANSLATION",
    "PREVIEW_TTS",
    "SYNTHESIZE",
    "MASTER",
    "AUDIO_QA",
    "EXPORT",
)
_JOB_KINDS_AFTER = (*_JOB_KINDS_BEFORE, "SUMMARIZE")


def _kind_check(values: tuple[str, ...]) -> str:
    return "kind IN (" + ", ".join(f"'{value}'" for value in values) + ")"


def _replace_kind_check(values: tuple[str, ...]) -> None:
    """Rewrite ck_jobs_kind_enum on SQLite (table rebuild, data preserved).

    The bare name "kind_enum" is deliberate: the naming convention
    (ck_%(table_name)s_%(constraint_name)s) expands it to the
    ck_jobs_kind_enum name the frozen schema already uses, so the rebuilt
    table keeps the same constraint identity as the ORM metadata.
    """
    with op.batch_alter_table("jobs", recreate="always") as batch:
        batch.drop_constraint("kind_enum", type_="check")
        batch.create_check_constraint("kind_enum", _kind_check(values))


def upgrade() -> None:
    """Allow SUMMARIZE story-memory jobs so the worker can claim them."""
    _replace_kind_check(_JOB_KINDS_AFTER)


def downgrade() -> None:
    _replace_kind_check(_JOB_KINDS_BEFORE)
