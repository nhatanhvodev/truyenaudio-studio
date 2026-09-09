from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts import OperationContext, QaStatus, RunStatus, TranslationRequest, Usage, UsageUnit, new_id
from app.db.models import Chapter, Project, QaIssue, SourceRevision, SourceSegment, TranslationRun, TranslationSegment
from app.modules.translation.glossary import LockedGlossaryRule, active_glossary, locked_rules_for_chapter
from app.modules.translation.qa import run_deterministic_qa
from app.modules.translation.story_memory import StoryMemoryService
from app.modules.translation.workflow import PROMPT_VERSION, ZERO_HASH, TranslationRunView, TranslationWorkflow


QWEN_PLUS_MODEL = "qwen-mt-plus"
_PROPOSALS: dict[str, "RepairProposal"] = {}


@dataclass(frozen=True)
class RepairReplacement:
    source_segment_id: str
    source_text: str
    current_target_text: str
    target_text: str
    provider_request_id: str | None = None


@dataclass(frozen=True)
class RepairProposal:
    id: str
    base_run_id: str
    base_run_sha256: str
    chapter_id: str
    provider_model: str
    story_memory_revision_hash: str
    replacements: tuple[RepairReplacement, ...]
    estimated_cost_vnd: int
    hash: str
    consumed: bool = False


class RepairConflict(Exception):
    pass


class RepairService:
    def __init__(
        self,
        session: Session,
        *,
        translator: object | None = None,
        budget_guard: object | None = None,
        id_factory=new_id,
    ) -> None:
        self.session = session
        self.translator = translator
        self.budget_guard = budget_guard
        self.id_factory = id_factory

    def current_target(self, source_segment_id: str) -> str:
        segment = self.session.scalar(
            select(TranslationSegment)
            .where(TranslationSegment.source_segment_id == source_segment_id)
            .join(TranslationRun, TranslationRun.id == TranslationSegment.translation_run_id)
            .where(TranslationRun.status == RunStatus.REVIEW.value)
            .order_by(TranslationRun.created_at.desc(), TranslationSegment.id.desc())
        )
        if segment is None:
            raise ValueError("TRANSLATION_SEGMENT_NOT_FOUND")
        return segment.target_text

    def propose(
        self,
        chapter_id: str,
        selected_source_segment_ids: tuple[str, ...],
        provider_model: str = QWEN_PLUS_MODEL,
        *,
        cloud_consent_id: str | None = None,
        budget_authorization_id: str | None = None,
    ) -> RepairProposal:
        if not selected_source_segment_ids:
            raise ValueError("REPAIR_SEGMENT_REQUIRED")
        if self.translator is None:
            raise ValueError("TRANSLATOR_ADAPTER_REQUIRED")

        chapter = self._chapter(chapter_id)
        project = self._project(chapter.project_id)
        run = _current_review_run(self.session, chapter_id)
        segments = self._selected_segments(run.id, selected_source_segment_ids)
        estimated_cost_vnd = self._estimate(project, provider_model, segments)
        story_memory = StoryMemoryService(self.session)
        memory = story_memory.summaries_for(project.id, chapter.ordinal)
        memory_hash = story_memory.hash_for(project.id, chapter.ordinal)

        replacements: list[RepairReplacement] = []
        for source, current in segments:
            result = asyncio.run(
                self.translator.translate(
                    TranslationRequest(
                        context=OperationContext(
                            operation_id=f"repair:{chapter.id}:{run.id}:{source.id}",
                            cache_key=_repair_cache_key(
                                run.id,
                                source.id,
                                current.target_sha256,
                                provider_model,
                                memory_hash,
                            ),
                            timeout_seconds=60,
                            estimated_units=len(source.source_text) + len(current.target_text),
                            budget_authorization_id=budget_authorization_id,
                            cloud_consent_id=cloud_consent_id,
                            billing_category="QA_REPAIR",
                        ),
                        source_segment_id=source.id,
                        source_text=source.source_text,
                        source_language=project.default_language,
                        target_language=project.target_language,
                        terms=self._locked_terms(project.id, chapter.ordinal),
                        tm_list=(),
                        domain_instruction=project.style_guide_text or "",
                        story_memory=memory,
                    )
                )
            )
            replacements.append(
                RepairReplacement(
                    source_segment_id=source.id,
                    source_text=source.source_text,
                    current_target_text=current.target_text,
                    target_text=result.target_text,
                    provider_request_id=_provider_request_id(result.usage),
                )
            )

        proposal_id = self.id_factory()
        proposal_hash = _proposal_hash(
            run.id,
            run.translation_text_sha256 or ZERO_HASH,
            provider_model,
            memory_hash,
            tuple(replacements),
            estimated_cost_vnd,
        )
        proposal = RepairProposal(
            id=proposal_id,
            base_run_id=run.id,
            base_run_sha256=run.translation_text_sha256 or ZERO_HASH,
            chapter_id=chapter.id,
            provider_model=provider_model,
            story_memory_revision_hash=memory_hash,
            replacements=tuple(replacements),
            estimated_cost_vnd=estimated_cost_vnd,
            hash=proposal_hash,
        )
        _PROPOSALS[proposal.id] = proposal
        return proposal

    def accept_repair(self, proposal_id: str, expected_hash: str) -> TranslationRunView:
        proposal = _PROPOSALS.get(proposal_id)
        if proposal is None:
            raise ValueError("REPAIR_PROPOSAL_NOT_FOUND")
        if proposal.consumed:
            raise RepairConflict("REPAIR_PROPOSAL_CONSUMED")
        if proposal.hash != expected_hash:
            raise RepairConflict("REPAIR_PROPOSAL_HASH_MISMATCH")

        base = self.session.get(TranslationRun, proposal.base_run_id)
        if base is None or base.chapter_id != proposal.chapter_id:
            raise ValueError("TRANSLATION_RUN_NOT_FOUND")
        if (
            base.status != RunStatus.REVIEW.value
            or (base.translation_text_sha256 or ZERO_HASH) != proposal.base_run_sha256
            or _current_review_run(self.session, proposal.chapter_id).id != base.id
        ):
            raise RepairConflict("REPAIR_PROPOSAL_STALE")
        chapter = self._chapter(base.chapter_id)
        project = self._project(chapter.project_id)
        revision = self.session.get(SourceRevision, base.source_revision_id)
        if revision is None:
            raise ValueError("SOURCE_REVISION_NOT_FOUND")

        replacements = {replacement.source_segment_id: replacement for replacement in proposal.replacements}
        new_run = TranslationRun(
            id=self.id_factory(),
            chapter_id=chapter.id,
            source_revision_id=base.source_revision_id,
            provider_profile_id=base.provider_profile_id,
            model=proposal.provider_model,
            prompt_version=base.prompt_version,
            glossary_revision_hash=base.glossary_revision_hash,
            story_memory_revision_hash=proposal.story_memory_revision_hash,
            status=RunStatus.REVIEW.value,
            estimated_cost_vnd=proposal.estimated_cost_vnd,
            actual_cost_vnd=0,
            created_by="LOCAL_OWNER",
        )
        self.session.add(new_run)
        self.session.flush()

        for old_segment in self._translation_segments(base.id):
            replacement = replacements.get(old_segment.source_segment_id)
            target_text = replacement.target_text if replacement else old_segment.target_text
            self.session.add(
                TranslationSegment(
                    id=self.id_factory(),
                    translation_run_id=new_run.id,
                    source_segment_id=old_segment.source_segment_id,
                    target_text=target_text,
                    target_sha256=_sha(target_text),
                    provider_request_id=replacement.provider_request_id if replacement else old_segment.provider_request_id,
                    cache_key=None if replacement else old_segment.cache_key,
                    was_cache_hit=False if replacement else old_segment.was_cache_hit,
                    manually_edited=old_segment.manually_edited,
                )
            )

        base.status = RunStatus.SUPERSEDED.value
        workflow = TranslationWorkflow(self.session, id_factory=self.id_factory)
        workflow._invalidate_downstream(chapter)
        chapter.state = "TRANSLATION_REVIEW"
        self.session.flush()
        locked_rules = self._locked_rules(project.id, chapter.ordinal)
        self._replace_qa_issues(
            new_run,
            tuple((rule.source_term, rule.target_term) for rule in locked_rules),
            tuple(
                (rule.source_term, rule.forbidden_forms)
                for rule in locked_rules
                if rule.forbidden_forms
            ),
        )
        new_run.translation_text_sha256 = workflow._run_hash(
            new_run,
            project,
            revision,
            _sha(project.style_guide_text or ""),
        )
        _PROPOSALS[proposal.id] = RepairProposal(**{**proposal.__dict__, "consumed": True})
        self.session.commit()
        return workflow._run_view(new_run.id)

    def _estimate(self, project: Project, provider_model: str, segments: tuple[tuple[SourceSegment, TranslationSegment], ...]) -> int:
        if self.budget_guard is None:
            return 0
        capabilities = self.translator.capabilities() if hasattr(self.translator, "capabilities") else {}
        quote = self.budget_guard.quote_usage(
            operation_id=f"repair-quote:{project.id}:{provider_model}",
            provider=str(capabilities.get("provider") or "qwen"),
            model=provider_model,
            region=capabilities.get("region"),
            usage=(Usage(UsageUnit.INPUT_TOKEN.value, sum(len(source.source_text) + len(current.target_text) for source, current in segments)),),
            category="QA_REPAIR",
        )
        return int(quote.total_vnd)

    def _selected_segments(self, run_id: str, selected_source_segment_ids: tuple[str, ...]) -> tuple[tuple[SourceSegment, TranslationSegment], ...]:
        selected = set(selected_source_segment_ids)
        rows = self.session.execute(
            select(SourceSegment, TranslationSegment)
            .join(TranslationSegment, TranslationSegment.source_segment_id == SourceSegment.id)
            .where(TranslationSegment.translation_run_id == run_id)
            .order_by(SourceSegment.segment_index, SourceSegment.id)
        ).all()
        by_id = {source.id: (source, segment) for source, segment in rows}
        unknown = selected - set(by_id)
        if unknown:
            raise ValueError("SOURCE_SEGMENT_NOT_IN_RUN")
        return tuple(by_id[source_id] for source_id in selected_source_segment_ids)

    def _translation_segments(self, run_id: str) -> tuple[TranslationSegment, ...]:
        return tuple(
            self.session.scalars(
                select(TranslationSegment)
                .where(TranslationSegment.translation_run_id == run_id)
                .join(SourceSegment, SourceSegment.id == TranslationSegment.source_segment_id)
                .order_by(SourceSegment.segment_index, TranslationSegment.id)
            ).all()
        )

    def _replace_qa_issues(
        self,
        run: TranslationRun,
        locked_terms: tuple[tuple[str, str], ...],
        forbidden_forms: tuple[tuple[str, tuple[str, ...]], ...] = (),
    ) -> None:
        for segment in self._translation_segments(run.id):
            source = self.session.get(SourceSegment, segment.source_segment_id)
            if source is None:
                raise ValueError("SOURCE_SEGMENT_NOT_FOUND")
            for draft in run_deterministic_qa(
                source.source_text, segment.target_text, locked_terms, forbidden_forms
            ):
                self.session.add(
                    QaIssue(
                        id=self.id_factory(),
                        chapter_id=run.chapter_id,
                        translation_run_id=run.id,
                        category=draft.category.value,
                        severity=draft.severity.value,
                        status=QaStatus.OPEN.value,
                        source_segment_id=segment.source_segment_id,
                        evidence=draft.evidence,
                        suggestion=draft.suggestion,
                        rule_or_model=draft.rule_or_model,
                    )
                )
        self.session.flush()

    def _locked_terms(
        self, project_id: str, chapter_ordinal: int | None = None
    ) -> tuple[tuple[str, str], ...]:
        rules = self._locked_rules(project_id, chapter_ordinal)
        return tuple((rule.source_term, rule.target_term) for rule in rules)

    def _locked_rules(
        self, project_id: str, chapter_ordinal: int | None = None
    ) -> tuple[LockedGlossaryRule, ...]:
        if chapter_ordinal is not None:
            return locked_rules_for_chapter(self.session, project_id, chapter_ordinal)
        return tuple(
            LockedGlossaryRule(
                source_term=entry.source_term,
                target_term=entry.target_term,
                forbidden_forms=tuple(entry.forbidden_forms or ()),
            )
            for entry in active_glossary(self.session, project_id).entries
            if entry.is_locked
        )

    def _chapter(self, chapter_id: str) -> Chapter:
        chapter = self.session.get(Chapter, chapter_id)
        if chapter is None:
            raise ValueError("CHAPTER_NOT_FOUND")
        return chapter

    def _project(self, project_id: str) -> Project:
        project = self.session.get(Project, project_id)
        if project is None:
            raise ValueError("PROJECT_NOT_FOUND")
        return project


def _current_review_run(session: Session, chapter_id: str) -> TranslationRun:
    run = session.scalar(
        select(TranslationRun)
        .where(TranslationRun.chapter_id == chapter_id, TranslationRun.status == RunStatus.REVIEW.value)
        .order_by(TranslationRun.created_at.desc(), TranslationRun.id.desc())
    )
    if run is None:
        raise ValueError("TRANSLATION_RUN_NOT_FOUND")
    return run


def _repair_cache_key(
    run_id: str,
    source_segment_id: str,
    target_sha256: str,
    provider_model: str,
    story_memory_revision_hash: str,
) -> str:
    return _canonical_sha256(
        {
            "run_id": run_id,
            "source_segment_id": source_segment_id,
            "target_sha256": target_sha256,
            "prompt_version": PROMPT_VERSION,
            "provider_model": provider_model,
            "story_memory": story_memory_revision_hash,
        }
    )


def _proposal_hash(
    base_run_id: str,
    base_run_sha256: str,
    provider_model: str,
    story_memory_revision_hash: str,
    replacements: tuple[RepairReplacement, ...],
    estimated_cost_vnd: int,
) -> str:
    return _canonical_sha256(
        {
            "base_run_id": base_run_id,
            "base_run_sha256": base_run_sha256,
            "provider_model": provider_model,
            "story_memory_revision_hash": story_memory_revision_hash,
            "estimated_cost_vnd": estimated_cost_vnd,
            "replacements": [replacement.__dict__ for replacement in replacements],
        }
    )


def _provider_request_id(usage: tuple[Usage, ...]) -> str | None:
    for item in usage:
        if item.provider_request_id:
            return item.provider_request_id
    return None


def _canonical_sha256(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
