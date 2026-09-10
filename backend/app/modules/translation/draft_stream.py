"""Resumable, bounded draft text stream (task J04).

A provider delta sequence is applied to a draft stream identified by the job
attempt:

- offsets must be monotonic; a replayed frame (same offset) is ignored
  (duplicate) and never re-applied;
- a non-contiguous offset records a **gap** and the caller must resync from
  ``snapshot()`` instead of applying the frame;
- streams are bounded in frames and characters (memory bound), and a
  ``truncated`` flag records that older frames were dropped for resync
  purposes only;
- drafts are never approvable (``is_approvable`` is always False); a terminal
  flush keeps the text for viewing, and cancel keeps the partial draft too.

Non-stream providers call ``apply_segment`` (segmentReady semantics) which
appends the complete segment text as one frame.
"""

from __future__ import annotations

from dataclasses import dataclass, field


MAX_FRAMES = 1_000
MAX_CHARS = 200_000

STATUS_DRAFT = "DRAFT"
STATUS_FINISHED = "FINISHED"
STATUS_CANCELED = "CANCELED"


class DraftStreamClosed(Exception):
    def __init__(self, status: str) -> None:
        self.code = "DRAFT_STREAM_CLOSED"
        super().__init__(f"draft stream already {status}")


@dataclass(frozen=True)
class DeltaFrame:
    attempt_no: int
    offset: int
    text: str
    source: str = "delta"


@dataclass(frozen=True)
class ApplyResult:
    status: str  # applied | duplicate | gap
    offset: int
    text: str = ""


@dataclass
class DraftStream:
    max_frames: int = MAX_FRAMES
    max_chars: int = MAX_CHARS
    _attempt_no: int = 0
    _offset: int = 0
    _text: str = ""
    _status: str = STATUS_DRAFT
    _frames: list[DeltaFrame] = field(default_factory=list)
    _gaps: list[int] = field(default_factory=list)
    truncated: bool = False

    @property
    def status(self) -> str:
        return self._status

    @property
    def offset(self) -> int:
        return self._offset

    @property
    def text(self) -> str:
        return self._text

    @property
    def gaps(self) -> tuple[int, ...]:
        return tuple(self._gaps)

    @property
    def is_approvable(self) -> bool:
        """A draft is never an approved output (J04 / C05)."""
        return False

    def apply(self, frame: DeltaFrame) -> ApplyResult:
        if self._status != STATUS_DRAFT:
            raise DraftStreamClosed(self._status)
        if frame.attempt_no < self._attempt_no:
            # A stale attempt must never rewrite committed draft text.
            return ApplyResult("duplicate", self._offset)
        if frame.attempt_no > self._attempt_no:
            self._attempt_no = frame.attempt_no
        if frame.offset < self._offset:
            return ApplyResult("duplicate", self._offset)
        if frame.offset > self._offset:
            self._gaps.append(frame.offset)
            return ApplyResult("gap", self._offset, self.snapshot_text())
        self._append(frame)
        return ApplyResult("applied", self._offset)

    def apply_segment(self, attempt_no: int, text: str) -> ApplyResult:
        """Non-stream provider path: the whole segment arrives at once."""
        return self.apply(DeltaFrame(attempt_no=attempt_no, offset=self._offset, text=text, source="segmentReady"))

    def snapshot(self, after_offset: int | None = None) -> dict[str, object]:
        """Resync snapshot for reconnecting clients (no provider re-run)."""
        if after_offset is not None and after_offset > self._offset:
            return {"offset": self._offset, "text": self._text, "resync": True}
        return {"offset": self._offset, "text": self._text, "resync": False}

    def snapshot_text(self) -> str:
        return self._text

    def finish(self) -> None:
        if self._status == STATUS_DRAFT:
            self._status = STATUS_FINISHED

    def cancel(self) -> None:
        if self._status == STATUS_DRAFT:
            self._status = STATUS_CANCELED

    def _append(self, frame: DeltaFrame) -> None:
        self._frames.append(frame)
        self._text += frame.text
        self._offset += len(frame.text)
        if len(self._frames) > self.max_frames:
            dropped = len(self._frames) - self.max_frames
            del self._frames[:dropped]
            self.truncated = True
        if len(self._text) > self.max_chars:
            self._text = self._text[-self.max_chars :]
            self.truncated = True
