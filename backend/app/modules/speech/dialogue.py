from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class DialogueSuggestion:
    start: int
    end: int
    text: str
    confidence: float
    reason: str
    suggested_role_key: str | None = None


QUOTE_PATTERN = re.compile(r'["“”](?P<text>[^"“”]{2,500})["“”]')


def suggest_dialogue_boundaries(text: str) -> tuple[DialogueSuggestion, ...]:
    """Suggest dialogue boundaries without assigning character identity.

    The rule is intentionally conservative: it only marks quoted spans and never
    infers a speaker. Manual assignment remains required by the assisted
    multi-voice workflow.
    """

    suggestions: list[DialogueSuggestion] = []
    for match in QUOTE_PATTERN.finditer(text):
        dialogue_text = match.group("text").strip()
        if not dialogue_text:
            continue
        suggestions.append(
            DialogueSuggestion(
                start=match.start("text"),
                end=match.end("text"),
                text=dialogue_text,
                confidence=0.74,
                reason="quote boundary detected; speaker identity not inferred",
                suggested_role_key=None,
            )
        )
    return tuple(suggestions)
