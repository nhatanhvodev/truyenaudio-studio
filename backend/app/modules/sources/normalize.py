from __future__ import annotations

from dataclasses import dataclass
import hashlib
import unicodedata


NORMALIZER_VERSION = "nfc-v1"


@dataclass(frozen=True)
class NormalizedSource:
    text: str
    sha256: str
    han_char_count: int
    total_char_count: int
    normalizer_version: str = NORMALIZER_VERSION


def normalize_source(raw: str) -> NormalizedSource:
    normalized = unicodedata.normalize("NFC", raw.replace("\r\n", "\n").replace("\r", "\n"))
    lines = [line.rstrip() for line in normalized.split("\n")]
    text = _limit_blank_lines(lines).strip("\n")
    return NormalizedSource(
        text=text,
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        han_char_count=sum(1 for char in text if "\u4e00" <= char <= "\u9fff"),
        total_char_count=len(text),
    )


def _limit_blank_lines(lines: list[str]) -> str:
    output: list[str] = []
    blank_run = 0
    for line in lines:
        if line == "":
            blank_run += 1
            if blank_run <= 2:
                output.append(line)
            continue
        blank_run = 0
        output.append(line)
    return "\n".join(output)
