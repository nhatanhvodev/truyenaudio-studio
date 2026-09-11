"""X06 - optional vector index: import validation, deterministic retrieval, rebuild.

Every vector in this file is hand-written fixture data. No test generates an
embedding, opens a socket or downloads a model: the string "embedding" in this
module always refers to numbers the *user* supplies, which is exactly the X06
contract (the studio stores and searches embeddings, it never creates one).

Coverage map (plan X06 acceptance):
- valid import -> status (dimension/modelId/entryCount/hash);
- negative matrix: schema, dimension, NaN, Infinity, empty vector, missing id,
  oversized index, missing license;
- deterministic retrieval: same input twice -> same ids in the same order, top-8
  out of 12 candidates, ties broken by reference id;
- cross-project: project A's index is never returned for project B;
- stale: a sourceHash that no longer matches the memory snapshot is not used;
- query mismatch (model/dimension) -> structured fallback + reason in the trace;
- off path: with no index the context engine returns exactly the pre-X06 result;
- rebuild: same input -> same index hash, different input -> different hash.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.api.memory import create_memory_router
from app.contracts import ChapterState, ImportKind, RightsStatus, RunStatus, SourceType
from app.db.base import session_factory
from app.db.models import Chapter, Project, SourceRevision, SourceSegment, TranslationRun
from app.modules.translation.context_engine import ContextItem, VectorContextHint, select_context
from app.modules.translation.memory_index import (
    DIMENSION_MISMATCH,
    ENTRY_INVALID,
    INDEX_DISABLED,
    INDEX_MISSING,
    INDEX_STALE,
    INDEX_TOO_LARGE,
    LICENSE_REQUIRED,
    MEMORY_NOT_APPROVED,
    OUT_OF_RANGE,
    QUERY_MISMATCH,
    QUERY_MISSING,
    REF_NOT_IN_PROJECT,
    SCHEMA_UNSUPPORTED,
    VALUE_INVALID,
    IndexLimits,
    MemoryIndexService,
    VectorQuery,
    project_memory_snapshot_hash,
)
from app.modules.translation.story_memory import StoryMemoryService
from app.settings.config import Settings

MODEL_ID = "user-imported-embedder"
MODEL_REVISION = "rev-1"
LICENSE = "CC-BY-4.0 snapshot declared by the importer"

_SEQUENCE = [0]


def _next_id() -> str:
    _SEQUENCE[0] += 1
    return f"018f0000-0000-7000-9000-{_SEQUENCE[0]:012x}"


def _artifact(
    entries: list[dict[str, object]],
    *,
    dimension: int = 2,
    model_id: str = MODEL_ID,
    source_hash: str | None = None,
    license_id: str | None = LICENSE,
    schema_version: int = 1,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schemaVersion": schema_version,
        "dimension": dimension,
        "modelId": model_id,
        "modelRevision": MODEL_REVISION,
        "vectors": entries,
    }
    if source_hash is not None:
        payload["sourceHash"] = source_hash
    if license_id is not None:
        payload["license"] = license_id
    return payload


def _vector(entry_id: str, ref_id: str, values: list[float], kind: str = "MEMORY") -> dict[str, object]:
    return {"id": entry_id, "refId": ref_id, "kind": kind, "embedding": values}


def _seed(session, *, slug: str) -> dict[str, str]:
    """One project with an APPROVED run, one APPROVED memory entry and one candidate."""
    project = Project(
        id=_next_id(),
        title=f"Truyen {slug}",
        slug=slug,
        source_type=SourceType.USER_SUPPLIED_PRIVATE.value,
        rights_status=RightsStatus.PRIVATE_ONLY.value,
        default_language="zh-CN",
        target_language="vi-VN",
    )
    session.add(project)
    session.flush()
    chapter = Chapter(id=_next_id(), project_id=project.id, ordinal=1, state=ChapterState.TRANSLATION_REVIEW.value)
    session.add(chapter)
    session.flush()
    revision = SourceRevision(
        id=_next_id(),
        chapter_id=chapter.id,
        revision_no=1,
        import_kind=ImportKind.PASTE.value,
        normalized_text="林动抬头。",
        normalized_sha256=hashlib.sha256(b"lin-dong").hexdigest(),
        han_char_count=0,
        total_char_count=5,
        normalizer_version="nfc-v1",
    )
    session.add(revision)
    session.flush()
    segment = SourceSegment(
        id=_next_id(),
        source_revision_id=revision.id,
        segment_index=0,
        paragraph_start=0,
        paragraph_end=0,
        source_text="林动抬头。",
        source_sha256=hashlib.sha256(b"lin-dong-segment").hexdigest(),
        segment_kind="SOURCE",
    )
    session.add(segment)
    session.flush()
    run = TranslationRun(
        id=_next_id(),
        chapter_id=chapter.id,
        source_revision_id=revision.id,
        prompt_version="translation-v1",
        status=RunStatus.APPROVED.value,
        translation_text_sha256=hashlib.sha256(b"ban dich").hexdigest(),
        estimated_cost_vnd=0,
        actual_cost_vnd=0,
    )
    session.add(run)
    session.flush()
    session.commit()
    return {
        "project_id": project.id,
        "chapter_id": chapter.id,
        "revision_id": revision.id,
        "segment_id": segment.id,
        "run_id": run.id,
    }


def _approve_memory(
    session,
    fixture: dict[str, str],
    *,
    entity_key: str,
    summary: str,
    valid_from_ordinal: int = 1,
) -> str:
    memory = StoryMemoryService(session)
    candidate = memory.create_candidate(
        fixture["project_id"],
        entity_key=entity_key,
        entity_type="CHARACTER",
        summary=summary,
        valid_from_ordinal=valid_from_ordinal,
    )
    approved = memory.approve(
        fixture["project_id"],
        candidate.id,
        source_run_id=fixture["run_id"],
        evidence_segment_ids=(fixture["segment_id"],),
    )
    session.commit()
    return approved.id


# --------------------------------------------------------------------------- #
# 1. Valid import
# --------------------------------------------------------------------------- #


def test_import_valid_artifact_stores_status_and_hash(db_session) -> None:
    fixture = _seed(db_session, slug="x06-valid")
    memory_id = _approve_memory(db_session, fixture, entity_key="lin-dong", summary="Nhân vật chính.")
    service = MemoryIndexService(db_session, id_factory=_next_id)

    index = service.import_artifact(
        fixture["project_id"],
        _artifact([_vector("v1", memory_id, [1.0, 0.0])]),
    )

    assert index["status"] == "READY"
    assert index["dimension"] == 2
    assert index["modelId"] == MODEL_ID
    assert index["modelRevision"] == MODEL_REVISION
    assert index["entryCount"] == 1
    assert index["license"] == LICENSE
    assert index["dataSource"] == "USER_SUPPLIED"
    assert index["stale"] is False
    assert index["disabled"] is False
    assert index["retrievalEnabled"] is True
    assert index["sourceHash"] == project_memory_snapshot_hash(db_session, fixture["project_id"])
    stored = service.index_row(fixture["project_id"])
    assert stored is not None
    assert index["indexHash"] == stored.index_hash
    assert len(index["indexHash"]) == 64
    # GET status must agree with what the import returned.
    assert service.status(fixture["project_id"]) == index


def test_index_hash_ignores_entry_order_in_the_artifact(db_session) -> None:
    fixture = _seed(db_session, slug="x06-order")
    first = _approve_memory(db_session, fixture, entity_key="a", summary="A.")
    second = _approve_memory(db_session, fixture, entity_key="b", summary="B.")
    service = MemoryIndexService(db_session, id_factory=_next_id)

    forward = service.import_artifact(
        fixture["project_id"],
        _artifact([_vector("v1", first, [1.0, 0.0]), _vector("v2", second, [0.0, 1.0])]),
    )
    reversed_index = service.import_artifact(
        fixture["project_id"],
        _artifact([_vector("v2", second, [0.0, 1.0]), _vector("v1", first, [1.0, 0.0])]),
    )

    assert forward["indexHash"] == reversed_index["indexHash"]
    assert forward["entryCount"] == reversed_index["entryCount"] == 2


# --------------------------------------------------------------------------- #
# 2. Negative matrix
# --------------------------------------------------------------------------- #


def test_import_rejects_unsupported_schema_version(db_session) -> None:
    fixture = _seed(db_session, slug="x06-schema")
    service = MemoryIndexService(db_session, id_factory=_next_id)

    with pytest.raises(ValueError, match=SCHEMA_UNSUPPORTED):
        service.import_artifact(
            fixture["project_id"],
            _artifact([_vector("v1", "ref-1", [1.0, 0.0])], schema_version=2),
        )

    with pytest.raises(ValueError, match=SCHEMA_UNSUPPORTED):
        service.import_artifact(fixture["project_id"], _artifact([], schema_version="1"))  # type: ignore[arg-type]

    assert service.index_row(fixture["project_id"]) is None


def test_import_rejects_dimension_mismatch(db_session) -> None:
    fixture = _seed(db_session, slug="x06-dimension")
    service = MemoryIndexService(db_session, id_factory=_next_id)

    with pytest.raises(ValueError, match=DIMENSION_MISMATCH):
        service.import_artifact(
            fixture["project_id"],
            _artifact([_vector("v1", "ref-1", [1.0, 0.0])], dimension=3),
        )

    with pytest.raises(ValueError, match=DIMENSION_MISMATCH):
        service.import_artifact(
            fixture["project_id"],
            _artifact([_vector("v1", "ref-1", [1.0, 0.0])], dimension=4097),
        )


@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), float("-inf")])
def test_import_rejects_non_finite_values(db_session, bad_value: float) -> None:
    fixture = _seed(db_session, slug=f"x06-nonfinite-{abs(hash(bad_value)) % 1000}")
    service = MemoryIndexService(db_session, id_factory=_next_id)

    with pytest.raises(ValueError, match=VALUE_INVALID):
        service.import_artifact(
            fixture["project_id"],
            _artifact([_vector("v1", "ref-1", [bad_value, 1.0])]),
        )


def test_import_rejects_all_zero_vector(db_session) -> None:
    fixture = _seed(db_session, slug="x06-zero")
    service = MemoryIndexService(db_session, id_factory=_next_id)

    with pytest.raises(ValueError, match=VALUE_INVALID):
        service.import_artifact(fixture["project_id"], _artifact([_vector("v1", "ref-1", [0.0, 0.0])]))


def test_import_rejects_empty_vector_list_and_empty_entry(db_session) -> None:
    fixture = _seed(db_session, slug="x06-empty")
    service = MemoryIndexService(db_session, id_factory=_next_id)

    with pytest.raises(ValueError, match=ENTRY_INVALID):
        service.import_artifact(fixture["project_id"], _artifact([]))

    with pytest.raises(ValueError, match=ENTRY_INVALID):
        service.import_artifact(fixture["project_id"], _artifact([_vector("v1", "ref-1", [])]))

    with pytest.raises(ValueError, match=ENTRY_INVALID):
        service.import_artifact(
            fixture["project_id"],
            _artifact([{"embedding": [1.0, 0.0]}]),
        )


def test_import_rejects_duplicate_entry_ids(db_session) -> None:
    fixture = _seed(db_session, slug="x06-duplicate")
    service = MemoryIndexService(db_session, id_factory=_next_id)

    with pytest.raises(ValueError, match=ENTRY_INVALID):
        service.import_artifact(
            fixture["project_id"],
            _artifact([_vector("v1", "ref-1", [1.0, 0.0]), _vector("v1", "ref-2", [0.0, 1.0])]),
        )


def test_import_rejects_index_over_entry_and_byte_budget(db_session) -> None:
    fixture = _seed(db_session, slug="x06-budget")
    entry_limited = MemoryIndexService(db_session, limits=IndexLimits(max_entries=2), id_factory=_next_id)

    with pytest.raises(ValueError, match=INDEX_TOO_LARGE):
        entry_limited.import_artifact(
            fixture["project_id"],
            _artifact([_vector(f"v{index}", f"ref-{index}", [1.0, 0.0]) for index in range(3)]),
        )

    byte_limited = MemoryIndexService(db_session, limits=IndexLimits(max_bytes=200), id_factory=_next_id)
    with pytest.raises(ValueError, match=INDEX_TOO_LARGE):
        byte_limited.import_artifact(
            fixture["project_id"],
            _artifact([_vector("v1", "ref-1", [1.0, 0.0])]),
        )
    assert entry_limited.index_row(fixture["project_id"]) is None
    assert byte_limited.index_row(fixture["project_id"]) is None


def test_import_requires_a_declared_license(db_session) -> None:
    fixture = _seed(db_session, slug="x06-license")
    service = MemoryIndexService(db_session, id_factory=_next_id)

    with pytest.raises(ValueError, match=LICENSE_REQUIRED):
        service.import_artifact(
            fixture["project_id"],
            _artifact([_vector("v1", "ref-1", [1.0, 0.0])], license_id=None),
        )

    with pytest.raises(ValueError, match=LICENSE_REQUIRED):
        service.import_artifact(
            fixture["project_id"],
            _artifact([_vector("v1", "ref-1", [1.0, 0.0])], license_id="   "),
        )
    assert not service.status(fixture["project_id"])["configured"]


def test_import_rejects_unknown_project(db_session) -> None:
    service = MemoryIndexService(db_session, id_factory=_next_id)

    with pytest.raises(ValueError, match="PROJECT_NOT_FOUND"):
        service.import_artifact("018f0000-0000-7000-9000-ffffffffffff", _artifact([_vector("v1", "r", [1.0, 0.0])]))


# --------------------------------------------------------------------------- #
# 3. Deterministic retrieval
# --------------------------------------------------------------------------- #


def _twelve_memory_index(db_session, fixture) -> tuple[MemoryIndexService, list[str]]:
    memory_ids = [
        _approve_memory(db_session, fixture, entity_key=f"key-{index:02d}", summary=f"Ký ức {index}.")
        for index in range(12)
    ]
    service = MemoryIndexService(db_session, id_factory=_next_id)
    # cos(query=[1,0], vector=[i+1, 1]) grows with i, so the ranking is known:
    # the eight highest i values win, ordered from 11 down to 4.
    service.import_artifact(
        fixture["project_id"],
        _artifact([_vector(f"v{index:02d}", memory_ids[index], [float(index + 1), 1.0]) for index in range(12)]),
    )
    return service, memory_ids


def test_retrieval_is_deterministic_and_returns_exactly_top_8(db_session) -> None:
    fixture = _seed(db_session, slug="x06-top8")
    service, memory_ids = _twelve_memory_index(db_session, fixture)
    query = VectorQuery(embedding=(1.0, 0.0), model_id=MODEL_ID, dimension=2)

    first = service.retrieve(fixture["project_id"], query=query, ordinal=1)
    second = service.retrieve(fixture["project_id"], query=query, ordinal=1)

    assert first.used is True
    assert first.reason == "OK"
    assert first.selected == second.selected
    assert first.ranked == second.ranked
    assert len(first.selected) == 8
    assert first.selected == tuple(memory_ids[index] for index in (11, 10, 9, 8, 7, 6, 5, 4))
    # Everything that lost keeps a machine-readable reason in the trace.
    assert {reason for _ref, reason in first.excluded} == {"NOT_TOP_K"}
    assert len(first.excluded) == 4
    assert set(first.selected).isdisjoint(ref for ref, _reason in first.excluded)


def test_retrieval_breaks_ties_by_reference_id(db_session) -> None:
    fixture = _seed(db_session, slug="x06-ties")
    memory_ids = sorted(
        _approve_memory(db_session, fixture, entity_key=f"tie-{index:02d}", summary=f"Tie {index}.")
        for index in range(10)
    )
    service = MemoryIndexService(db_session, id_factory=_next_id)
    service.import_artifact(
        fixture["project_id"],
        _artifact([_vector(f"v{index:02d}", memory_id, [1.0, 1.0]) for index, memory_id in enumerate(memory_ids)]),
    )
    query = VectorQuery(embedding=(1.0, 1.0), model_id=MODEL_ID, dimension=2)

    first = service.retrieve(fixture["project_id"], query=query, ordinal=1)
    second = service.retrieve(fixture["project_id"], query=query, ordinal=1)

    assert first.selected == second.selected
    assert first.selected == tuple(memory_ids[:8])


# --------------------------------------------------------------------------- #
# 4. Cross-project isolation
# --------------------------------------------------------------------------- #


def test_index_of_another_project_is_never_used(db_session) -> None:
    project_a = _seed(db_session, slug="x06-project-a")
    project_b = _seed(db_session, slug="x06-project-b")
    memory_a = _approve_memory(db_session, project_a, entity_key="a", summary="Của A.")
    memory_b = _approve_memory(db_session, project_b, entity_key="b", summary="Của B.")
    service = MemoryIndexService(db_session, id_factory=_next_id)
    service.import_artifact(
        project_a["project_id"],
        _artifact([_vector("v1", memory_a, [1.0, 0.0])]),
    )
    query = VectorQuery(embedding=(1.0, 0.0), model_id=MODEL_ID, dimension=2)

    assert service.retrieve(project_b["project_id"], query=query, ordinal=1).reason == INDEX_MISSING
    assert service.status(project_b["project_id"])["configured"] is False
    assert service.retrieve(project_a["project_id"], query=query, ordinal=1).selected == (memory_a,)

    # A vector that points into another project is dropped before ranking, so an
    # artifact smuggled across projects cannot surface foreign memory.
    service.import_artifact(
        project_b["project_id"],
        _artifact([_vector("v1", memory_a, [1.0, 0.0]), _vector("v2", memory_b, [0.0, 1.0])], source_hash=None),
    )
    retrieval = service.retrieve(project_b["project_id"], query=query, ordinal=1)
    assert retrieval.selected == (memory_b,)
    assert (memory_a, REF_NOT_IN_PROJECT) in retrieval.excluded


def test_retrieval_drops_memory_that_is_not_approved_or_out_of_range(db_session) -> None:
    fixture = _seed(db_session, slug="x06-eligible")
    approved = _approve_memory(db_session, fixture, entity_key="approved", summary="Đã duyệt.")
    future = _approve_memory(db_session, fixture, entity_key="future", summary="Chương sau.", valid_from_ordinal=5)
    candidate = StoryMemoryService(db_session).create_candidate(
        fixture["project_id"],
        entity_key="candidate",
        entity_type="FACT",
        summary="Chưa duyệt.",
        valid_from_ordinal=1,
    )
    db_session.commit()
    service = MemoryIndexService(db_session, id_factory=_next_id)
    service.import_artifact(
        fixture["project_id"],
        _artifact(
            [
                _vector("v1", approved, [1.0, 0.0]),
                _vector("v2", future, [1.0, 0.0]),
                _vector("v3", candidate.id, [1.0, 0.0]),
            ]
        ),
    )
    query = VectorQuery(embedding=(1.0, 0.0), model_id=MODEL_ID, dimension=2)

    retrieval = service.retrieve(fixture["project_id"], query=query, ordinal=1)

    assert retrieval.selected == (approved,)
    assert set(retrieval.excluded) == {(future, OUT_OF_RANGE), (candidate.id, MEMORY_NOT_APPROVED)}

    # At ordinal 5 the later memory is in range, the candidate never is.
    later = service.retrieve(fixture["project_id"], query=query, ordinal=5)
    assert set(later.selected) == {approved, future}
    assert (candidate.id, MEMORY_NOT_APPROVED) in later.excluded


# --------------------------------------------------------------------------- #
# 5. Stale index
# --------------------------------------------------------------------------- #


def test_stale_index_is_not_used_and_says_why(db_session) -> None:
    fixture = _seed(db_session, slug="x06-stale")
    memory_id = _approve_memory(db_session, fixture, entity_key="old", summary="Cũ.")
    service = MemoryIndexService(db_session, id_factory=_next_id)
    service.import_artifact(
        fixture["project_id"],
        _artifact([_vector("v1", memory_id, [1.0, 0.0])]),
    )
    query = VectorQuery(embedding=(1.0, 0.0), model_id=MODEL_ID, dimension=2)
    assert service.retrieve(fixture["project_id"], query=query, ordinal=1).used is True

    # A new approved memory changes the snapshot the index was built from.
    _approve_memory(db_session, fixture, entity_key="new", summary="Mới.")

    status = service.status(fixture["project_id"])
    retrieval = service.retrieve(fixture["project_id"], query=query, ordinal=1)

    assert status["stale"] is True
    assert status["status"] == "STALE"
    assert status["retrievalEnabled"] is False
    assert status["reason"] == INDEX_STALE
    assert retrieval.used is False
    assert retrieval.reason == INDEX_STALE
    assert retrieval.selected == ()


# --------------------------------------------------------------------------- #
# 6. Query embedding matching / fallback
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("query", "expected_reason"),
    [
        (VectorQuery(embedding=(1.0, 0.0), model_id="another-model", dimension=2), QUERY_MISMATCH),
        (VectorQuery(embedding=(1.0, 0.0, 0.0), model_id=MODEL_ID, dimension=2), QUERY_MISMATCH),
        (VectorQuery(embedding=(1.0,), model_id=MODEL_ID, dimension=1), QUERY_MISMATCH),
    ],
)
def test_query_mismatch_falls_back_to_structured(db_session, query, expected_reason) -> None:
    fixture = _seed(db_session, slug=f"x06-query-{abs(hash(query.model_id + str(query.dimension))) % 10000}")
    memory_id = _approve_memory(db_session, fixture, entity_key="lin-dong", summary="Nhân vật chính.")
    service = MemoryIndexService(db_session, id_factory=_next_id)
    service.import_artifact(fixture["project_id"], _artifact([_vector("v1", memory_id, [1.0, 0.0])]))

    retrieval = service.retrieve(fixture["project_id"], query=query, ordinal=1)
    items = tuple(
        ContextItem(id=entry.id, kind=entry.entity_type, text=entry.summary)
        for entry in StoryMemoryService(db_session).memory_for(fixture["project_id"], 1)
    )
    structured = select_context(items, token_budget=1200)
    decorated = select_context(items, token_budget=1200, vector=retrieval.hint())

    assert retrieval.used is False
    assert retrieval.reason == expected_reason
    assert retrieval.fallback is True
    assert decorated.selected == structured.selected
    assert decorated.sha256 == structured.sha256
    assert decorated.vector is not None and decorated.vector.reason == expected_reason


def test_missing_query_embedding_never_generates_one(db_session) -> None:
    fixture = _seed(db_session, slug="x06-no-query")
    memory_id = _approve_memory(db_session, fixture, entity_key="lin-dong", summary="Nhân vật chính.")
    service = MemoryIndexService(db_session, id_factory=_next_id)
    before = service.status(fixture["project_id"])
    service.import_artifact(fixture["project_id"], _artifact([_vector("v1", memory_id, [1.0, 0.0])]))

    retrieval = service.retrieve(fixture["project_id"], query=None, ordinal=1)

    assert before["configured"] is False and before["entryCount"] == 0
    assert retrieval.used is False
    assert retrieval.reason == QUERY_MISSING
    # The index is untouched: no embedding was invented to fill the gap.
    assert service.status(fixture["project_id"])["entryCount"] == 1


def test_disabled_index_is_not_used(db_session) -> None:
    fixture = _seed(db_session, slug="x06-disabled")
    memory_id = _approve_memory(db_session, fixture, entity_key="lin-dong", summary="Nhân vật chính.")
    service = MemoryIndexService(db_session, id_factory=_next_id)
    payload = _artifact([_vector("v1", memory_id, [1.0, 0.0])])
    payload["disabled"] = True
    index = service.import_artifact(fixture["project_id"], payload)

    retrieval = service.retrieve(
        fixture["project_id"], query=VectorQuery(embedding=(1.0, 0.0), model_id=MODEL_ID), ordinal=1
    )

    assert index["disabled"] is True
    assert index["retrievalEnabled"] is False
    assert retrieval.used is False
    assert retrieval.reason == INDEX_DISABLED


# --------------------------------------------------------------------------- #
# 7. Off path
# --------------------------------------------------------------------------- #


def test_off_path_context_selection_is_identical_without_any_index(db_session) -> None:
    fixture = _seed(db_session, slug="x06-offpath")
    first = _approve_memory(db_session, fixture, entity_key="a", summary="A.")
    second = _approve_memory(db_session, fixture, entity_key="b", summary="B.")
    items = (
        ContextItem(id=first, kind="CHARACTER", text="A."),
        ContextItem(id=second, kind="CHARACTER", text="B."),
    )

    pre_x06 = select_context(items, token_budget=1200)
    repeated = select_context(items, token_budget=1200)
    explicit_none = select_context(items, token_budget=1200, vector=None)
    fallback = select_context(
        items, token_budget=1200, vector=VectorContextHint(used=False, reason=INDEX_MISSING)
    )

    # Same result as before the feature existed - twice, and with an explicit None.
    assert pre_x06 == repeated == explicit_none
    assert pre_x06.vector is None

    # The fallback hint changes neither the selection nor the hash: the payload
    # the hash covers is still exactly {items, total_tokens}.
    assert fallback.selected == pre_x06.selected
    assert fallback.excluded == pre_x06.excluded
    assert fallback.total_tokens == pre_x06.total_tokens
    assert fallback.sha256 == pre_x06.sha256
    expected_payload = {
        "items": [
            {"id": first, "kind": "CHARACTER", "text": "A.", "priority": 100, "estimated_tokens": 1},
            {"id": second, "kind": "CHARACTER", "text": "B.", "priority": 100, "estimated_tokens": 1},
        ],
        "total_tokens": 2,
    }
    assert pre_x06.sha256 == hashlib.sha256(
        json.dumps(expected_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    # And a project without an index reports a fallback, not an error.
    service = MemoryIndexService(db_session, id_factory=_next_id)
    retrieval = service.retrieve(fixture["project_id"], query=None, ordinal=1)
    assert retrieval.reason == INDEX_MISSING
    assert service.status(fixture["project_id"])["status"] == "MISSING"


# --------------------------------------------------------------------------- #
# 8. Rebuild determinism
# --------------------------------------------------------------------------- #


def test_rebuild_from_the_same_artifact_reproduces_the_same_hash(db_session) -> None:
    fixture = _seed(db_session, slug="x06-rebuild")
    memory_id = _approve_memory(db_session, fixture, entity_key="lin-dong", summary="Nhân vật chính.")
    service = MemoryIndexService(db_session, id_factory=_next_id)
    imported = service.import_artifact(fixture["project_id"], _artifact([_vector("v1", memory_id, [1.0, 0.0])]))

    rebuilt = service.rebuild(fixture["project_id"])
    again = service.rebuild(fixture["project_id"])

    assert rebuilt["indexHash"] == imported["indexHash"]
    assert again["indexHash"] == imported["indexHash"]
    assert rebuilt["rebuilt"] is False
    assert again["previousIndexHash"] == imported["indexHash"]
    assert rebuilt["entryCount"] == 1


def test_rebuild_reflects_a_different_imported_artifact(db_session) -> None:
    fixture = _seed(db_session, slug="x06-rebuild-change")
    memory_id = _approve_memory(db_session, fixture, entity_key="lin-dong", summary="Nhân vật chính.")
    service = MemoryIndexService(db_session, id_factory=_next_id)
    first = service.import_artifact(fixture["project_id"], _artifact([_vector("v1", memory_id, [1.0, 0.0])]))
    second = service.import_artifact(fixture["project_id"], _artifact([_vector("v1", memory_id, [0.0, 1.0])]))

    assert first["indexHash"] != second["indexHash"]
    assert service.rebuild(fixture["project_id"])["indexHash"] == second["indexHash"]

    with pytest.raises(ValueError, match="EMBEDDING_INDEX_NOT_FOUND"):
        MemoryIndexService(db_session, id_factory=_next_id).rebuild("018f0000-0000-7000-9000-ffffffffffff")


def test_rebuild_marks_a_corrupted_artifact_failed_instead_of_inventing_vectors(db_session) -> None:
    fixture = _seed(db_session, slug="x06-rebuild-corrupt")
    memory_id = _approve_memory(db_session, fixture, entity_key="lin-dong", summary="Nhân vật chính.")
    service = MemoryIndexService(db_session, id_factory=_next_id)
    service.import_artifact(fixture["project_id"], _artifact([_vector("v1", memory_id, [1.0, 0.0])]))
    row = service.index_row(fixture["project_id"])
    assert row is not None
    row.artifact_json = {"schemaVersion": 99}
    db_session.flush()
    db_session.commit()

    with pytest.raises(ValueError, match=SCHEMA_UNSUPPORTED):
        service.rebuild(fixture["project_id"])

    status = service.status(fixture["project_id"])
    assert status["status"] == "FAILED"
    assert status["retrievalEnabled"] is False


# --------------------------------------------------------------------------- #
# 9. HTTP surface
# --------------------------------------------------------------------------- #


def _client(tmp_path: Path) -> TestClient:
    app = FastAPI()
    app.include_router(create_memory_router(Settings(data_root=tmp_path)))
    return TestClient(app)


def test_embedding_index_api_import_status_rebuild_and_retrieve(migrated_engine: Engine, tmp_path: Path) -> None:
    session = session_factory(migrated_engine)()
    try:
        fixture = _seed(session, slug="x06-api")
        memory_id = _approve_memory(session, fixture, entity_key="lin-dong", summary="Nhân vật chính.")
        project_id = fixture["project_id"]
        others = [
            _approve_memory(session, fixture, entity_key=f"extra-{index}", summary=f"Extra {index}.")
            for index in range(3)
        ]
    finally:
        session.close()

    payload = _artifact(
        [_vector("v0", memory_id, [1.0, 0.0])]
        + [_vector(f"v{index + 1}", ref, [0.5, 1.0]) for index, ref in enumerate(others)]
    )

    with _client(tmp_path) as client:
        imported = client.post(f"/api/projects/{project_id}/memory/embedding-index/import", json=payload)
        assert imported.status_code == 200, imported.text
        index = imported.json()["index"]
        assert index["entryCount"] == 4
        assert index["status"] == "READY"
        assert index["dataSource"] == "USER_SUPPLIED"

        status = client.get(f"/api/projects/{project_id}/memory/embedding-index/status")
        assert status.status_code == 200
        assert status.json()["index"] == index

        rebuilt = client.post(f"/api/projects/{project_id}/memory/embedding-index/rebuild")
        assert rebuilt.status_code == 200
        assert rebuilt.json()["index"]["indexHash"] == index["indexHash"]
        assert rebuilt.json()["index"]["rebuilt"] is False

        retrieved = client.post(
            f"/api/projects/{project_id}/memory/embedding-index/retrieve",
            json={"ordinal": 1, "query": {"embedding": [1.0, 0.0], "modelId": MODEL_ID, "dimension": 2}},
        )
        assert retrieved.status_code == 200, retrieved.text
        body = retrieved.json()
        assert body["retrieval"]["used"] is True
        assert body["retrieval"]["selected"][0] == memory_id
        assert body["selection"]["selected"] == body["structured"]["selected"]
        assert body["selection"]["vectorUsed"] is True

        mismatched = client.post(
            f"/api/projects/{project_id}/memory/embedding-index/retrieve",
            json={"ordinal": 1, "query": {"embedding": [1.0, 0.0], "modelId": "other-model", "dimension": 2}},
        )
        assert mismatched.status_code == 200
        assert mismatched.json()["retrieval"]["reason"] == QUERY_MISMATCH
        assert mismatched.json()["selection"]["sha256"] == mismatched.json()["structured"]["sha256"]

        no_query = client.post(
            f"/api/projects/{project_id}/memory/embedding-index/retrieve",
            json={"ordinal": 1},
        )
        assert no_query.status_code == 200
        assert no_query.json()["retrieval"]["reason"] == QUERY_MISSING


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        (_artifact([_vector("v1", "ref-1", [1.0, 0.0])], schema_version=7), SCHEMA_UNSUPPORTED),
        (_artifact([_vector("v1", "ref-1", [1.0, 0.0])], dimension=5), DIMENSION_MISMATCH),
        (_artifact([_vector("v1", "ref-1", [1.0, 0.0])], license_id=None), LICENSE_REQUIRED),
        (_artifact([{"id": "v1", "embedding": []}]), ENTRY_INVALID),
        (_artifact([{"embedding": [1.0, 0.0]}]), ENTRY_INVALID),
        (_artifact([_vector("v1", "ref-1", [0.0, 0.0])]), VALUE_INVALID),
    ],
)
def test_embedding_index_import_api_rejects_with_named_codes(
    migrated_engine: Engine, tmp_path: Path, payload: dict[str, object], code: str
) -> None:
    session = session_factory(migrated_engine)()
    try:
        fixture = _seed(session, slug=f"x06-api-negative-{code}")
    finally:
        session.close()

    with _client(tmp_path) as client:
        response = client.post(
            f"/api/projects/{fixture['project_id']}/memory/embedding-index/import", json=payload
        )

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == code
        assert (
            client.get(f"/api/projects/{fixture['project_id']}/memory/embedding-index/status").json()["index"][
                "configured"
            ]
            is False
        )
