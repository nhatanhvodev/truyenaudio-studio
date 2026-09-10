"""Persist provider stream deltas into the workspace draft store (J04 round 4).

The worker consumes provider deltas while a TRANSLATE job runs; this factory
turns each delta into an append on ``workspace_drafts`` (U04 store), so the API
process can serve ``/api/jobs/{id}/draft`` (and its SSE feed) with the same
offsets without sharing a session with the worker.

Design notes:

- the sink is keyed by (chapter, base source revision, segment) and the offset
  is the position of the delta inside that segment, which is exactly what
  ``append_draft_delta`` uses for duplicate/gap detection;
- ``bind(session)`` makes ``TranslationWorkflow`` write the draft **inside the
  run's transaction**. That is required on SQLite (single writer): a second
  connection would block or time out while the workflow holds the write
  transaction, and the draft is then committed atomically with the run, so the
  API process reads it right after the job's commit;
- without a bound session the factory opens its own engine (one per attempt,
  disposed by ``close()``), which is what a non-transactional caller should use;
- the draft is auxiliary: ``TranslationWorkflow`` swallows sink failures, so a
  locked or oversized draft never fails a paid translation run.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.db.base import create_engine_for, session_factory
from app.modules.translation.drafts import DraftAppendResult, append_draft_delta


@dataclass
class DraftDeltaSinkFactory:
    """Callable factory used as ``TranslationWorkflow(draft_sink=...)``."""

    database_path: object
    session: Session | None = None
    applied: int = 0
    duplicates: int = 0
    gaps: int = 0
    _engine: object | None = field(default=None, repr=False)

    def bind(self, session: Session) -> DraftDeltaSinkFactory:
        """Use the caller's open transaction for draft writes (no own commit)."""
        self.session = session
        return self

    def __call__(self, chapter_id: str, base_revision_id: str, segment_id: str):
        def sink(delta: str, offset: int | None = None) -> DraftAppendResult:
            return self._append(chapter_id, base_revision_id, segment_id, delta, offset)

        return sink

    def _append(
        self,
        chapter_id: str,
        base_revision_id: str,
        segment_id: str,
        delta: str,
        offset: int | None,
    ) -> DraftAppendResult:
        if self.session is not None:
            result = append_draft_delta(
                self.session,
                chapter_id,
                base_revision_id,
                segment_id,
                delta,
                offset=offset,
                commit=False,
            )
        else:
            engine = self._get_engine()
            with session_factory(engine)() as session:
                result = append_draft_delta(
                    session,
                    chapter_id,
                    base_revision_id,
                    segment_id,
                    delta,
                    offset=offset,
                )
        if result.status == "applied":
            self.applied += 1
        elif result.status == "duplicate":
            self.duplicates += 1
        else:
            self.gaps += 1
        return result

    def _get_engine(self):
        if self._engine is None:
            self._engine = create_engine_for(self.database_path)
        return self._engine

    def close(self) -> None:
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None
