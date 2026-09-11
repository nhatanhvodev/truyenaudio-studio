from __future__ import annotations

from collections.abc import Iterator
from dataclasses import asdict
from enum import Enum

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.contracts import TranslationRequest, TranslationResult, Usage, UsageUnit
from app.db.base import create_engine_for, session_factory
from app.modules.translation.hanviet import convert_hanviet
from app.modules.translation.repair import RepairConflict, RepairService
from app.modules.translation.review import ReviewService
from app.settings.config import Settings


class EnqueueReviewRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    selected_segment_ids: tuple[str, ...] = Field(default=(), alias="selectedSegmentIds")


class RepairPreviewRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    selected_segment_ids: tuple[str, ...] = Field(alias="selectedSegmentIds")
    provider_model: str | None = Field(default=None, alias="providerModel")


class RepairApplyRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    proposal_id: str = Field(alias="proposalId", min_length=1)
    expected_proposal_hash: str = Field(alias="expectedProposalHash", min_length=1)


# Which engine produced a repair proposal. The preview/apply route is hardwired to
# the offline deterministic translator (J01 owns the real cloud repair), and the
# proposal carries this marker so the UI can never present a locally generated
# proposal as a cloud-model repair — applying one writes a new translation run.
REPAIR_OFFLINE_GENERATOR = "OFFLINE_DETERMINISTIC"


def create_review_router(settings: Settings | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/chapters/{chapter_id}/review")
    active_settings = settings or Settings()

    def service_dependency() -> Iterator[ReviewService]:
        engine = create_engine_for(active_settings.data_root / "studio.sqlite3")
        factory = session_factory(engine)
        with factory() as session:
            yield ReviewService(session)
        engine.dispose()

    def repair_service_dependency() -> Iterator[RepairService]:
        """Offline preview/apply path: always the deterministic fake adapter.

        U06 round 3: repair-preview is a PREVIEW only. It never calls a cloud
        model and never writes translations: the proposals are produced by the
        local deterministic CleanRepairFakeTranslator (Han-Viet conversion, no
        network), so the route is fully offline and reproducible.

        A real cloud-model repair stays with the J01 REPAIR job handler and is
        explicitly NOT_RUN in this round; this endpoint switches to the
        authorized provider adapter only when that work lands.
        """
        engine = create_engine_for(active_settings.data_root / "studio.sqlite3")
        factory = session_factory(engine)
        with factory() as session:
            yield RepairService(session, translator=CleanRepairFakeTranslator())
        engine.dispose()

    @router.post("/enqueue")
    def enqueue_review(
        chapter_id: str,
        request: EnqueueReviewRequest,
        service: ReviewService = Depends(service_dependency),
    ) -> dict[str, object]:
        try:
            jobs = service.enqueue(chapter_id, selected_ids=request.selected_segment_ids)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"jobs": [_camelize(_convert(asdict(job))) for job in jobs]}

    @router.post("/repair-preview")
    def repair_preview(
        chapter_id: str,
        request: RepairPreviewRequest,
        service: RepairService = Depends(repair_service_dependency),
    ) -> dict[str, object]:
        """Preview a repair proposal for the selected segments (offline, fake).

        U06 round 3 contract:
        - uses RepairService.propose() with the deterministic fake adapter (see
          repair_service_dependency): no cloud call, no translation row is
          written, the current REVIEW run is only read;
        - the same chapter + segment selection + provider model yields a stable
          hash (the proposal payload is deterministic and immutable);
        - the payload states its own generator (REPAIR_OFFLINE_GENERATOR): it is
          NOT a cloud-model repair, and the UI must show that before an operator
          applies the proposal (apply writes a new translation run);
        - the real cloud-model repair belongs to the J01 REPAIR job handler and
          is NOT_RUN in this round.
        """
        provider_model = request.provider_model or "qwen-mt-plus"
        try:
            proposal = service.propose(chapter_id, request.selected_segment_ids, provider_model)
        except ValueError as exc:
            raise HTTPException(status_code=_preview_error_status(str(exc)), detail=str(exc)) from exc
        return _repair_proposal_payload(proposal)

    @router.post("/repair-apply")
    def repair_apply(
        chapter_id: str,
        request: RepairApplyRequest,
        service: RepairService = Depends(repair_service_dependency),
    ) -> dict[str, object]:
        """Apply a previously previewed repair proposal as a new REVIEW run.

        U06 round 3 contract (C06 invariants):
        - creates a new REVIEW run instead of overwriting the approved run (the
          previous run becomes SUPERSEDED and downstream stages are marked
          stale);
        - expectedProposalHash must match the proposal hash exactly, otherwise
          409 REPAIR_PROPOSAL_CONFLICT;
        - an already consumed proposal answers 409 REPAIR_PROPOSAL_CONSUMED;
        - unknown proposal id answers 400 REPAIR_PROPOSAL_NOT_FOUND.
        """
        try:
            run = service.accept_repair(request.proposal_id, request.expected_proposal_hash)
        except RepairConflict as exc:
            raise HTTPException(status_code=409, detail=_repair_conflict_detail(str(exc))) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        stored = service.stored_proposal(request.proposal_id)
        # Every listed replacement was applied: accept_repair rejects proposals
        # whose segments are missing from the current run before writing.
        applied_count = len(stored.replacements) if stored is not None else 0
        return {"runId": run.id, "sha256": run.sha256, "appliedCount": applied_count}

    return router


class CleanRepairFakeTranslator:
    """Deterministic offline translator used by repair-preview/apply.

    Same behavior as app.api.translation.CleanFakeTranslator: Han-Viet
    conversion with locked glossary terms, zero network. Keeping a local copy
    avoids importing the translation router module into the review router.
    """

    def capabilities(self) -> dict[str, object]:
        return {
            "provider": "fake",
            "model": "fake-hanviet-v2",
            "provider_version": "2",
            "region": "local",
            "network": False,
        }

    async def translate(self, request: TranslationRequest) -> TranslationResult:
        target = convert_hanviet(request.source_text, request.terms)
        return TranslationResult(
            target_text=target,
            provider="fake",
            model="fake-hanviet-v2",
            provider_version="2",
            usage=(Usage(UsageUnit.CHARACTER.value, len(request.source_text)),),
        )


def _repair_proposal_payload(proposal: object) -> dict[str, object]:
    data = _convert(asdict(proposal))
    return {
        "id": data["id"],
        "baseRunId": data["base_run_id"],
        "baseRunSha256": data["base_run_sha256"],
        "generator": REPAIR_OFFLINE_GENERATOR,
        "providerModel": data["provider_model"],
        "storyMemoryRevisionHash": data["story_memory_revision_hash"],
        "hash": data["hash"],
        "estimatedCostVnd": data["estimated_cost_vnd"],
        "replacements": [
            {
                "sourceSegmentId": replacement["source_segment_id"],
                "sourceText": replacement["source_text"],
                "currentTargetText": replacement["current_target_text"],
                "targetText": replacement["target_text"],
            }
            for replacement in data["replacements"]
        ],
    }


def _preview_error_status(code: str) -> int:
    if code == "REPAIR_SEGMENT_REQUIRED":
        return 400
    if code == "SOURCE_SEGMENT_NOT_IN_RUN":
        return 404
    return 409


def _repair_conflict_detail(code: str) -> str:
    if code == "REPAIR_PROPOSAL_CONSUMED":
        return "REPAIR_PROPOSAL_CONSUMED"
    if code == "REPAIR_PROPOSAL_HASH_MISMATCH":
        return "REPAIR_PROPOSAL_CONFLICT,REPAIR_PROPOSAL_HASH_MISMATCH"
    return "REPAIR_PROPOSAL_CONFLICT," + code


def _convert(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _convert(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_convert(item) for item in value]
    return value


def _camelize(value: object) -> object:
    if isinstance(value, dict):
        return {_camel_key(key): _camelize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_camelize(item) for item in value]
    return value


def _camel_key(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in tail)
