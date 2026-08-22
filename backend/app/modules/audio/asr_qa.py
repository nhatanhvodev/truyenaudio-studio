from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

from sqlalchemy.orm import Session

from app.contracts import QaCategory, QaSeverity


@dataclass(frozen=True)
class AsrIssue:
    category: QaCategory
    severity: QaSeverity
    evidence: str
    suggestion: str
    speech_segment_id: str | None = None


class AsrQaService:
    def __init__(self, session: Session | None = None) -> None:
        self.session = session

    def compare(
        self,
        expected_text: str,
        transcript: str,
        *,
        speech_segment_id: str | None = None,
    ) -> tuple[AsrIssue, ...]:
        expected = _normalize(expected_text)
        actual = _normalize(transcript)
        issues: list[AsrIssue] = []
        expected_numbers = set(_numbers(expected))
        actual_numbers = set(_numbers(actual))
        missing_numbers = sorted(expected_numbers - actual_numbers)
        if missing_numbers:
            issues.append(
                AsrIssue(
                    category=QaCategory.NUMBER_UNIT,
                    severity=QaSeverity.MAJOR,
                    evidence=f"ASR transcript missing numbers: {', '.join(missing_numbers)}",
                    suggestion="Nghe lại đoạn này và sửa audio/narration nếu đúng là thiếu số.",
                    speech_segment_id=speech_segment_id,
                )
            )
        if expected and actual and _token_overlap(expected, actual) < 0.65:
            issues.append(
                AsrIssue(
                    category=QaCategory.COMPLETENESS,
                    severity=QaSeverity.MAJOR,
                    evidence="ASR transcript diverges from expected narration.",
                    suggestion="Nghe lại để kiểm tra thiếu/lặp câu.",
                    speech_segment_id=speech_segment_id,
                )
            )
        return tuple(issues)


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).lower()
    normalized = re.sub(r"[^\w\s/.-]+", " ", normalized, flags=re.UNICODE)
    return re.sub(r"\s+", " ", normalized).strip()


def _numbers(value: str) -> tuple[str, ...]:
    return tuple(re.findall(r"\b\d+(?:[./-]\d+)*\b", value))


def _token_overlap(expected: str, actual: str) -> float:
    expected_tokens = set(expected.split())
    actual_tokens = set(actual.split())
    if not expected_tokens:
        return 1.0
    return len(expected_tokens & actual_tokens) / len(expected_tokens)
