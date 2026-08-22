from __future__ import annotations

from app.modules.speech.dialogue import suggest_dialogue_boundaries


def test_dialogue_suggestions_mark_quotes_without_identity_claims() -> None:
    suggestions = suggest_dialogue_boundaries('Minh nói: "Ta sẽ quay lại." Rồi cậu bước đi.')

    assert suggestions
    suggestion = suggestions[0]
    assert suggestion.start < suggestion.end
    assert suggestion.text == "Ta sẽ quay lại."
    assert suggestion.suggested_role_key is None
    assert "quote" in suggestion.reason.lower()
