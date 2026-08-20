from __future__ import annotations

import hashlib

import pytest

from app.modules.translation.segmenter import segment_source


def test_segmenter_segments_reconstruct_source_with_blank_breaks() -> None:
    source = "甲。\n\n乙。\n\n丙。"

    parts = segment_source(source)

    assert "".join(part.source_text for part in parts) == source


def test_segmenter_packs_adjacent_short_paragraphs_toward_target() -> None:
    source = ("甲" * 700 + "。") + "\n\n" + ("乙" * 700 + "。")

    (part,) = segment_source(source)

    assert part.source_text == source
    assert part.paragraph_start == 0
    assert part.paragraph_end == 1


def test_segmenter_never_splits_inside_sentence_or_dialogue() -> None:
    first_dialogue = "“" + ("林" * 1_997) + "”。"
    second_sentence = "风" * 1_200 + "。"
    parts = segment_source(f"{first_dialogue}\n\n{second_sentence}")

    assert len(parts) == 2
    assert parts[0].source_text.endswith("”。")
    assert not parts[0].source_text.endswith("“")
    assert [part.segment_index for part in parts] == [0, 1]
    assert parts[0].paragraph_start == 0
    assert parts[0].paragraph_end == 0
    assert parts[1].paragraph_start == 1
    assert parts[1].paragraph_end == 1


@pytest.mark.parametrize("han_count", [1_199, 1_200, 2_500])
def test_segmenter_keeps_boundary_sized_sentences_whole(han_count: int) -> None:
    source = "甲" * han_count + "。"

    (part,) = segment_source(source)

    assert part.source_text == source
    assert part.segment_kind == "SOURCE"


def test_segmenter_splits_single_overlong_sentence_at_comma_near_max() -> None:
    text = ("甲" * 1_700) + "，" + ("乙" * 800) + "，" + ("丙" * 600) + "。"

    first, second = segment_source(text)

    assert first.segment_kind == "LONG_SENTENCE_SPLIT"
    assert second.segment_kind == "LONG_SENTENCE_SPLIT"
    assert first.source_text.endswith("，")
    assert first.source_text + second.source_text == text
    assert sum(1 for char in first.source_text if "\u4e00" <= char <= "\u9fff") <= 2_500


def test_segmenter_hashes_source_text_and_indexes_deterministically() -> None:
    parts = segment_source(("甲" * 1_200 + "。") + ("\n\n" + "乙" * 1_200 + "。"))

    assert tuple(part.segment_index for part in parts) == tuple(range(len(parts)))
    assert all(
        part.source_sha256
        == hashlib.sha256(part.source_text.encode("utf-8")).hexdigest()
        for part in parts
    )
