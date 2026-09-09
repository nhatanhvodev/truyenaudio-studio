from __future__ import annotations

from collections.abc import Callable
import asyncio
from dataclasses import dataclass
import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts import (
    ChapterState,
    OperationContext,
    ProviderKind,
    QaCategory,
    QaSeverity,
    QaStatus,
    RunStatus,
    TranslationRequest,
    TranslationResult,
    TranslatorAdapter,
    new_id,
)
from app.db.base import utc_now
from app.db.models import (
    AuditEvent,
    Chapter,
    Project,
    ProviderProfile,
    QaIssue,
    SourceRevision,
    SourceSegment,
    TranslationRun,
    TranslationSegment,
)
from app.modules.projects.state_machine import next_state
from app.modules.translation.glossary import (
    LockedGlossaryRule,
    active_glossary,
    locked_rules_for_chapter,
)
from app.modules.translation.qa import QaIssueDraft, run_deterministic_qa
from app.modules.translation.story_memory import StoryMemoryService
from app.modules.translation.translation_memory import exact_match, record_approved_run


PROMPT_VERSION = "translation-v1"
ZERO_HASH = "0" * 64


class ApprovalBlocked(Exception):
    """Raised when open major or critical translation QA issues remain."""


class RevisionConflict(Exception):
    """Raised when an editor tries to save against a stale run hash."""


@dataclass(frozen=True)
class TranslationSegmentView:
    id: str
    source_segment_id: str
    source_text: str
    target_text: str
    target_sha256: str
    cache_key: str | None
    was_cache_hit: bool
    manually_edited: bool


@dataclass(frozen=True)
class QaIssueView:
    id: str
    category: QaCategory
    severity: QaSeverity
    status: QaStatus
    evidence: str | None
    suggestion: str | None
    rule_or_model: str | None
    source_segment_id: str | None


@dataclass(frozen=True)
class TranslationRunView:
    id: str
    chapter_id: str
    source_revision_id: str
    status: RunStatus
    sha256: str
    provider_profile_id: str | None
    model: str | None
    glossary_revision_hash: str | None
    story_memory_revision_hash: str | None
    segments: tuple[TranslationSegmentView, ...]
    issues: tuple[QaIssueView, ...]


@dataclass(frozen=True)
class _ProviderSelection:
    adapter: TranslatorAdapter
    provider_profile_id: str | None
    provider: str
    model: str
    provider_version: str
    region: str | None


class TranslationWorkflow:
    def __init__(
        self,
        session: Session,
        translator: TranslatorAdapter | None = None,
        id_factory: Callable[[], str] = new_id,
    ) -> None:
        self.session = session
        self.translator = translator
        self.id_factory = id_factory

    def estimate(self, chapter_id: str) -> dict[str, object]:
        chapter, revision = self._chapter_and_revision(chapter_id)
        segments = self._source_segments(revision)
        return {
            "chapter_id": chapter.id,
            "segment_count": len(segments),
            "source_characters": sum(len(segment.source_text) for segment in segments),
        }

    def enqueue_translation(
        self,
        chapter_id: str,
        *,
        cloud_consent_id: str | None = None,
        budget_authorization_id: str | None = None,
    ) -> TranslationRunView:
        chapter, revision = self._chapter_and_revision(chapter_id)
        project = self._project(chapter.project_id)
        provider = self._provider(project)
        glossary_hash = active_glossary(self.session, project.id).sha256
        style_hash = _sha(project.style_guide_text or "")
        memory = self._story_memory(project.id, chapter.ordinal)
        memory_hash = _canonical_sha256(memory)
        source_segments = self._source_segments(revision)
        estimated_units = sum(len(segment.source_text) for segment in source_segments)

        if chapter.state == ChapterState.NORMALIZED.value:
            chapter.state = next_state(
                ChapterState.NORMALIZED, ChapterState.TRANSLATING
            ).value

        run = TranslationRun(
            id=self.id_factory(),
            chapter_id=chapter.id,
            source_revision_id=revision.id,
            provider_profile_id=provider.provider_profile_id,
            model=provider.model,
            prompt_version=PROMPT_VERSION,
            glossary_revision_hash=glossary_hash,
            story_memory_revision_hash=memory_hash,
            status=RunStatus.RUNNING.value,
            estimated_cost_vnd=0,
            actual_cost_vnd=0,
        )
        self.session.add(run)
        self.session.flush()

        locked_rules = self._locked_rules(project.id, chapter.ordinal)
        locked_terms = tuple(
            (rule.source_term, rule.target_term) for rule in locked_rules
        )
        forbidden_forms = tuple(
            (rule.source_term, rule.forbidden_forms)
            for rule in locked_rules
            if rule.forbidden_forms
        )
        for source_segment in source_segments:
            cache_key = _translation_cache_key(
                source_segment=source_segment,
                provider=provider,
                prompt_version=PROMPT_VERSION,
                style_hash=style_hash,
                glossary_hash=glossary_hash,
                story_memory_hash=memory_hash,
            )
            cached = self._cached_segment(cache_key)
            if cached is None:
                tm_match = exact_match(
                    self.session,
                    project_id=project.id,
                    source_text=source_segment.source_text,
                    source_language=project.default_language,
                    target_language=project.target_language,
                    glossary_hash=glossary_hash,
                )
                tm_list = (
                    ((tm_match.source_text, tm_match.target_text),)
                    if tm_match is not None
                    else ()
                )
                result = self._translate_segment(
                    provider,
                    project,
                    chapter,
                    source_segment,
                    cache_key,
                    locked_terms,
                    memory,
                    estimated_units,
                    cloud_consent_id=cloud_consent_id,
                    budget_authorization_id=budget_authorization_id,
                    tm_list=tm_list,
                )
                target_text = result.target_text
                provider_request_id = _provider_request_id(result)
                was_cache_hit = False
            else:
                target_text = cached.target_text
                provider_request_id = cached.provider_request_id
                was_cache_hit = True
            self._add_translation_segment(
                run.id,
                source_segment.id,
                target_text,
                provider_request_id,
                cache_key,
                was_cache_hit,
                manually_edited=False,
            )

        self.session.flush()
        self._replace_qa_issues(run, locked_terms, forbidden_forms)
        run.translation_text_sha256 = self._run_hash(run, project, revision, style_hash)
        run.status = RunStatus.REVIEW.value
        chapter.state = ChapterState.TRANSLATION_REVIEW.value
        self.session.commit()
        return self._run_view(run.id)

    def revise_segment(
        self,
        chapter_id: str,
        run_id: str,
        source_segment_id: str,
        target_text: str,
        *,
        expected_run_hash: str,
    ) -> TranslationRunView:
        previous = self._run_for_chapter(chapter_id, run_id)
        if previous.translation_text_sha256 != expected_run_hash:
            raise RevisionConflict("TRANSLATION_RUN_HASH_MISMATCH")
        chapter = self.session.get(Chapter, chapter_id)
        if chapter is None:
            raise ValueError("CHAPTER_NOT_FOUND")
        project = self._project(chapter.project_id)
        revision = self.session.get(SourceRevision, previous.source_revision_id)
        if revision is None:
            raise ValueError("SOURCE_REVISION_NOT_FOUND")

        style_hash = _sha(project.style_guide_text or "")
        new_run = TranslationRun(
            id=self.id_factory(),
            chapter_id=chapter.id,
            source_revision_id=previous.source_revision_id,
            provider_profile_id=previous.provider_profile_id,
            model=previous.model,
            prompt_version=previous.prompt_version,
            glossary_revision_hash=previous.glossary_revision_hash,
            story_memory_revision_hash=previous.story_memory_revision_hash,
            status=RunStatus.REVIEW.value,
            estimated_cost_vnd=0,
            actual_cost_vnd=0,
            created_by="LOCAL_OWNER",
        )
        self.session.add(new_run)
        self.session.flush()

        found = False
        for old_segment in self._translation_segments(previous.id):
            replacement = (
                target_text
                if old_segment.source_segment_id == source_segment_id
                else old_segment.target_text
            )
            found = found or old_segment.source_segment_id == source_segment_id
            self._add_translation_segment(
                new_run.id,
                old_segment.source_segment_id,
                replacement,
                old_segment.provider_request_id,
                old_segment.cache_key,
                was_cache_hit=False,
                manually_edited=old_segment.source_segment_id == source_segment_id
                or old_segment.manually_edited,
            )
        if not found:
            raise ValueError("SOURCE_SEGMENT_NOT_IN_RUN")

        previous.status = RunStatus.SUPERSEDED.value
        self._invalidate_downstream(chapter)
        chapter.state = ChapterState.TRANSLATION_REVIEW.value
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
        new_run.translation_text_sha256 = self._run_hash(
            new_run, project, revision, style_hash
        )
        self.session.commit()
        return self._run_view(new_run.id)

    def approve_revision(
        self,
        chapter_id: str,
        run_id: str,
        expected_run_hash: str,
        *,
        force: bool = False,
    ) -> TranslationRunView:
        run = self._run_for_chapter(chapter_id, run_id)
        if run.translation_text_sha256 != expected_run_hash:
            raise RevisionConflict("TRANSLATION_RUN_HASH_MISMATCH")
        chapter = self.session.get(Chapter, chapter_id)
        if chapter is None:
            raise ValueError("CHAPTER_NOT_FOUND")
        self._require_current_approvable_run(chapter, run)
        blockers = self.session.scalars(
            select(QaIssue).where(
                QaIssue.chapter_id == chapter_id,
                QaIssue.translation_run_id == run_id,
                QaIssue.status == QaStatus.OPEN.value,
                QaIssue.severity.in_(
                    (QaSeverity.MAJOR.value, QaSeverity.CRITICAL.value)
                ),
            )
        ).all()
        if blockers:
            if force:
                for blocker in blockers:
                    blocker.status = QaStatus.DISMISSED.value
                    blocker.resolved_note = "Dismissed via force approval override"
                    blocker.resolved_at = utc_now()
                self.session.flush()
            else:
                raise ApprovalBlocked("TRANSLATION_QA_BLOCKERS_OPEN")

        before_hash = self._approved_translation_hash(chapter)
        if before_hash and chapter.approved_translation_run_id != run.id:
            self._invalidate_downstream(chapter)
        run.status = RunStatus.APPROVED.value
        chapter.approved_translation_run_id = run.id
        chapter.translation_approved_at = utc_now()
        if chapter.state != ChapterState.TRANSLATION_APPROVED.value:
            chapter.state = next_state(
                chapter.state, ChapterState.TRANSLATION_APPROVED
            ).value
        self.session.add(
            AuditEvent(
                id=self.id_factory(),
                actor="LOCAL_OWNER",
                action="APPROVE_TRANSLATION",
                entity_type="TranslationRun",
                entity_id=run.id,
                before_hash=before_hash,
                after_hash=run.translation_text_sha256,
                redacted_details=json.dumps(
                    {"chapter_id": chapter_id, "run_id": run.id},
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
        )
        record_approved_run(self.session, run.id, id_factory=self.id_factory)
        self.session.commit()
        return self._run_view(run.id)

    def _require_current_approvable_run(
        self, chapter: Chapter, run: TranslationRun
    ) -> None:
        if (
            run.status == RunStatus.APPROVED.value
            and chapter.approved_translation_run_id == run.id
        ):
            return
        if run.status != RunStatus.REVIEW.value:
            raise RevisionConflict("TRANSLATION_RUN_NOT_APPROVABLE")
        if self._latest_review_run_id(chapter.id) != run.id:
            raise RevisionConflict("TRANSLATION_RUN_NOT_CURRENT")

    def _latest_review_run_id(self, chapter_id: str) -> str | None:
        return self.session.scalar(
            select(TranslationRun.id)
            .where(
                TranslationRun.chapter_id == chapter_id,
                TranslationRun.status == RunStatus.REVIEW.value,
            )
            .order_by(TranslationRun.created_at.desc(), TranslationRun.id.desc())
        )

    def _approved_translation_hash(self, chapter: Chapter) -> str | None:
        if chapter.approved_translation_run_id is None:
            return None
        approved = self.session.get(TranslationRun, chapter.approved_translation_run_id)
        if approved is None:
            return None
        return approved.translation_text_sha256

    def current_translation(self, chapter_id: str) -> TranslationRunView:
        chapter = self.session.get(Chapter, chapter_id)
        if chapter is None:
            raise ValueError("CHAPTER_NOT_FOUND")
        run_id = chapter.approved_translation_run_id
        if run_id is None:
            run_id = self.session.scalar(
                select(TranslationRun.id)
                .where(TranslationRun.chapter_id == chapter_id)
                .order_by(TranslationRun.created_at.desc(), TranslationRun.id.desc())
            )
        if run_id is None:
            raise ValueError("TRANSLATION_RUN_NOT_FOUND")
        return self._run_view(run_id)

    def _translate_segment(
        self,
        provider: _ProviderSelection,
        project: Project,
        chapter: Chapter,
        source_segment: SourceSegment,
        cache_key: str,
        locked_terms: tuple[tuple[str, str], ...],
        story_memory: tuple[str, ...],
        estimated_units: int,
        cloud_consent_id: str | None,
        budget_authorization_id: str | None,
        tm_list: tuple[tuple[str, str], ...] = (),
    ) -> TranslationResult:
        request = TranslationRequest(
            context=OperationContext(
                operation_id=_translation_operation_id(chapter.id),
                cache_key=cache_key,
                timeout_seconds=60,
                estimated_units=estimated_units,
                budget_authorization_id=budget_authorization_id,
                cloud_consent_id=cloud_consent_id,
            ),
            source_segment_id=source_segment.id,
            source_text=source_segment.source_text,
            source_language=project.default_language,
            target_language=project.target_language,
            terms=locked_terms,
            tm_list=tm_list,
            domain_instruction=project.style_guide_text or "",
            story_memory=story_memory,
        )
        return asyncio.run(provider.adapter.translate(request))

    def _invalidate_downstream(self, chapter: Chapter) -> None:
        chapter.approved_translation_run_id = None
        chapter.translation_approved_at = None
        chapter.active_voice_plan_id = None
        chapter.approved_master_artifact_id = None
        chapter.audio_approved_at = None
        chapter.last_export_id = None

    def _replace_qa_issues(
        self,
        run: TranslationRun,
        locked_terms: tuple[tuple[str, str], ...],
        forbidden_forms: tuple[tuple[str, tuple[str, ...]], ...] = (),
    ) -> None:
        for segment in self._translation_segments(run.id):
            source_segment = self.session.get(SourceSegment, segment.source_segment_id)
            if source_segment is None:
                raise ValueError("SOURCE_SEGMENT_NOT_FOUND")
            for draft in run_deterministic_qa(
                source_segment.source_text,
                segment.target_text,
                locked_terms,
                forbidden_forms,
            ):
                self._add_qa_issue(run, segment.source_segment_id, draft)
        self.session.flush()

    def _add_qa_issue(
        self,
        run: TranslationRun,
        source_segment_id: str,
        draft: QaIssueDraft,
    ) -> None:
        self.session.add(
            QaIssue(
                id=self.id_factory(),
                chapter_id=run.chapter_id,
                translation_run_id=run.id,
                category=draft.category.value,
                severity=draft.severity.value,
                status=QaStatus.OPEN.value,
                source_segment_id=source_segment_id,
                evidence=draft.evidence,
                suggestion=draft.suggestion,
                rule_or_model=draft.rule_or_model,
            )
        )

    def _run_hash(
        self,
        run: TranslationRun,
        project: Project,
        revision: SourceRevision,
        style_hash: str,
    ) -> str:
        segments = [
            {
                "source_segment_id": segment.source_segment_id,
                "target_sha256": segment.target_sha256,
            }
            for segment in self._translation_segments(run.id)
        ]
        return _canonical_sha256(
            {
                "source_revision_hash": revision.normalized_sha256,
                "run_id": run.id,
                "prompt_version": run.prompt_version,
                "style_hash": style_hash,
                "glossary_revision_hash": run.glossary_revision_hash or ZERO_HASH,
                "story_memory_revision_hash": run.story_memory_revision_hash
                or ZERO_HASH,
                "target_language": project.target_language,
                "segments": segments,
            }
        )

    def _run_view(self, run_id: str) -> TranslationRunView:
        run = self.session.get(TranslationRun, run_id)
        if run is None:
            raise ValueError("TRANSLATION_RUN_NOT_FOUND")
        return TranslationRunView(
            id=run.id,
            chapter_id=run.chapter_id,
            source_revision_id=run.source_revision_id,
            status=RunStatus(run.status),
            sha256=run.translation_text_sha256 or ZERO_HASH,
            provider_profile_id=run.provider_profile_id,
            model=run.model,
            glossary_revision_hash=run.glossary_revision_hash,
            story_memory_revision_hash=run.story_memory_revision_hash,
            segments=tuple(
                self._segment_view(segment)
                for segment in self._translation_segments(run.id)
            ),
            issues=tuple(self._issue_view(issue) for issue in self._issues(run.id)),
        )

    def _segment_view(self, segment: TranslationSegment) -> TranslationSegmentView:
        source = self.session.get(SourceSegment, segment.source_segment_id)
        if source is None:
            raise ValueError("SOURCE_SEGMENT_NOT_FOUND")
        return TranslationSegmentView(
            id=segment.id,
            source_segment_id=segment.source_segment_id,
            source_text=source.source_text,
            target_text=segment.target_text,
            target_sha256=segment.target_sha256,
            cache_key=segment.cache_key,
            was_cache_hit=segment.was_cache_hit,
            manually_edited=segment.manually_edited,
        )

    def _issue_view(self, issue: QaIssue) -> QaIssueView:
        return QaIssueView(
            id=issue.id,
            category=QaCategory(issue.category),
            severity=QaSeverity(issue.severity),
            status=QaStatus(issue.status),
            evidence=issue.evidence,
            suggestion=issue.suggestion,
            rule_or_model=issue.rule_or_model,
            source_segment_id=issue.source_segment_id,
        )

    def _provider(self, project: Project) -> _ProviderSelection:
        if self.translator is None:
            raise ValueError("TRANSLATOR_ADAPTER_REQUIRED")
        adapter = self.translator
        capabilities = adapter.capabilities()
        profile = self._provider_profile(project)
        provider = str(
            capabilities.get("provider")
            or (profile.adapter_name if profile is not None else "fake")
        )
        model = str(
            profile.model
            if profile and profile.model
            else capabilities.get("model") or "unknown"
        )
        region = (
            profile.region if profile else _optional_str(capabilities.get("region"))
        )
        return _ProviderSelection(
            adapter=adapter,
            provider_profile_id=profile.id if profile else None,
            provider=provider,
            model=model,
            provider_version=str(capabilities.get("provider_version") or "unknown"),
            region=region,
        )

    def _provider_profile(self, project: Project) -> ProviderProfile | None:
        if project.default_translator_profile_id:
            return self.session.get(
                ProviderProfile, project.default_translator_profile_id
            )
        return self.session.scalar(
            select(ProviderProfile)
            .where(
                ProviderProfile.provider_kind == ProviderKind.TRANSLATOR.value,
                ProviderProfile.enabled.is_(True),
            )
            .order_by(ProviderProfile.id)
        )

    def _chapter_and_revision(self, chapter_id: str) -> tuple[Chapter, SourceRevision]:
        chapter = self.session.get(Chapter, chapter_id)
        if chapter is None:
            raise ValueError("CHAPTER_NOT_FOUND")
        if chapter.active_source_revision_id is None:
            raise ValueError("CHAPTER_ACTIVE_SOURCE_REVISION_REQUIRED")
        revision = self.session.get(SourceRevision, chapter.active_source_revision_id)
        if revision is None:
            raise ValueError("SOURCE_REVISION_NOT_FOUND")
        return chapter, revision

    def _project(self, project_id: str) -> Project:
        project = self.session.get(Project, project_id)
        if project is None:
            raise ValueError("PROJECT_NOT_FOUND")
        return project

    def _source_segments(self, revision: SourceRevision) -> tuple[SourceSegment, ...]:
        segments = tuple(
            self.session.scalars(
                select(SourceSegment)
                .where(SourceSegment.source_revision_id == revision.id)
                .order_by(SourceSegment.segment_index, SourceSegment.id)
            ).all()
        )
        if segments:
            return segments
        segment = SourceSegment(
            id=self.id_factory(),
            source_revision_id=revision.id,
            segment_index=0,
            paragraph_start=0,
            paragraph_end=0,
            source_text=revision.normalized_text,
            source_sha256=_sha(revision.normalized_text),
            segment_kind="SOURCE",
        )
        self.session.add(segment)
        self.session.flush()
        return (segment,)

    def _translation_segments(self, run_id: str) -> tuple[TranslationSegment, ...]:
        return tuple(
            self.session.scalars(
                select(TranslationSegment)
                .where(TranslationSegment.translation_run_id == run_id)
                .join(
                    SourceSegment,
                    SourceSegment.id == TranslationSegment.source_segment_id,
                )
                .order_by(SourceSegment.segment_index, TranslationSegment.id)
            ).all()
        )

    def _issues(self, run_id: str) -> tuple[QaIssue, ...]:
        return tuple(
            self.session.scalars(
                select(QaIssue)
                .where(QaIssue.translation_run_id == run_id)
                .order_by(QaIssue.severity.desc(), QaIssue.category, QaIssue.id)
            ).all()
        )

    def _run_for_chapter(self, chapter_id: str, run_id: str) -> TranslationRun:
        run = self.session.get(TranslationRun, run_id)
        if run is None or run.chapter_id != chapter_id:
            raise ValueError("TRANSLATION_RUN_NOT_FOUND")
        return run

    def _cached_segment(self, cache_key: str) -> TranslationSegment | None:
        return self.session.scalar(
            select(TranslationSegment)
            .where(
                TranslationSegment.cache_key == cache_key,
                TranslationSegment.manually_edited.is_(False),
                TranslationSegment.was_cache_hit.is_(False),
            )
            .order_by(
                TranslationSegment.created_at.desc(), TranslationSegment.id.desc()
            )
        )

    def _add_translation_segment(
        self,
        run_id: str,
        source_segment_id: str,
        target_text: str,
        provider_request_id: str | None,
        cache_key: str | None,
        was_cache_hit: bool,
        manually_edited: bool,
    ) -> None:
        self.session.add(
            TranslationSegment(
                id=self.id_factory(),
                translation_run_id=run_id,
                source_segment_id=source_segment_id,
                target_text=target_text,
                target_sha256=_sha(target_text),
                provider_request_id=provider_request_id,
                cache_key=cache_key,
                was_cache_hit=was_cache_hit,
                manually_edited=manually_edited,
            )
        )

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

    def _story_memory(self, project_id: str, ordinal: int) -> tuple[str, ...]:
        return StoryMemoryService(self.session).summaries_for(project_id, ordinal)


def _translation_cache_key(
    *,
    source_segment: SourceSegment,
    provider: _ProviderSelection,
    prompt_version: str,
    style_hash: str,
    glossary_hash: str,
    story_memory_hash: str,
) -> str:
    return _canonical_sha256(
        {
            "source": {
                "segment_id": source_segment.id,
                "sha256": source_segment.source_sha256,
            },
            "provider": provider.provider,
            "model": provider.model,
            "version": provider.provider_version,
            "region": provider.region,
            "prompt": prompt_version,
            "style": style_hash,
            "glossary": glossary_hash,
            "story_memory": story_memory_hash,
        }
    )


def _canonical_sha256(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _translation_operation_id(chapter_id: str) -> str:
    return f"translate:{chapter_id}"


def _provider_request_id(result: TranslationResult) -> str | None:
    for usage in result.usage:
        if usage.provider_request_id:
            return usage.provider_request_id
    return None


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)
