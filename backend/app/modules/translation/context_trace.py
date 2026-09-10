"""Context trace for a chapter's translation run (task U06 round 2).

The inspector answers "what exactly was this run allowed to use?" without
re-running anything and without exposing source text:

- the latest run of the chapter and the hashes it recorded (C02 provenance);
- the **currently active** glossary revision and the locked rules that are in
  scope for this chapter's ordinal (C02 scoping);
- the APPROVED story memory entries valid for the ordinal and their hash (C05);
- the active characters (canonical name, aliases, status) so addressing can be
  checked against the glossary/memory;
- a deterministic ``stale`` comparison: when the active glossary/memory hash
  differs from the hash recorded on the run, the run was produced with an older
  context and the inspector says so instead of implying it is current.

Raw identifiers (run id, memory ids, character revision ids, hashes) are kept in
the payload on purpose: the UI shows them only in the details drawer.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Chapter, TranslationRun
from app.modules.translation.characters import CharacterService
from app.modules.translation.glossary import active_glossary, locked_rules_for_chapter
from app.modules.translation.story_memory import StoryMemoryService


def build_context_trace(session: Session, chapter_id: str) -> dict[str, Any]:
    chapter = session.get(Chapter, chapter_id)
    if chapter is None:
        raise ValueError("CHAPTER_NOT_FOUND")
    ordinal = int(chapter.ordinal)

    run = session.scalar(
        select(TranslationRun)
        .where(TranslationRun.chapter_id == chapter_id)
        .order_by(TranslationRun.created_at.desc(), TranslationRun.id.desc())
        .limit(1)
    )
    glossary = active_glossary(session, chapter.project_id)
    locked_rules = locked_rules_for_chapter(session, chapter.project_id, ordinal)
    memory_service = StoryMemoryService(session)
    memory_entries = memory_service.memory_for(chapter.project_id, ordinal)
    memory_hash = memory_service.hash_for(chapter.project_id, ordinal)
    characters = CharacterService(session).active_characters(chapter.project_id)

    run_trace = None
    stale = {"glossary": False, "memory": False}
    if run is not None:
        run_trace = {
            "id": run.id,
            "status": run.status,
            "model": run.model,
            "provider_profile_id": run.provider_profile_id,
            "prompt_version": run.prompt_version,
            "glossary_revision_hash": run.glossary_revision_hash,
            "story_memory_revision_hash": run.story_memory_revision_hash,
            "source_revision_id": run.source_revision_id,
        }
        stale = {
            "glossary": bool(run.glossary_revision_hash)
            and run.glossary_revision_hash != glossary.sha256,
            "memory": bool(run.story_memory_revision_hash)
            and run.story_memory_revision_hash != memory_hash,
        }

    return {
        "chapter_id": chapter.id,
        "project_id": chapter.project_id,
        "ordinal": ordinal,
        "state": chapter.state,
        "run": run_trace,
        "glossary": {
            "sha256": glossary.sha256,
            "entry_count": len(glossary.entries),
            "locked_rules": [
                {
                    "source_term": rule.source_term,
                    "target_term": rule.target_term,
                    "forbidden_forms": list(rule.forbidden_forms),
                }
                for rule in locked_rules
            ],
        },
        "memory": {
            "sha256": memory_hash,
            "entries": [asdict(entry) for entry in memory_entries],
        },
        "characters": [
            {
                "character_id": view.character_id,
                "revision_id": view.revision_id,
                "revision_no": view.revision_no,
                "canonical_name": view.canonical_name,
                "aliases": list(view.aliases),
                "entity_type": view.entity_type,
                "status": view.status,
                "sha256": view.sha256,
                "evidence_segment_ids": list(view.evidence_segment_ids),
            }
            for view in characters
        ],
        "stale": stale,
    }
