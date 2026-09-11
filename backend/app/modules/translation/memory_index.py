"""Optional, user-supplied vector index for translation context (task X06).

The index is **data the user imports**, never something this application
produces: no cloud call, no local model and no background runtime is started to
create an embedding. This module only stores, re-validates and searches what was
handed to it.

Contract highlights (plan C08 / task X06):

- one index per project, bound to modelId + dimension + sourceHash. sourceHash is
  the hash of the memory snapshot the vectors were built from (declared by the
  importer, or pinned to the project's current APPROVED memory snapshot when the
  artifact declares none). When the current snapshot no longer matches, the index
  is **stale** and is not used at all;
- retrieval is a *decoration* on top of the deterministic structured selector in
  app.modules.translation.context_engine, never a replacement: no index, a
  disabled index, a stale index, a missing query embedding or a query embedding
  whose model/dimension does not match all fall back to the structured selection
  unchanged and only record a reason in the trace;
- everything is deterministic: the same artifact yields the same index hash
  (entry order in the payload is irrelevant - entries are canonicalised by id)
  and the same index + the same query embedding yield the same top-8 reference
  ids in the same order (exact cosine, ties broken by reference id).

The persisted artifact is the canonical JSON of the validated payload, so
'rebuild' re-derives the index from the artifact the user imported and never
invents a new embedding. Supported wire shape (the C08 sketch spells the same
fields as sourceSnapshotHash/items; this module implements the key names X06
specifies)::

    {"schemaVersion": 1, "dimension": 1536, "modelId": "user-model",
     "modelRevision": "rev-1", "sourceHash": "<sha256 of the memory snapshot>",
     "license": "user-declared license", "disabled": false,
     "vectors": [{"id": "v1", "refId": "<memory id>", "kind": "MEMORY",
                  "embedding": [0.1, 0.2]}]}
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import math

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts import RunStatus, new_id
from app.db.models import (
    MemoryIndex as MemoryIndexRow,
    MemoryIndexDataSource,
    MemoryIndexStatus,
    Project,
    StoryMemoryEntry as StoryMemoryRow,
    TranslationRun,
)
from app.modules.translation.context_engine import VectorContextHint
from app.modules.translation.story_memory import STATUS_APPROVED


#: Artifact schema versions this module understands. An unknown version is
#: rejected instead of being read with best-effort assumptions.
SUPPORTED_SCHEMA_VERSIONS: tuple[int, ...] = (1,)

#: Cosine factors stay exact and bounded: the width is capped and only the best
#: TOP_K reference ids are ever selected.
MIN_DIMENSION = 1
MAX_DIMENSION = 4096
TOP_K = 8

#: Memory budget (plan C08). MAX_BYTES bounds the canonical JSON payload of the
#: artifact, which is exactly what is persisted and re-validated.
MAX_ENTRIES = 10_000
MAX_BYTES = 200 * 1024 * 1024

SCHEMA_UNSUPPORTED = "EMBEDDING_SCHEMA_UNSUPPORTED"
DIMENSION_MISMATCH = "EMBEDDING_DIMENSION_MISMATCH"
VALUE_INVALID = "EMBEDDING_VALUE_INVALID"
ENTRY_INVALID = "EMBEDDING_ENTRY_INVALID"
INDEX_TOO_LARGE = "EMBEDDING_INDEX_TOO_LARGE"
LICENSE_REQUIRED = "EMBEDDING_LICENSE_REQUIRED"
MODEL_REQUIRED = "EMBEDDING_MODEL_REQUIRED"
SOURCE_HASH_INVALID = "EMBEDDING_SOURCE_HASH_INVALID"

PROJECT_NOT_FOUND = "PROJECT_NOT_FOUND"
INDEX_NOT_FOUND = "EMBEDDING_INDEX_NOT_FOUND"

INDEX_MISSING = "EMBEDDING_INDEX_MISSING"
INDEX_DISABLED = "EMBEDDING_INDEX_DISABLED"
INDEX_STALE = "EMBEDDING_INDEX_STALE"
INDEX_INVALID = "EMBEDDING_INDEX_INVALID"
QUERY_MISSING = "EMBEDDING_QUERY_MISSING"
QUERY_MISMATCH = "EMBEDDING_QUERY_MISMATCH"
RETRIEVAL_OK = "OK"

#: Reasons attached to individual index entries that were dropped before ranking.
REF_NOT_IN_PROJECT = "REF_NOT_IN_PROJECT"
MEMORY_NOT_APPROVED = "MEMORY_NOT_APPROVED"
EVIDENCE_NOT_APPROVED = "EVIDENCE_NOT_APPROVED"
OUT_OF_RANGE = "OUT_OF_RANGE"
NOT_TOP_K = "NOT_TOP_K"

STATUS_READY = MemoryIndexStatus.READY.value
STATUS_STALE = MemoryIndexStatus.STALE.value
STATUS_FAILED = MemoryIndexStatus.FAILED.value
STATUS_MISSING = "MISSING"

DATA_SOURCE_USER_SUPPLIED = MemoryIndexDataSource.USER_SUPPLIED.value

DEFAULT_ENTRY_KIND = "MEMORY"


@dataclass(frozen=True)
class IndexLimits:
    """Memory budget for one index; injectable so tests can stay small."""

    max_entries: int = MAX_ENTRIES
    max_bytes: int = MAX_BYTES
    min_dimension: int = MIN_DIMENSION
    max_dimension: int = MAX_DIMENSION
    top_k: int = TOP_K


DEFAULT_LIMITS = IndexLimits()


@dataclass(frozen=True)
class EmbeddingEntry:
    """One user-supplied vector, pointing at the memory revision it embeds."""

    id: str
    ref_id: str
    kind: str
    embedding: tuple[float, ...]


@dataclass(frozen=True)
class EmbeddingArtifact:
    """A validated, canonicalised user-supplied embedding artifact."""

    schema_version: int
    dimension: int
    model_id: str
    model_revision: str | None
    license: str
    source_hash: str
    entries: tuple[EmbeddingEntry, ...]
    index_hash: str
    byte_size: int
    artifact_json: dict[str, object]

    @property
    def entry_count(self) -> int:
        return len(self.entries)


@dataclass(frozen=True)
class VectorQuery:
    """A query embedding the caller supplies; never generated by this module."""

    embedding: tuple[float, ...]
    model_id: str
    dimension: int | None = None
    model_revision: str | None = None
    #: Provenance only: this module never sees the query text, so equality of
    #: queryHash and the text is enforced by whoever owns that text.
    query_hash: str | None = None


@dataclass(frozen=True)
class VectorRetrieval:
    """What the optional vector layer contributed, and why (trace)."""

    used: bool
    reason: str
    selected: tuple[str, ...] = ()
    ranked: tuple[tuple[str, float], ...] = ()
    excluded: tuple[tuple[str, str], ...] = ()
    index_id: str | None = None
    index_hash: str | None = None
    dimension: int | None = None
    model_id: str | None = None

    @property
    def fallback(self) -> bool:
        """True when the structured selection must be used unchanged."""
        return not self.used

    def hint(self) -> VectorContextHint:
        """The minimal view the context engine consumes."""
        return VectorContextHint(used=self.used, reason=self.reason, selected=self.selected)

    def trace(self) -> dict[str, object]:
        return {
            "used": self.used,
            "reason": self.reason,
            "selected": list(self.selected),
            "ranked": [{"refId": ref_id, "score": score} for ref_id, score in self.ranked],
            "excluded": [{"refId": ref_id, "reason": reason} for ref_id, reason in self.excluded],
            "indexId": self.index_id,
            "indexHash": self.index_hash,
            "dimension": self.dimension,
            "modelId": self.model_id,
        }


def _canonical_bytes(payload: object) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _required_text(value: object, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(code)
    return value.strip()


def _optional_text(value: object, code: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(code)
    return value.strip()


def _validated_source_hash(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError(SOURCE_HASH_INVALID)
    candidate = value.strip()
    if len(candidate) != 64 or any(char not in "0123456789abcdef" for char in candidate):
        raise ValueError(SOURCE_HASH_INVALID)
    return candidate


def compute_index_hash(artifact_json: Mapping[str, object]) -> str:
    return hashlib.sha256(_canonical_bytes(artifact_json)).hexdigest()


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    """Exact cosine similarity of two equal-width, finite, non-zero vectors."""
    if len(left) != len(right):
        raise ValueError(DIMENSION_MISMATCH)
    dot = math.fsum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(math.fsum(value * value for value in left))
    right_norm = math.sqrt(math.fsum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        raise ValueError(VALUE_INVALID)
    return dot / (left_norm * right_norm)


def validate_artifact(
    payload: Mapping[str, object],
    *,
    limits: IndexLimits = DEFAULT_LIMITS,
    source_hash: str | None = None,
) -> EmbeddingArtifact:
    """Validate a user-supplied artifact; every rejection is a named code.

    The caller supplies a source_hash fallback (the project's current APPROVED
    memory snapshot); an artifact that declares its own sourceHash keeps it.
    """
    if not isinstance(payload, Mapping):
        raise ValueError(ENTRY_INVALID)

    schema_version = payload.get("schemaVersion")
    if isinstance(schema_version, bool) or not isinstance(schema_version, int):
        raise ValueError(SCHEMA_UNSUPPORTED)
    if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        raise ValueError(SCHEMA_UNSUPPORTED)

    dimension = payload.get("dimension")
    if (
        isinstance(dimension, bool)
        or not isinstance(dimension, int)
        or dimension < limits.min_dimension
        or dimension > limits.max_dimension
    ):
        raise ValueError(DIMENSION_MISMATCH)

    model_id = _required_text(payload.get("modelId"), MODEL_REQUIRED)
    model_revision = _optional_text(payload.get("modelRevision"), MODEL_REQUIRED)
    license_id = _required_text(payload.get("license"), LICENSE_REQUIRED)

    declared_source = payload.get("sourceHash")
    if declared_source is None or (isinstance(declared_source, str) and not declared_source.strip()):
        resolved_source = source_hash
    else:
        resolved_source = _validated_source_hash(declared_source)
    if resolved_source is None:
        raise ValueError(SOURCE_HASH_INVALID)

    vectors = payload.get("vectors")
    if isinstance(vectors, (str, bytes)) or not isinstance(vectors, Sequence):
        raise ValueError(ENTRY_INVALID)
    if len(vectors) == 0:
        raise ValueError(ENTRY_INVALID)
    if len(vectors) > limits.max_entries:
        raise ValueError(INDEX_TOO_LARGE)

    seen: set[str] = set()
    entries = tuple(sorted((_validate_entry(raw, dimension, seen) for raw in vectors), key=lambda entry: entry.id))

    artifact_json: dict[str, object] = {
        "schemaVersion": schema_version,
        "dimension": dimension,
        "modelId": model_id,
        "modelRevision": model_revision,
        "license": license_id,
        "sourceHash": resolved_source,
        "vectors": [
            {
                "id": entry.id,
                "refId": entry.ref_id,
                "kind": entry.kind,
                "embedding": list(entry.embedding),
            }
            for entry in entries
        ],
    }
    encoded = _canonical_bytes(artifact_json)
    if len(encoded) > limits.max_bytes:
        raise ValueError(INDEX_TOO_LARGE)

    return EmbeddingArtifact(
        schema_version=schema_version,
        dimension=dimension,
        model_id=model_id,
        model_revision=model_revision,
        license=license_id,
        source_hash=resolved_source,
        entries=entries,
        index_hash=hashlib.sha256(encoded).hexdigest(),
        byte_size=len(encoded),
        artifact_json=artifact_json,
    )


def _validate_entry(raw: object, dimension: int, seen: set[str]) -> EmbeddingEntry:
    if not isinstance(raw, Mapping):
        raise ValueError(ENTRY_INVALID)
    entry_id = _required_text(raw.get("id"), ENTRY_INVALID)
    if entry_id in seen:
        raise ValueError(ENTRY_INVALID)
    seen.add(entry_id)
    ref_id = _optional_text(raw.get("refId"), ENTRY_INVALID) or entry_id
    kind = _optional_text(raw.get("kind"), ENTRY_INVALID) or DEFAULT_ENTRY_KIND
    embedding = raw.get("embedding")
    if isinstance(embedding, (str, bytes)) or not isinstance(embedding, Sequence) or len(embedding) == 0:
        raise ValueError(ENTRY_INVALID)
    if len(embedding) != dimension:
        raise ValueError(DIMENSION_MISMATCH)
    values: list[float] = []
    for value in embedding:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(VALUE_INVALID)
        number = float(value)
        if not math.isfinite(number):
            raise ValueError(VALUE_INVALID)
        values.append(number)
    if not any(values):
        raise ValueError(VALUE_INVALID)
    return EmbeddingEntry(id=entry_id, ref_id=ref_id, kind=kind, embedding=tuple(values))


def project_memory_snapshot_hash(session: Session, project_id: str) -> str:
    """Deterministic hash of the project's APPROVED story-memory snapshot.

    The index is bound to this snapshot: approving, revising or superseding a
    memory entry changes the hash and makes an older index stale.
    """
    rows = session.scalars(
        select(StoryMemoryRow)
        .where(
            StoryMemoryRow.project_id == project_id,
            StoryMemoryRow.status == STATUS_APPROVED,
        )
        .order_by(StoryMemoryRow.entity_key, StoryMemoryRow.revision_no, StoryMemoryRow.id)
    ).all()
    payload = [
        {
            "id": row.id,
            "entity_key": row.entity_key,
            "entity_type": row.entity_type,
            "summary": row.summary,
            "valid_from_ordinal": row.valid_from_ordinal,
            "valid_to_ordinal": row.valid_to_ordinal,
            "revision_no": row.revision_no,
        }
        for row in rows
    ]
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def stored_entries(row: MemoryIndexRow) -> tuple[EmbeddingEntry, ...]:
    """Read back the entries of a persisted artifact, skipping malformed ones."""
    payload = row.artifact_json
    raw_vectors = payload.get("vectors") if isinstance(payload, Mapping) else None
    if isinstance(raw_vectors, (str, bytes)) or not isinstance(raw_vectors, Sequence):
        return ()
    entries: list[EmbeddingEntry] = []
    for raw in raw_vectors:
        if not isinstance(raw, Mapping):
            continue
        entry_id = raw.get("id")
        embedding = raw.get("embedding")
        if not isinstance(entry_id, str) or not entry_id:
            continue
        if isinstance(embedding, (str, bytes)) or not isinstance(embedding, Sequence) or len(embedding) == 0:
            continue
        values: list[float] = []
        for value in embedding:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                break
            values.append(float(value))
        else:
            ref_id = raw.get("refId")
            kind = raw.get("kind")
            entries.append(
                EmbeddingEntry(
                    id=entry_id,
                    ref_id=ref_id if isinstance(ref_id, str) and ref_id else entry_id,
                    kind=kind if isinstance(kind, str) and kind else DEFAULT_ENTRY_KIND,
                    embedding=tuple(values),
                )
            )
    return tuple(entries)


class MemoryIndexService:
    """Import, inspect, rebuild and query one project's optional vector index.

    Every method is deterministic given the same database state and the same
    inputs, and no method ever produces an embedding.
    """

    def __init__(
        self,
        session: Session,
        *,
        limits: IndexLimits = DEFAULT_LIMITS,
        id_factory: Callable[[], str] = new_id,
    ) -> None:
        self.session = session
        self.limits = limits
        self.id_factory = id_factory

    def import_artifact(self, project_id: str, payload: Mapping[str, object]) -> dict[str, object]:
        """Store a validated user-supplied artifact, replacing any earlier index."""
        if not isinstance(payload, Mapping):
            raise ValueError(ENTRY_INVALID)
        if self.session.get(Project, project_id) is None:
            raise ValueError(PROJECT_NOT_FOUND)

        artifact = validate_artifact(
            payload,
            limits=self.limits,
            source_hash=project_memory_snapshot_hash(self.session, project_id),
        )
        disabled = payload.get("disabled") is True

        existing = self.index_row(project_id)
        if existing is not None:
            self.session.delete(existing)
            self.session.flush()

        self.session.add(
            MemoryIndexRow(
                id=self.id_factory(),
                project_id=project_id,
                schema_version=artifact.schema_version,
                embedding_model_id=artifact.model_id,
                embedding_model_revision=artifact.model_revision,
                dimension=artifact.dimension,
                license=artifact.license,
                source_hash=artifact.source_hash,
                data_source=DATA_SOURCE_USER_SUPPLIED,
                status=self._materialized_status(project_id, artifact.source_hash),
                disabled=disabled,
                entry_count=artifact.entry_count,
                byte_size=artifact.byte_size,
                index_hash=artifact.index_hash,
                artifact_json=artifact.artifact_json,
            )
        )
        self.session.flush()
        self.session.commit()
        return self.status(project_id)

    def rebuild(self, project_id: str) -> dict[str, object]:
        """Re-derive the index from the *stored* artifact; never a new embedding."""
        row = self.index_row(project_id)
        if row is None:
            raise ValueError(INDEX_NOT_FOUND)
        previous = row.index_hash
        try:
            artifact = validate_artifact(row.artifact_json, limits=self.limits, source_hash=row.source_hash)
        except ValueError:
            row.status = STATUS_FAILED
            self.session.flush()
            self.session.commit()
            raise

        row.schema_version = artifact.schema_version
        row.embedding_model_id = artifact.model_id
        row.embedding_model_revision = artifact.model_revision
        row.dimension = artifact.dimension
        row.license = artifact.license
        row.entry_count = artifact.entry_count
        row.byte_size = artifact.byte_size
        row.index_hash = artifact.index_hash
        row.artifact_json = artifact.artifact_json
        row.status = self._materialized_status(project_id, row.source_hash)
        self.session.flush()
        self.session.commit()

        view = self.status(project_id)
        view["rebuilt"] = artifact.index_hash != previous
        view["previousIndexHash"] = previous
        return view

    def status(self, project_id: str) -> dict[str, object]:
        row = self.index_row(project_id)
        if row is None:
            return {
                "projectId": project_id,
                "configured": False,
                "status": STATUS_MISSING,
                "dimension": None,
                "modelId": None,
                "modelRevision": None,
                "license": None,
                "entryCount": 0,
                "byteSize": 0,
                "sourceHash": None,
                "indexHash": None,
                "dataSource": None,
                "stale": False,
                "disabled": True,
                "retrievalEnabled": False,
                "reason": INDEX_MISSING,
                "updatedAt": None,
            }

        current = project_memory_snapshot_hash(self.session, project_id)
        if row.status == STATUS_FAILED:
            state, reason = STATUS_FAILED, INDEX_INVALID
        elif row.source_hash != current:
            state, reason = STATUS_STALE, INDEX_STALE
        else:
            state = STATUS_READY
            reason = INDEX_DISABLED if row.disabled else RETRIEVAL_OK
        return {
            "projectId": project_id,
            "configured": True,
            "status": state,
            "dimension": row.dimension,
            "modelId": row.embedding_model_id,
            "modelRevision": row.embedding_model_revision,
            "license": row.license,
            "entryCount": row.entry_count,
            "byteSize": row.byte_size,
            "sourceHash": row.source_hash,
            "indexHash": row.index_hash,
            "dataSource": row.data_source,
            "stale": state == STATUS_STALE,
            "disabled": bool(row.disabled),
            "retrievalEnabled": state == STATUS_READY and not row.disabled,
            "reason": reason,
            "updatedAt": row.updated_at.isoformat() if row.updated_at is not None else None,
        }

    def retrieve(
        self,
        project_id: str,
        *,
        query: VectorQuery | None,
        ordinal: int | None = None,
    ) -> VectorRetrieval:
        """Cosine top-K of the project's index, or a reasoned fallback.

        The caller supplies the query embedding. Without a matching one (or with
        no usable index at all) the result is used=False and the structured
        selection stays untouched.
        """
        row = self.index_row(project_id)
        if row is None:
            return VectorRetrieval(used=False, reason=INDEX_MISSING)

        # The four "located" fields are passed explicitly at every return so the
        # keyword arguments stay type-checked: unpacking a dict[str, object] would
        # erase the VectorRetrieval field contract (mypy **dict[str, object]).
        index_id = row.id
        index_hash = row.index_hash
        dimension = row.dimension
        model_id = row.embedding_model_id
        if row.status == STATUS_FAILED:
            return VectorRetrieval(
                used=False,
                reason=INDEX_INVALID,
                index_id=index_id,
                index_hash=index_hash,
                dimension=dimension,
                model_id=model_id,
            )
        if row.disabled:
            return VectorRetrieval(
                used=False,
                reason=INDEX_DISABLED,
                index_id=index_id,
                index_hash=index_hash,
                dimension=dimension,
                model_id=model_id,
            )
        if row.source_hash != project_memory_snapshot_hash(self.session, project_id):
            return VectorRetrieval(
                used=False,
                reason=INDEX_STALE,
                index_id=index_id,
                index_hash=index_hash,
                dimension=dimension,
                model_id=model_id,
            )
        if query is None:
            return VectorRetrieval(
                used=False,
                reason=QUERY_MISSING,
                index_id=index_id,
                index_hash=index_hash,
                dimension=dimension,
                model_id=model_id,
            )
        query_problem = _query_problem(row, query)
        if query_problem is not None:
            return VectorRetrieval(
                used=False,
                reason=query_problem,
                index_id=index_id,
                index_hash=index_hash,
                dimension=dimension,
                model_id=model_id,
            )

        candidates, excluded = self._eligible_entries(row, ordinal)
        best: dict[str, float] = {}
        for entry in sorted(candidates, key=lambda item: item.ref_id):
            score = cosine_similarity(entry.embedding, query.embedding)
            if entry.ref_id not in best or score > best[entry.ref_id]:
                best[entry.ref_id] = score
        ordered = sorted(best.items(), key=lambda item: (-item[1], item[0]))
        selected = tuple(ordered[: self.limits.top_k])
        for ref_id, _score in ordered[self.limits.top_k :]:
            excluded.append((ref_id, NOT_TOP_K))
        return VectorRetrieval(
            used=True,
            reason=RETRIEVAL_OK,
            selected=tuple(ref_id for ref_id, _score in selected),
            ranked=selected,
            excluded=tuple(excluded),
            index_id=index_id,
            index_hash=index_hash,
            dimension=dimension,
            model_id=model_id,
        )

    def index_row(self, project_id: str) -> MemoryIndexRow | None:
        return self.session.scalar(select(MemoryIndexRow).where(MemoryIndexRow.project_id == project_id))

    def _materialized_status(self, project_id: str, source_hash: str) -> str:
        current = project_memory_snapshot_hash(self.session, project_id)
        return STATUS_READY if source_hash == current else STATUS_STALE

    def _eligible_entries(
        self, row: MemoryIndexRow, ordinal: int | None
    ) -> tuple[list[EmbeddingEntry], list[tuple[str, str]]]:
        """Entries that may take part in retrieval, plus why the others may not.

        Cross-project references, memory that was never approved, memory whose
        evidence run was superseded and (when an ordinal is given) memory outside
        that chapter's range are all dropped *before* ranking.
        """
        entries = stored_entries(row)
        excluded: list[tuple[str, str]] = []
        if not entries:
            return [], excluded

        memory_rows = {
            memory.id: memory
            for memory in self.session.scalars(
                select(StoryMemoryRow).where(
                    StoryMemoryRow.project_id == row.project_id,
                    StoryMemoryRow.id.in_([entry.ref_id for entry in entries]),
                )
            ).all()
        }
        run_ids = {memory.source_run_id for memory in memory_rows.values() if memory.source_run_id}
        approved_runs = (
            set(
                self.session.scalars(
                    select(TranslationRun.id).where(
                        TranslationRun.id.in_(run_ids),
                        TranslationRun.status == RunStatus.APPROVED.value,
                    )
                ).all()
            )
            if run_ids
            else set()
        )

        candidates: list[EmbeddingEntry] = []
        for entry in sorted(entries, key=lambda item: item.id):
            memory = memory_rows.get(entry.ref_id)
            if memory is None:
                excluded.append((entry.ref_id, REF_NOT_IN_PROJECT))
                continue
            if memory.status != STATUS_APPROVED:
                excluded.append((entry.ref_id, MEMORY_NOT_APPROVED))
                continue
            if memory.source_run_id is not None and memory.source_run_id not in approved_runs:
                excluded.append((entry.ref_id, EVIDENCE_NOT_APPROVED))
                continue
            if ordinal is not None and not _covers_ordinal(memory, ordinal):
                excluded.append((entry.ref_id, OUT_OF_RANGE))
                continue
            candidates.append(entry)
        return candidates, excluded


def _query_problem(row: MemoryIndexRow, query: VectorQuery) -> str | None:
    """None when the query embedding matches the index; otherwise a reason code."""
    if not isinstance(query.embedding, Sequence) or len(query.embedding) == 0:
        return QUERY_MISMATCH
    if query.dimension is not None and query.dimension != len(query.embedding):
        return QUERY_MISMATCH
    if query.model_id != row.embedding_model_id:
        return QUERY_MISMATCH
    if query.model_revision is not None and row.embedding_model_revision not in (None, query.model_revision):
        return QUERY_MISMATCH
    if len(query.embedding) != row.dimension:
        return QUERY_MISMATCH
    values = tuple(float(value) for value in query.embedding)
    if not all(math.isfinite(value) for value in values):
        return VALUE_INVALID
    if not any(values):
        return VALUE_INVALID
    return None


def _covers_ordinal(memory: StoryMemoryRow, ordinal: int) -> bool:
    if memory.valid_from_ordinal > ordinal:
        return False
    return memory.valid_to_ordinal is None or memory.valid_to_ordinal >= ordinal
