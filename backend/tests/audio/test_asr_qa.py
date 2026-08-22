from __future__ import annotations

from app.contracts import QaCategory
from app.db.models import Chapter
from app.modules.audio.asr_qa import AsrQaService
from app.settings.config import Settings
from tests.speech.test_single_narrator import _approved_chapter


def test_asr_flags_missing_number_but_does_not_reject_or_approve(db_session) -> None:
    fixture = _approved_chapter(db_session)
    chapter = db_session.get(Chapter, fixture.chapter_id)
    assert chapter is not None
    before_state = chapter.state
    service = AsrQaService(db_session)

    issues = service.compare("Chàng có 12 viên đan.", "Chàng có viên đan.")

    assert any(issue.category is QaCategory.NUMBER_UNIT for issue in issues)
    db_session.refresh(chapter)
    assert chapter.state == before_state


def test_asr_is_off_by_default() -> None:
    assert Settings().enable_asr_backcheck is False
