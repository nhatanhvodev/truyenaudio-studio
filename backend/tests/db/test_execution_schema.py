from __future__ import annotations

from sqlalchemy import inspect


def test_execution_schema_has_snapshot_and_provenance_columns(migrated_engine) -> None:
    inspector = inspect(migrated_engine)

    assert "execution_snapshots" in inspector.get_table_names()
    snapshot_columns = {column["name"] for column in inspector.get_columns("execution_snapshots")}
    assert {"id", "kind", "schema_version", "hash", "payload_json", "created_at"} <= snapshot_columns

    job_columns = {column["name"] for column in inspector.get_columns("jobs")}
    assert "plan_id" in job_columns
    attempt_columns = {column["name"] for column in inspector.get_columns("job_attempts")}
    assert {"requested_model", "actual_model", "billing_state"} <= attempt_columns
    profile_columns = {column["name"] for column in inspector.get_columns("provider_profiles")}
    assert "revision" in profile_columns
    ledger_columns = {column["name"] for column in inspector.get_columns("usage_ledger")}
    assert {"attempt_id", "entry_kind"} <= ledger_columns


def test_job_idempotency_is_scoped_by_project_and_kind(migrated_engine) -> None:
    inspector = inspect(migrated_engine)
    unique_constraints = {
        (constraint.get("name"), tuple(constraint.get("column_names") or ()))
        for constraint in inspector.get_unique_constraints("jobs")
    }
    assert ("uq_jobs_project_kind_idempotency", ("project_id", "kind", "idempotency_key")) in unique_constraints
    assert ("uq_jobs_idempotency_key", ("idempotency_key",)) not in unique_constraints


def test_legacy_rows_can_keep_unknown_provenance(migrated_engine) -> None:
    inspector = inspect(migrated_engine)
    for table, names in {
        "jobs": {"plan_id"},
        "job_attempts": {"requested_model", "actual_model", "billing_state"},
        "usage_ledger": {"attempt_id", "entry_kind"},
    }.items():
        columns = {column["name"]: column for column in inspector.get_columns(table)}
        assert all(columns[name]["nullable"] for name in names)
