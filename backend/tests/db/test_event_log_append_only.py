from __future__ import annotations

from sqlalchemy import text


def test_event_log_allows_multiple_events_for_same_entity(migrated_engine) -> None:
    with migrated_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO event_log (entity_type, entity_id, created_at) VALUES ('job', 'job-1', :created_at)"
            ),
            {"created_at": "2026-09-08T00:00:00+00:00"},
        )
        connection.execute(
            text(
                "INSERT INTO event_log (entity_type, entity_id, created_at) VALUES ('job', 'job-1', :created_at)"
            ),
            {"created_at": "2026-09-08T00:00:01+00:00"},
        )
        assert connection.execute(text("SELECT COUNT(*) FROM event_log WHERE entity_id = 'job-1'")).scalar_one() == 2
