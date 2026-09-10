"""U06 round 2: the context trace must describe exactly what a run could use.

Coverage: glossary revision + in-scope locked rules, APPROVED-only story memory,
active characters with aliases, the run's recorded hashes, and the staleness
comparison (a run produced with an older context is reported as stale instead of
being presented as current).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.api.translation import create_translation_router
from app.contracts import ChapterState, ImportKind, RightsStatus, RunStatus, SourceType
from app.db.base import session_factory
from app.db.models import Chapter, Project, SourceRevision, SourceSegment, TranslationRun
from app.modules.translation.characters import CharacterService
from app.modules.translation.context_trace import build_context_trace
from app.modules.translation.glossary import GlossaryCommand, GlossaryService, active_glossary
from app.modules.translation.story_memory import StoryMemoryService
from app.settings.config import Settings

PROJECT_ID = "018f0000-0000-7000-8000-000000000a01"
CHAPTER_ID = "018f0000-0000-7000-8000-000000000a02"
REVISION_ID = "018f0000-0000-7000-8000-000000000a03"
RUN_ID = "018f0000-0000-7000-8000-000000000a04"
LATEST_RUN_ID = "018f0000-0000-7000-8000-000000000a06"
SEGMENT_ID = "018f0000-0000-7000-8000-000000000a05"
PLACEHOLDER_HASH = "d" * 64


def _seed(engine: Engine) -> None:
    session = session_factory(engine)()
    try:
        session.add(
            Project(
                id=PROJECT_ID,
                title="Trace",
                slug="trace",
                source_type=SourceType.SELF_AUTHORED.value,
                rights_status=RightsStatus.PRIVATE_ONLY.value,
                default_language="zh-CN",
                target_language="vi-VN",
                style_guide_text="Giọng cổ trang.",
            )
        )
        session.flush()
        session.add(
            Chapter(
                id=CHAPTER_ID,
                project_id=PROJECT_ID,
                ordinal=3,
                state=ChapterState.TRANSLATION_REVIEW.value,
            )
        )
        session.flush()
        session.add(
            SourceRevision(
                id=REVISION_ID,
                chapter_id=CHAPTER_ID,
                revision_no=1,
                import_kind=ImportKind.PASTE.value,
                normalized_text="一",
                normalized_sha256=hashlib.sha256("一".encode("utf-8")).hexdigest(),
                han_char_count=1,
                total_char_count=1,
                normalizer_version="nfc-v1",
            )
        )
        session.flush()
        session.add(
            SourceSegment(
                id=SEGMENT_ID,
                source_revision_id=REVISION_ID,
                segment_index=0,
                paragraph_start=0,
                paragraph_end=0,
                source_text="一",
                source_sha256=hashlib.sha256("一".encode("utf-8")).hexdigest(),
                segment_kind="SOURCE",
            )
        )
        session.commit()
    finally:
        session.close()


def _seed_context(engine: Engine) -> None:
    """Seed memory evidence (an APPROVED run) plus a newer REVIEW run with placeholder hashes."""
    session = session_factory(engine)()
    try:
        session.add(
            TranslationRun(
                id=RUN_ID,
                chapter_id=CHAPTER_ID,
                source_revision_id=REVISION_ID,
                prompt_version="translation-v1",
                status=RunStatus.APPROVED.value,
                glossary_revision_hash=PLACEHOLDER_HASH,
                story_memory_revision_hash=PLACEHOLDER_HASH,
                translation_text_sha256="c" * 64,
                model="fake-hanviet-v2",
                estimated_cost_vnd=0,
                actual_cost_vnd=0,
            )
        )
        session.add(
            TranslationRun(
                id=LATEST_RUN_ID,
                chapter_id=CHAPTER_ID,
                source_revision_id=REVISION_ID,
                prompt_version="translation-v1",
                status=RunStatus.REVIEW.value,
                glossary_revision_hash=PLACEHOLDER_HASH,
                story_memory_revision_hash=PLACEHOLDER_HASH,
                translation_text_sha256="c" * 64,
                model="fake-hanviet-v2",
                estimated_cost_vnd=0,
                actual_cost_vnd=0,
            )
        )
        session.commit()

        GlossaryService(session).upsert(
            PROJECT_ID,
            GlossaryCommand(
                source_term="林动",
                target_term="Lâm Động",
                is_locked=True,
                forbidden_forms=("Lâm Đông",),
            ),
        )
        memory = StoryMemoryService(session)
        candidate = memory.create_candidate(
            PROJECT_ID,
            entity_key="lin-dong",
            entity_type="CHARACTER",
            summary="Nhân vật chính.",
            valid_from_ordinal=1,
        )
        memory.approve(PROJECT_ID, candidate.id, source_run_id=RUN_ID, evidence_segment_ids=(SEGMENT_ID,))
        character = CharacterService(session).create_or_revise(
            PROJECT_ID,
            canonical_name="Lâm Động",
            entity_type="PERSON",
            aliases=("Lâm Động", "Động ca"),
            status="APPROVED",
            evidence_source_revision_id=REVISION_ID,
            evidence_segment_ids=(SEGMENT_ID,),
        )
        # Matching is case-folded; the trace reports exactly what is stored.
        assert character.canonical_name.casefold() == "lâm động"
        session.commit()
    finally:
        session.close()


def _current_hashes(session) -> tuple[str, str]:
    return active_glossary(session, PROJECT_ID).sha256, StoryMemoryService(session).hash_for(PROJECT_ID, 3)


def test_context_trace_reports_glossary_memory_characters_and_staleness(migrated_engine: Engine) -> None:
    _seed(migrated_engine)
    _seed_context(migrated_engine)
    session = session_factory(migrated_engine)()
    try:
        glossary_hash, memory_hash = _current_hashes(session)

        trace = build_context_trace(session, CHAPTER_ID)

        assert trace["project_id"] == PROJECT_ID
        assert trace["ordinal"] == 3
        assert trace["state"] == ChapterState.TRANSLATION_REVIEW.value
        # The run recorded placeholder hashes, so the context is reported stale.
        assert trace["stale"] == {"glossary": True, "memory": True}
        assert trace["glossary"]["sha256"] == glossary_hash
        assert trace["glossary"]["entry_count"] == 1
        assert trace["glossary"]["locked_rules"] == [
            {"source_term": "林动", "target_term": "Lâm Động", "forbidden_forms": ["Lâm Đông"]}
        ]
        assert trace["memory"]["sha256"] == memory_hash
        assert len(trace["memory"]["entries"]) == 1
        assert trace["memory"]["entries"][0]["entity_key"] == "lin-dong"
        assert trace["characters"][0]["canonical_name"].casefold() == "lâm động"
        assert [alias.casefold() for alias in trace["characters"][0]["aliases"]] == ["lâm động", "động ca"]
        assert trace["characters"][0]["status"] == "APPROVED"
        assert trace["run"]["id"] == LATEST_RUN_ID
        assert trace["run"]["prompt_version"] == "translation-v1"
        assert trace["run"]["model"] == "fake-hanviet-v2"

        # Only in-scope locked rules are reported: a rule scoped to other chapters is skipped.
        GlossaryService(session).upsert(
            PROJECT_ID,
            GlossaryCommand(
                source_term="限定",
                target_term="giới hạn",
                is_locked=True,
                scope_from_ordinal=9,
                scope_to_ordinal=10,
            ),
        )
        session.commit()

        scoped = build_context_trace(session, CHAPTER_ID)

        assert [rule["source_term"] for rule in scoped["glossary"]["locked_rules"]] == ["林动"]
        # The glossary changed after that scoped read, so the run is now stale.
        assert scoped["stale"]["glossary"] is True
    finally:
        session.close()


def test_context_trace_is_not_stale_when_hashes_match(migrated_engine: Engine) -> None:
    _seed(migrated_engine)
    _seed_context(migrated_engine)
    session = session_factory(migrated_engine)()
    try:
        glossary_hash, memory_hash = _current_hashes(session)
        run = session.get(TranslationRun, LATEST_RUN_ID)
        run.glossary_revision_hash = glossary_hash
        run.story_memory_revision_hash = memory_hash
        session.commit()
        session.expire_all()

        trace = build_context_trace(session, CHAPTER_ID)

        assert trace["stale"] == {"glossary": False, "memory": False}
    finally:
        session.close()


def test_context_trace_candidate_memory_is_never_used(migrated_engine: Engine) -> None:
    _seed(migrated_engine)
    _seed_context(migrated_engine)
    session = session_factory(migrated_engine)()
    try:
        StoryMemoryService(session).create_candidate(
            PROJECT_ID,
            entity_key="ung-vien",
            entity_type="FACT",
            summary="Chưa duyệt.",
            valid_from_ordinal=1,
        )
        session.commit()

        trace = build_context_trace(session, CHAPTER_ID)

        keys = [entry["entity_key"] for entry in trace["memory"]["entries"]]
        assert keys == ["lin-dong"]
    finally:
        session.close()


def test_context_trace_reports_no_run_without_hashes(migrated_engine: Engine) -> None:
    _seed(migrated_engine)
    session = session_factory(migrated_engine)()
    try:
        trace = build_context_trace(session, CHAPTER_ID)

        assert trace["run"] is None
        assert trace["stale"] == {"glossary": False, "memory": False}
        assert trace["glossary"]["locked_rules"] == []
        assert trace["memory"]["entries"] == []
        assert trace["characters"] == []
    finally:
        session.close()


def test_context_trace_api_returns_trace_and_404_for_unknown_chapter(
    migrated_engine: Engine, tmp_path: Path
) -> None:
    _seed(migrated_engine)
    _seed_context(migrated_engine)
    db_path = Path(migrated_engine.url.database)
    app = FastAPI()
    app.include_router(create_translation_router(Settings(data_root=db_path.parent)))

    with TestClient(app) as client:
        response = client.get(f"/api/chapters/{CHAPTER_ID}/translation/context-trace")
        assert response.status_code == 200
        body = response.json()
        assert body["chapterId"] == CHAPTER_ID
        assert body["ordinal"] == 3
        assert body["run"]["id"] == LATEST_RUN_ID
        assert body["glossary"]["lockedRules"][0]["targetTerm"] == "Lâm Động"
        assert body["memory"]["entries"][0]["entityKey"] == "lin-dong"
        assert body["characters"][0]["canonicalName"].casefold() == "lâm động"
        assert body["stale"]["glossary"] is True

        missing = client.get("/api/chapters/018f0000-0000-7000-8000-000000000a99/translation/context-trace")
        assert missing.status_code == 404
        assert missing.json()["detail"] == "CHAPTER_NOT_FOUND"
