from __future__ import annotations

import pytest

from app.modules.translation.draft_stream import (
    DeltaFrame,
    DraftStream,
    DraftStreamClosed,
)


def test_split_deltas_apply_in_order_and_advance_offset() -> None:
    stream = DraftStream()

    first = stream.apply(DeltaFrame(attempt_no=1, offset=0, text="Xin "))
    second = stream.apply(DeltaFrame(attempt_no=1, offset=4, text="chào"))

    assert first.status == "applied"
    assert second.status == "applied"
    assert stream.text == "Xin chào"
    assert stream.offset == 8


def test_duplicate_and_stale_attempt_frames_are_ignored() -> None:
    stream = DraftStream()
    stream.apply(DeltaFrame(attempt_no=2, offset=0, text="abc"))

    replay = stream.apply(DeltaFrame(attempt_no=2, offset=0, text="abc"))
    stale = stream.apply(DeltaFrame(attempt_no=1, offset=3, text="ghi"))

    assert replay.status == "duplicate"
    assert stale.status == "duplicate"
    assert stream.text == "abc"


def test_gap_records_offset_and_requires_resync() -> None:
    stream = DraftStream()
    stream.apply(DeltaFrame(attempt_no=1, offset=0, text="abc"))

    gap = stream.apply(DeltaFrame(attempt_no=1, offset=9, text="xyz"))

    assert gap.status == "gap"
    assert gap.text == "abc"
    assert stream.gaps == (9,)
    assert stream.text == "abc"

    # After resync the client re-sends the contiguous frame.
    recovered = stream.apply(DeltaFrame(attempt_no=1, offset=3, text="def"))
    assert recovered.status == "applied"
    assert stream.text == "abcdef"


def test_reconnect_snapshot_reports_server_offset_without_reapplying() -> None:
    stream = DraftStream()
    stream.apply(DeltaFrame(attempt_no=1, offset=0, text="abc"))

    behind = stream.snapshot(after_offset=0)
    ahead = stream.snapshot(after_offset=99)

    assert behind == {"offset": 3, "text": "abc", "resync": False}
    assert ahead["resync"] is True
    assert stream.offset == 3  # reconnect never replays provider frames


def test_cancel_and_terminal_are_races_safe() -> None:
    canceled = DraftStream()
    canceled.apply(DeltaFrame(attempt_no=1, offset=0, text="partial"))
    canceled.cancel()
    with pytest.raises(DraftStreamClosed, match="CANCELED"):
        canceled.apply(DeltaFrame(attempt_no=1, offset=7, text="late"))
    assert canceled.status == "CANCELED"
    assert canceled.text == "partial"  # partial draft kept for viewing

    finished = DraftStream()
    finished.apply(DeltaFrame(attempt_no=1, offset=0, text="done"))
    finished.finish()
    finished.cancel()  # terminal wins after finish
    assert finished.status == "FINISHED"


def test_stream_is_bounded_in_frames_and_characters() -> None:
    stream = DraftStream(max_frames=2, max_chars=5)
    for index in range(4):
        result = stream.apply(DeltaFrame(attempt_no=1, offset=index * 2, text="xy"))
        assert result.status == "applied"

    assert stream.truncated is True
    assert len(stream.text) <= 5
    assert stream.offset == 8


def test_draft_is_never_approvable_and_segment_ready_uses_single_frame() -> None:
    stream = DraftStream()
    result = stream.apply_segment(1, "Cả đoạn đã xong.")

    assert result.status == "applied"
    assert stream.text == "Cả đoạn đã xong."
    assert stream.is_approvable is False
