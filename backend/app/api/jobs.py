from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

from fastapi import APIRouter, Header, HTTPException, Query
from sse_starlette.sse import EventSourceResponse

from app.api.events import _events, _parse_cursor
from app.contracts import JobStatus
from app.db.base import create_engine_for, session_factory
from app.modules.jobs.retry import RetryDisposition, classify_retry
from app.modules.jobs.runner import JobRunner, JobView
from app.settings.config import Settings
import json


JOB_NOT_FOUND = "JOB_NOT_FOUND"
JOB_CANCEL_NOT_ELIGIBLE = "JOB_CANCEL_NOT_ELIGIBLE"
JOB_RETRY_NOT_ELIGIBLE = "JOB_RETRY_NOT_ELIGIBLE"
JOB_RETRY_NOT_RETRYABLE = "JOB_RETRY_NOT_RETRYABLE"
# Cancel is honoured at the next safe point and cannot guarantee that the
# provider stopped billing for work already in flight (plan J02).
CANCEL_BILLING_NOTE = "PROVIDER_BILLING_NOT_GUARANTEED"


def create_jobs_router(settings: Settings | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/jobs")
    active_settings = settings or Settings()

    @router.get("/snapshot")
    def snapshot(after: str | None = Query(default=None)) -> dict[str, list[dict[str, object]]]:
        engine = create_engine_for(active_settings.data_root / "studio.sqlite3")
        factory = session_factory(engine)
        try:
            with factory() as session:
                return {"events": _job_events_from_durable_log(session, after=after)}
        finally:
            engine.dispose()

    @router.get("/events")
    def events(
        after: str | None = Query(default=None),
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ) -> EventSourceResponse:
        cursor = last_event_id or after

        def stream() -> Iterator[dict[str, str]]:
            engine = create_engine_for(active_settings.data_root / "studio.sqlite3")
            factory = session_factory(engine)
            try:
                with factory() as session:
                    for event in _job_events_from_durable_log(session, after=cursor):
                        yield {
                            "id": str(event["sequenceId"]),
                            "event": "job",
                            "data": json.dumps(event, separators=(",", ":")),
                        }
            finally:
                engine.dispose()

        return EventSourceResponse(stream())

    @router.post("/{job_id}/cancel")
    def cancel_job(job_id: str) -> dict[str, object]:
        """Request cancellation at the next safe point (U07).

        QUEUED jobs cancel immediately; RUNNING jobs move to CANCEL_REQUESTED
        and the worker honours the request at its next safe point. The response
        carries an explicit billing note because a client-side cancel does not
        guarantee the provider stopped charging (plan J02).
        """
        engine = create_engine_for(active_settings.data_root / "studio.sqlite3")
        try:
            runner = JobRunner(engine)
            view = runner.request_cancel(job_id, datetime.now(UTC))
            if view is None:
                if runner.view_job(job_id) is None:
                    raise HTTPException(status_code=404, detail=JOB_NOT_FOUND)
                raise HTTPException(status_code=409, detail=JOB_CANCEL_NOT_ELIGIBLE)
            return {**_job_payload(view), "billingNote": CANCEL_BILLING_NOTE}
        finally:
            engine.dispose()

    @router.post("/{job_id}/retry")
    def retry_job(job_id: str) -> dict[str, object]:
        """Re-enqueue a FAILED job whose error classifies as retryable (U07).

        Manual retry is an explicit user action, never an auto-requeue: only a
        FAILED job whose stored error code the shared classifier would retry
        qualifies. BILLING_UNKNOWN and BLOCKED_BUDGET are never re-sent (cost is
        unknown or budget is still blocking), and the same job row is reused, so
        a double retry can only ever enqueue once.
        """
        engine = create_engine_for(active_settings.data_root / "studio.sqlite3")
        try:
            runner = JobRunner(engine)
            view = runner.view_job(job_id)
            if view is None:
                raise HTTPException(status_code=404, detail=JOB_NOT_FOUND)
            if view.status is not JobStatus.FAILED:
                raise HTTPException(status_code=409, detail=JOB_RETRY_NOT_ELIGIBLE)
            decision = classify_retry(view.error_code or "", attempt_no=1)
            if decision.disposition is not RetryDisposition.RETRY:
                raise HTTPException(status_code=409, detail=JOB_RETRY_NOT_RETRYABLE)
            retried = runner.retry_failed(job_id, datetime.now(UTC))
            if retried is None:
                raise HTTPException(status_code=409, detail=JOB_RETRY_NOT_ELIGIBLE)
            return _job_payload(retried)
        finally:
            engine.dispose()

    return router


def _job_payload(view: JobView) -> dict[str, object]:
    """Job state in the same camelCase shape the feed events use (U07)."""
    return {
        "jobId": view.id,
        "kind": view.kind.value,
        "status": view.status.value,
        "current": view.progress_current,
        "total": view.progress_total,
        "errorCode": view.error_code,
    }


def _job_events_from_durable_log(session, *, after: str | None) -> list[dict[str, object]]:
    cursor = _parse_cursor(after) if after else None
    return [event for event in _events(session, after=cursor) if event["type"] == "job"]
