from __future__ import annotations

from dataclasses import dataclass
import re

from app.contracts import QaCategory, QaSeverity


HAN_WHITELIST = frozenset("〇零一二三四五六七八九十百千万亿")
VERSION = "deterministic-qa-v1"


@dataclass(frozen=True)
class QaIssueDraft:
    category: QaCategory
    severity: QaSeverity
    evidence: str
    suggestion: str
    rule_or_model: str


def run_deterministic_qa(
    source: str,
    target: str,
    locked_terms: tuple[tuple[str, str], ...] = (),
    forbidden_forms: tuple[tuple[str, tuple[str, ...]], ...] = (),
) -> tuple[QaIssueDraft, ...]:
    issues: list[QaIssueDraft] = []
    source_text = source or ""
    target_text = target or ""

    if source_text.strip() and not target_text.strip():
        issues.append(
            QaIssueDraft(
                QaCategory.COMPLETENESS,
                QaSeverity.CRITICAL,
                "empty target",
                "Translate the missing segment before approval.",
                f"{VERSION}:empty-target",
            )
        )
        return tuple(issues)

    issues.extend(_length_ratio_issue(source_text, target_text))
    issues.extend(_residual_han_issues(target_text))
    issues.extend(_number_unit_issues(source_text, target_text))
    issues.extend(_locked_term_issues(source_text, target_text, locked_terms))
    issues.extend(_forbidden_form_issues(source_text, target_text, forbidden_forms))
    issues.extend(_repetition_issues(target_text))
    issues.extend(_meta_markdown_issues(target_text))
    issues.extend(_tts_length_issues(target_text))
    return tuple(issues)


def _length_ratio_issue(source: str, target: str) -> tuple[QaIssueDraft, ...]:
    source_len = len(source.strip())
    target_len = len(target.strip())
    if source_len == 0 or target_len == 0:
        return ()
    ratio = target_len / source_len
    if 0.55 <= ratio <= 3.50:
        return ()
    return (
        QaIssueDraft(
            QaCategory.COMPLETENESS,
            QaSeverity.MAJOR,
            f"length ratio {ratio:.2f}",
            "Review the segment for missing or inflated translation.",
            f"{VERSION}:length-ratio",
        ),
    )


def _residual_han_issues(target: str) -> tuple[QaIssueDraft, ...]:
    for char in target:
        if _is_han(char) and char not in HAN_WHITELIST:
            return (
                QaIssueDraft(
                    QaCategory.RESIDUAL_HAN,
                    QaSeverity.MAJOR,
                    char,
                    "Remove untranslated Han characters from the Vietnamese text.",
                    f"{VERSION}:residual-han",
                ),
            )
    return ()


def _number_unit_issues(source: str, target: str) -> tuple[QaIssueDraft, ...]:
    issues: list[QaIssueDraft] = []
    source_tokens = _protected_tokens(source)
    target_tokens = _protected_tokens(target)
    for token in source_tokens:
        if token not in target_tokens:
            issues.append(
                QaIssueDraft(
                    QaCategory.NUMBER_UNIT,
                    QaSeverity.MAJOR,
                    token,
                    "Preserve numeric, percent, money, date, time, rank, and unit facts.",
                    f"{VERSION}:number-unit",
                )
            )
    return tuple(issues)


def _locked_term_issues(
    source: str,
    target: str,
    locked_terms: tuple[tuple[str, str], ...],
) -> tuple[QaIssueDraft, ...]:
    issues: list[QaIssueDraft] = []
    for source_term, target_term in locked_terms:
        if (
            source_term
            and source_term in source
            and target_term
            and target_term not in target
        ):
            issues.append(
                QaIssueDraft(
                    QaCategory.NAME,
                    QaSeverity.MAJOR,
                    f"{source_term}->{target_term}",
                    "Use the locked glossary rendering.",
                    f"{VERSION}:locked-term",
                )
            )
    return tuple(issues)


def _forbidden_form_issues(
    source: str,
    target: str,
    forbidden_forms: tuple[tuple[str, tuple[str, ...]], ...],
) -> tuple[QaIssueDraft, ...]:
    issues: list[QaIssueDraft] = []
    for source_term, forms in forbidden_forms:
        if not source_term or source_term not in source or not forms:
            continue
        for form in forms:
            if form and form in target:
                issues.append(
                    QaIssueDraft(
                        QaCategory.NAME,
                        QaSeverity.MAJOR,
                        f"{source_term} !~ {form}",
                        "Do not use the forbidden glossary rendering.",
                        f"{VERSION}:forbidden-form",
                    )
                )
    return tuple(issues)


def _repetition_issues(target: str) -> tuple[QaIssueDraft, ...]:
    words = re.findall(r"\b\w+\b", target.lower(), flags=re.UNICODE)
    for first, second, third, fourth in zip(
        words,
        words[1:],
        words[2:],
        words[3:],
        strict=False,
    ):
        if first == second == third == fourth:
            return (
                QaIssueDraft(
                    QaCategory.REPETITION,
                    QaSeverity.MINOR,
                    first,
                    "Remove repeated generated text.",
                    f"{VERSION}:repetition",
                ),
            )
    return ()


def _meta_markdown_issues(target: str) -> tuple[QaIssueDraft, ...]:
    markers = ("```", "###", "**", "_Translator", "As an AI", "ban dich:")
    for marker in markers:
        if marker.lower() in target.lower():
            return (
                QaIssueDraft(
                    QaCategory.META_TEXT,
                    QaSeverity.MINOR,
                    marker,
                    "Remove markdown or translator meta text from narration.",
                    f"{VERSION}:meta-markdown",
                ),
            )
    return ()


def _tts_length_issues(target: str) -> tuple[QaIssueDraft, ...]:
    if len(target) <= 4_000:
        return ()
    return (
        QaIssueDraft(
            QaCategory.TTS_LENGTH,
            QaSeverity.MINOR,
            str(len(target)),
            "Split or tighten text before narration.",
            f"{VERSION}:tts-length",
        ),
    )


def _protected_tokens(text: str) -> tuple[str, ...]:
    patterns = (
        r"\b\d{1,2}[:h]\d{2}\b",
        r"\b\d{1,4}[-/]\d{1,2}[-/]\d{1,4}\b",
        r"[$₫]\s?\d+(?:[.,]\d+)*",
        r"\b\d+(?:[.,]\d+)?\s?%",
        r"\b\d+(?:[.,]\d+)?\s?(?:km|m|cm|mm|kg|g|mg|ml|l|VND|USD)\b",
        r"\b(?:rank|top|no\.?)\s?\d+\b",
        r"\b\d+(?:[.,]\d+)?\b",
    )
    tokens: list[str] = []
    for pattern in patterns:
        tokens.extend(
            match.group(0) for match in re.finditer(pattern, text, flags=re.IGNORECASE)
        )
    return tuple(dict.fromkeys(tokens))


def _is_han(char: str) -> bool:
    return "\u4e00" <= char <= "\u9fff"
