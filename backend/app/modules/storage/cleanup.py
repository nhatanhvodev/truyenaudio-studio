from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.contracts import new_id
from app.modules.artifacts.store import ArtifactStore, UnsafeArtifactPath


PARTIAL_AGE = timedelta(hours=24)
WAV_AGE = timedelta(days=7)
DIAGNOSTIC_LOG_RETENTION = timedelta(days=30)
PROTECTED_KINDS = {
    "SOURCE_SNAPSHOT",
    "TRANSLATION_MARKDOWN",
    "RIGHTS_EVIDENCE",
    "LICENSE_SNAPSHOT",
    "CONSENT_EVIDENCE",
    "PUBLICATION_BUNDLE",
    "PRIVATE_ARCHIVE",
}
SUPERSEDED_CACHE_KINDS = {"TTS_SEGMENT", "VOICE_PREVIEW"}


class CleanupPlanStale(RuntimeError):
    pass


@dataclass(frozen=True)
class CleanupCandidate:
    candidate_type: str
    relative_path: str
    byte_size: int
    sha256: str
    artifact_id: str | None = None


@dataclass(frozen=True)
class CleanupPlan:
    plan_id: str
    snapshot_hash: str
    candidates: tuple[CleanupCandidate, ...]
    total_bytes: int


@dataclass(frozen=True)
class CleanupResult:
    plan_id: str
    deleted_count: int
    skipped_count: int
    freed_bytes: int
    deleted_paths: tuple[str, ...]
    skipped_paths: tuple[str, ...]


class CleanupService:
    def __init__(
        self,
        session: Session,
        artifact_store: ArtifactStore,
        *,
        clock=lambda: datetime.now(UTC),
        plan_store: dict[str, CleanupPlan] | None = None,
    ) -> None:
        self.session = session
        self.artifact_store = artifact_store
        self.clock = clock
        self._plans: dict[str, CleanupPlan] = plan_store if plan_store is not None else {}

    def preview(self) -> CleanupPlan:
        candidates = sorted(
            [*self._artifact_candidates(), *self._partial_candidates(), *self._diagnostic_log_candidates()],
            key=lambda candidate: (candidate.relative_path, candidate.candidate_type),
        )
        snapshot_hash = _snapshot_hash(candidates)
        plan = CleanupPlan(
            plan_id=snapshot_hash[:24],
            snapshot_hash=snapshot_hash,
            candidates=tuple(candidates),
            total_bytes=sum(candidate.byte_size for candidate in candidates),
        )
        self._plans[plan.plan_id] = plan
        return plan

    def execute(self, plan_id: str, snapshot_hash: str) -> CleanupResult:
        plan = self._plans.get(plan_id)
        if plan is None or plan.snapshot_hash != snapshot_hash:
            raise CleanupPlanStale("cleanup plan snapshot does not match current review token")

        deleted_paths: list[str] = []
        skipped_paths: list[str] = []
        freed_bytes = 0
        for candidate in plan.candidates:
            if not self._candidate_still_deletable(candidate):
                skipped_paths.append(candidate.relative_path)
                continue
            path = self.artifact_store.resolve(candidate.relative_path)
            size = self._delete_with_durable_audit(candidate, path)
            freed_bytes += size
            deleted_paths.append(candidate.relative_path)
        return CleanupResult(
            plan_id,
            len(deleted_paths),
            len(skipped_paths),
            freed_bytes,
            tuple(deleted_paths),
            tuple(skipped_paths),
        )

    def _delete_with_durable_audit(self, candidate: CleanupCandidate, path: Path) -> int:
        size = path.stat().st_size
        quarantine_path = self._quarantine_path(path)
        path.replace(quarantine_path)
        try:
            self._audit_delete(candidate, size)
            if candidate.artifact_id is not None:
                self.session.execute(
                    text(
                        """
                        UPDATE artifacts
                        SET status = 'DELETED', updated_at = :updated_at
                        WHERE id = :id
                        """
                    ),
                    {"id": candidate.artifact_id, "updated_at": self._now_iso()},
                )
            self.session.commit()
        except Exception:
            self.session.rollback()
            if quarantine_path.exists() and not path.exists():
                quarantine_path.replace(path)
            raise

        quarantine_path.unlink(missing_ok=True)
        return size

    def _quarantine_path(self, path: Path) -> Path:
        return path.with_name(f".{path.name}.{uuid4().hex}.cleanup-delete")

    def _artifact_candidates(self) -> list[CleanupCandidate]:
        approved_master_ids = self._approved_master_ids()
        approved_mp3_chapter_ids = self._approved_mp3_chapter_ids()
        rows = self.session.execute(
            text(
                """
                SELECT id, chapter_id, kind, status, relative_path, sha256, byte_size, created_at, updated_at
                FROM artifacts
                WHERE status != 'DELETED'
                """
            )
        ).mappings()
        candidates: list[CleanupCandidate] = []
        for row in rows:
            if self._is_protected_artifact(row, approved_master_ids):
                continue
            candidate_type = self._candidate_type_for_artifact(row, approved_mp3_chapter_ids)
            if candidate_type is None:
                continue
            try:
                path = self.artifact_store.resolve(str(row["relative_path"]))
            except UnsafeArtifactPath:
                continue
            if not path.is_file():
                continue
            actual_sha256, byte_size = _sha256_file(path)
            if actual_sha256 != row["sha256"]:
                continue
            candidates.append(
                CleanupCandidate(
                    candidate_type=candidate_type,
                    relative_path=str(row["relative_path"]),
                    byte_size=byte_size,
                    sha256=actual_sha256,
                    artifact_id=str(row["id"]),
                )
            )
        return candidates

    def _partial_candidates(self) -> list[CleanupCandidate]:
        now = self.clock()
        candidates: list[CleanupCandidate] = []
        for path in self.artifact_store.resolve().rglob("*.partial"):
            if not path.is_file():
                continue
            age = now - datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
            if age <= PARTIAL_AGE:
                continue
            relative_path = path.relative_to(self.artifact_store.resolve()).as_posix()
            sha256, byte_size = _sha256_file(path)
            candidates.append(CleanupCandidate("partial", relative_path, byte_size, sha256))
        return candidates

    def _diagnostic_log_candidates(self) -> list[CleanupCandidate]:
        now = self.clock()
        log_root = self.artifact_store.resolve("diagnostics/logs")
        if not log_root.is_dir():
            return []
        candidates: list[CleanupCandidate] = []
        for path in log_root.glob("diagnostics-*.jsonl"):
            if not path.is_file():
                continue
            age = now - datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
            if age <= DIAGNOSTIC_LOG_RETENTION:
                continue
            relative_path = path.relative_to(self.artifact_store.resolve()).as_posix()
            sha256, byte_size = _sha256_file(path)
            candidates.append(CleanupCandidate("diagnostic_log", relative_path, byte_size, sha256))
        return candidates

    def _candidate_type_for_artifact(self, row, approved_mp3_chapter_ids: set[str]) -> str | None:
        kind = str(row["kind"])
        status = str(row["status"])
        if status == "SUPERSEDED" and kind in SUPERSEDED_CACHE_KINDS:
            return "superseded_cache"
        if kind == "VOICE_PREVIEW" and status == "READY":
            return "unapproved_preview"
        if (
            kind == "MASTER_WAV"
            and status == "READY"
            and row["chapter_id"] in approved_mp3_chapter_ids
            and self.clock() - _parse_db_datetime(row["created_at"]) > WAV_AGE
        ):
            return "wav_with_approved_mp3"
        return None

    def _candidate_still_deletable(self, candidate: CleanupCandidate) -> bool:
        try:
            path = self.artifact_store.resolve(candidate.relative_path)
        except UnsafeArtifactPath:
            return False
        if not path.is_file():
            return False
        actual_sha256, byte_size = _sha256_file(path)
        if actual_sha256 != candidate.sha256 or byte_size != candidate.byte_size:
            return False
        if candidate.artifact_id is None:
            return candidate.candidate_type in {"partial", "diagnostic_log"}
        row = self.session.execute(
            text(
                """
                SELECT id, chapter_id, kind, status, relative_path, sha256, byte_size, created_at, updated_at
                FROM artifacts
                WHERE id = :id
                """
            ),
            {"id": candidate.artifact_id},
        ).mappings().one_or_none()
        if row is None or row["relative_path"] != candidate.relative_path:
            return False
        if self._is_protected_artifact(row, self._approved_master_ids()):
            return False
        expected_type = self._candidate_type_for_artifact(row, self._approved_mp3_chapter_ids())
        return expected_type == candidate.candidate_type and row["sha256"] == candidate.sha256

    def _is_protected_artifact(self, row, approved_master_ids: set[str]) -> bool:
        return str(row["kind"]) in PROTECTED_KINDS or str(row["id"]) in approved_master_ids

    def _approved_master_ids(self) -> set[str]:
        rows = self.session.execute(
            text("SELECT approved_master_artifact_id FROM chapters WHERE approved_master_artifact_id IS NOT NULL")
        )
        return {str(row[0]) for row in rows}

    def _approved_mp3_chapter_ids(self) -> set[str]:
        rows = self.session.execute(
            text(
                """
                SELECT chapters.id
                FROM chapters
                JOIN artifacts ON artifacts.id = chapters.approved_master_artifact_id
                WHERE artifacts.kind = 'MASTER_MP3' AND artifacts.status = 'READY'
                """
            )
        )
        return {str(row[0]) for row in rows}

    def _audit_delete(self, candidate: CleanupCandidate, size: int) -> None:
        details = json.dumps(
            {
                "candidate_type": candidate.candidate_type,
                "relative_path": candidate.relative_path,
                "byte_size": size,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        self.session.execute(
            text(
                """
                INSERT INTO audit_events
                (id, actor, action, entity_type, entity_id, before_hash, after_hash, redacted_details, created_at)
                VALUES
                (:id, 'LOCAL_OWNER', 'STORAGE_CLEANUP_DELETE', :entity_type, :entity_id,
                 :before_hash, NULL, :details, :created_at)
                """
            ),
            {
                "id": new_id(),
                "entity_type": "artifact" if candidate.artifact_id is not None else "partial_file",
                "entity_id": candidate.artifact_id or candidate.relative_path,
                "before_hash": candidate.sha256,
                "details": details,
                "created_at": self._now_iso(),
            },
        )

    def _now_iso(self) -> str:
        return self.clock().astimezone(UTC).isoformat()


def _snapshot_hash(candidates: list[CleanupCandidate]) -> str:
    payload = [asdict(candidate) for candidate in candidates]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _parse_db_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(UTC)
    return datetime.fromisoformat(str(value)).astimezone(UTC)


def _sha256_file(path: Path) -> tuple[str, int]:
    sha256 = hashlib.sha256()
    byte_size = 0
    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            byte_size += len(chunk)
            sha256.update(chunk)
    return sha256.hexdigest(), byte_size
