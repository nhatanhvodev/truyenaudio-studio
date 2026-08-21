from __future__ import annotations

from dataclasses import dataclass
import difflib
import hashlib
import json
import re


WORDS_PER_SECOND = 2.5
MIN_TARGET_MS = 20_000
MAX_TARGET_MS = 60_000
HARD_MAX_MS = 90_000
NARRATION_VERSION = "single-narrator-v1"

_UNITS = {
    "km": "ki lo met",
    "kg": "ki lo gam",
    "m": "met",
}
_ABBREVIATIONS = {
    "TS.": "Tien si",
    "ThS.": "Thac si",
    "PGS.": "Pho giao su",
    "GS.": "Giao su",
}
_DIGITS = {
    0: "khong",
    1: "mot",
    2: "hai",
    3: "ba",
    4: "bon",
    5: "nam",
    6: "sau",
    7: "bay",
    8: "tam",
    9: "chin",
    10: "muoi",
}


@dataclass(frozen=True)
class NarrationRevision:
    text: str
    sha256: str
    diff: str
    pronunciation_hash: str
    estimated_duration_ms: int


def derive_narration(
    translation_text: str,
    pronunciation_entries: dict[str, str] | None = None,
) -> NarrationRevision:
    pronunciation_entries = pronunciation_entries or {}
    narrated = translation_text
    for source, target in pronunciation_entries.items():
        narrated = narrated.replace(source, target)
    narrated = _expand_dates(narrated)
    narrated = _expand_abbreviations(narrated)
    narrated = _expand_units(narrated)
    narrated = _expand_remaining_numbers(narrated)
    narrated = re.sub(r"\s+", " ", narrated).strip()
    pronunciation_hash = _canonical_sha256(pronunciation_entries)
    return NarrationRevision(
        text=narrated,
        sha256=_sha(narrated),
        diff=_diff(translation_text, narrated),
        pronunciation_hash=pronunciation_hash,
        estimated_duration_ms=estimate_duration_ms(narrated),
    )


def narration_chunks(text: str) -> tuple[str, ...]:
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]
    if not sentences:
        return (text.strip(),) if text.strip() else ()

    chunks: list[str] = []
    current: list[str] = []
    for sentence in sentences:
        if estimate_duration_ms(sentence) > HARD_MAX_MS:
            if current:
                chunks.append(" ".join(current))
                current = []
            chunks.extend(_split_long_sentence(sentence))
            continue
        candidate = " ".join([*current, sentence])
        if current and estimate_duration_ms(candidate) > MAX_TARGET_MS:
            chunks.append(" ".join(current))
            current = [sentence]
        else:
            current.append(sentence)
    if current:
        chunks.append(" ".join(current))
    return tuple(chunks)


def estimate_duration_ms(text: str) -> int:
    words = [word for word in re.split(r"\s+", text.strip()) if word]
    if not words:
        return 0
    return round(len(words) / WORDS_PER_SECOND * 1000)


def _split_long_sentence(sentence: str) -> tuple[str, ...]:
    words = sentence.split()
    max_words = round(HARD_MAX_MS / 1000 * WORDS_PER_SECOND)
    target_words = round(MAX_TARGET_MS / 1000 * WORDS_PER_SECOND)
    size = min(max_words, target_words)
    return tuple(" ".join(words[index : index + size]) for index in range(0, len(words), size))


def _expand_dates(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        day, month, year = match.groups()
        return f"ngay {_number_to_words(int(day))} thang {_number_to_words(int(month))} nam {_number_to_words(int(year))}"

    return re.sub(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", replace, text)


def _expand_abbreviations(text: str) -> str:
    for short, long in _ABBREVIATIONS.items():
        text = text.replace(short, long)
    return text


def _expand_units(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        number, unit = match.groups()
        return f"{_number_to_words(int(number))} {_UNITS[unit.lower()]}"

    units = "|".join(re.escape(unit) for unit in sorted(_UNITS, key=len, reverse=True))
    return re.sub(rf"\b(\d+)\s*({units})\b", replace, text, flags=re.IGNORECASE)


def _expand_remaining_numbers(text: str) -> str:
    return re.sub(r"\b\d+\b", lambda match: _number_to_words(int(match.group(0))), text)


def _number_to_words(value: int) -> str:
    if value <= 10:
        return _DIGITS[value]
    if value < 20:
        return "muoi " + _DIGITS[value - 10]
    if value < 100:
        tens, ones = divmod(value, 10)
        prefix = f"{_DIGITS[tens]} muoi"
        return prefix if ones == 0 else f"{prefix} {_DIGITS[ones]}"
    if value < 1000:
        hundreds, rest = divmod(value, 100)
        prefix = f"{_DIGITS[hundreds]} tram"
        return prefix if rest == 0 else f"{prefix} {_number_to_words(rest)}"
    thousands, rest = divmod(value, 1000)
    prefix = f"{_number_to_words(thousands)} nghin"
    return prefix if rest == 0 else f"{prefix} {_number_to_words(rest)}"


def _diff(before: str, after: str) -> str:
    if before == after:
        return ""
    return "\n".join(
        difflib.unified_diff(
            before.splitlines(),
            after.splitlines(),
            fromfile="translation",
            tofile="narration",
            lineterm="",
        )
    )


def _canonical_sha256(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
