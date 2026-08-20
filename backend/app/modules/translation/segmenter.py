from __future__ import annotations

from dataclasses import dataclass
import hashlib


SENTENCE_ENDINGS = frozenset("。！？!?…")
OPEN_QUOTES = frozenset("“「『《")
CLOSE_QUOTES = frozenset("”」』》")
LONG_SPLIT_MARKS = frozenset("，,；;、")


@dataclass(frozen=True)
class SegmentDraft:
    segment_index: int
    paragraph_start: int
    paragraph_end: int
    source_text: str
    source_sha256: str
    segment_kind: str


@dataclass(frozen=True)
class _Unit:
    text: str
    paragraph_index: int
    kind: str = "SOURCE"


def segment_source(
    text: str, min_han: int = 1_200, target_han: int = 2_000, max_han: int = 2_500
) -> tuple[SegmentDraft, ...]:
    if min_han <= 0 or target_han <= 0 or max_han <= 0:
        raise ValueError("SEGMENT_LIMITS_MUST_BE_POSITIVE")
    if min_han > target_han or target_han > max_han:
        raise ValueError("SEGMENT_LIMITS_INVALID")

    segments: list[SegmentDraft] = []
    for paragraph_index, paragraph in enumerate(_paragraphs(text)):
        units = _units_for_paragraph(paragraph, paragraph_index, max_han)
        segments.extend(_pack_units(units, len(segments), min_han, target_han, max_han))
    return tuple(segments)


def _paragraphs(text: str) -> list[str]:
    paragraphs: list[str] = []
    current: list[str] = []
    for line in text.split("\n"):
        if line.strip() == "":
            if current:
                paragraphs.append("\n".join(current))
                current = []
            continue
        current.append(line)
    if current:
        paragraphs.append("\n".join(current))
    return paragraphs


def _units_for_paragraph(
    paragraph: str, paragraph_index: int, max_han: int
) -> tuple[_Unit, ...]:
    units: list[_Unit] = []
    for sentence in _sentences(paragraph):
        if _han_count(sentence) > max_han:
            units.extend(
                _Unit(part, paragraph_index, "LONG_SENTENCE_SPLIT")
                for part in _split_long_sentence(sentence, max_han)
            )
        else:
            units.append(_Unit(sentence, paragraph_index))
    return tuple(units)


def _sentences(paragraph: str) -> tuple[str, ...]:
    sentences: list[str] = []
    start = 0
    quote_depth = 0
    last_non_space = ""
    for index, char in enumerate(paragraph):
        if char in OPEN_QUOTES:
            quote_depth += 1
        elif char in CLOSE_QUOTES and quote_depth > 0:
            quote_depth -= 1

        split_after_char = (char in SENTENCE_ENDINGS and quote_depth == 0) or (
            char in CLOSE_QUOTES
            and quote_depth == 0
            and last_non_space in SENTENCE_ENDINGS
        )
        if split_after_char:
            sentence = paragraph[start : index + 1]
            if sentence.strip():
                sentences.append(sentence)
            start = index + 1
        if not char.isspace():
            last_non_space = char

    tail = paragraph[start:]
    if tail.strip():
        sentences.append(tail)
    return tuple(sentences)


def _split_long_sentence(sentence: str, max_han: int) -> tuple[str, ...]:
    parts: list[str] = []
    remaining = sentence
    while _han_count(remaining) > max_han:
        split_at = _best_long_split_index(remaining, max_han)
        parts.append(remaining[:split_at])
        remaining = remaining[split_at:]
    if remaining:
        parts.append(remaining)
    return tuple(part for part in parts if part)


def _best_long_split_index(text: str, max_han: int) -> int:
    han_seen = 0
    last_mark_index: int | None = None
    hard_index: int | None = None
    for index, char in enumerate(text):
        if _is_han(char):
            han_seen += 1
        if char in LONG_SPLIT_MARKS and 0 < han_seen <= max_han:
            last_mark_index = index + 1
        if hard_index is None and han_seen >= max_han:
            hard_index = index + 1
            break
    return last_mark_index or hard_index or len(text)


def _pack_units(
    units: tuple[_Unit, ...],
    first_index: int,
    min_han: int,
    target_han: int,
    max_han: int,
) -> tuple[SegmentDraft, ...]:
    packed: list[SegmentDraft] = []
    current: list[_Unit] = []
    current_han = 0
    for unit in units:
        unit_han = _han_count(unit.text)
        if current and _should_flush(
            current_han, unit_han, min_han, target_han, max_han
        ):
            packed.append(_draft(first_index + len(packed), current))
            current = []
            current_han = 0
        current.append(unit)
        current_han += unit_han
    if current:
        packed.append(_draft(first_index + len(packed), current))
    return tuple(packed)


def _should_flush(
    current_han: int, next_han: int, min_han: int, target_han: int, max_han: int
) -> bool:
    if current_han + next_han > max_han:
        return True
    return current_han >= min_han and current_han + next_han > target_han


def _draft(segment_index: int, units: list[_Unit]) -> SegmentDraft:
    source_text = "".join(unit.text for unit in units)
    if any(unit.kind == "LONG_SENTENCE_SPLIT" for unit in units):
        segment_kind = "LONG_SENTENCE_SPLIT"
    else:
        segment_kind = "SOURCE"
    return SegmentDraft(
        segment_index=segment_index,
        paragraph_start=units[0].paragraph_index,
        paragraph_end=units[-1].paragraph_index,
        source_text=source_text,
        source_sha256=hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
        segment_kind=segment_kind,
    )


def _han_count(text: str) -> int:
    return sum(1 for char in text if _is_han(char))


def _is_han(char: str) -> bool:
    return "\u4e00" <= char <= "\u9fff"
