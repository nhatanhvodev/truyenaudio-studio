from __future__ import annotations

from collections.abc import Iterator
from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
import os
from pathlib import Path
import re
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts import ArtifactKind, ArtifactStatus
from app.db.base import create_engine_for, session_factory
from app.db.models import Artifact, Chapter
from app.modules.artifacts.store import ArtifactStore, UnsafeArtifactPath
from app.modules.speech.workflow import (
    AudioApprovalBlocked,
    AudioApprovalConflict,
    SpeechWorkflow,
    TranslationApprovalRequired,
    TtsUnavailable,
    VoicePlanRequired,
)
from app.providers.fake import FakeMp3AudioProcessor, FakeTts
from app.settings.config import Settings


RANGE_UNIT = "bytes"
ARTIFACT_CACHE_CONTROL = "private, max-age=31536000, immutable"
ARTIFACT_CHUNK_BYTES = 1024 * 1024
SINGLE_RANGE_PATTERN = re.compile(r"^(?P<start>\d*)-(?P<end>\d*)$")


@dataclass(frozen=True)
class _RangeDecision:
    """Outcome of interpreting a single Range header against the representation size."""

    kind: Literal["full", "partial", "unsatisfiable"]
    start: int = 0
    end: int = 0


class ConfigureSingleRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    preset_id: str = Field(alias="presetId")


class ApproveAudioRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    master_artifact_id: str = Field(alias="masterArtifactId")
    expected_sha256: str = Field(alias="expectedSha256")


class RenderAudioRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    cloud_consent_id: str | None = Field(default=None, alias="cloudConsentId")
    budget_authorization_id: str | None = Field(default=None, alias="budgetAuthorizationId")


def create_audio_router(settings: Settings | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/chapters/{chapter_id}/audio")
    active_settings = settings or Settings()

    def workflow_dependency() -> Iterator[SpeechWorkflow]:
        engine = create_engine_for(active_settings.data_root / "studio.sqlite3")
        factory = session_factory(engine)
        with factory() as session:
            fake_audio = os.getenv("STUDIO_FAKE_AUDIO") == "1"
            yield SpeechWorkflow(
                session,
                tts=FakeTts() if fake_audio else None,
                audio_processor=FakeMp3AudioProcessor() if fake_audio else None,
                artifact_root=active_settings.data_root / "artifacts",
                allow_fake_tts=fake_audio,
            )
        engine.dispose()

    @router.post("/configure-single")
    def configure_single(
        chapter_id: str,
        request: ConfigureSingleRequest,
        workflow: SpeechWorkflow = Depends(workflow_dependency),
    ) -> dict[str, object]:
        try:
            return _camel_payload(
                workflow.configure_single(chapter_id, request.preset_id)
            )
        except TranslationApprovalRequired as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/render")
    def render_audio(
        chapter_id: str,
        request: RenderAudioRequest | None = None,
        workflow: SpeechWorkflow = Depends(workflow_dependency),
    ) -> dict[str, object]:
        render_request = request or RenderAudioRequest()
        try:
            return _camel_payload(
                workflow.enqueue_render(
                    chapter_id,
                    cloud_consent_id=render_request.cloud_consent_id,
                    budget_authorization_id=render_request.budget_authorization_id,
                )
            )
        except VoicePlanRequired as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except TranslationApprovalRequired as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except TtsUnavailable as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/approve")
    def approve_audio(
        chapter_id: str,
        request: ApproveAudioRequest,
        workflow: SpeechWorkflow = Depends(workflow_dependency),
    ) -> dict[str, object]:
        try:
            return _camel_payload(
                workflow.approve_audio(
                    chapter_id,
                    request.master_artifact_id,
                    expected_sha256=request.expected_sha256,
                )
            )
        except AudioApprovalBlocked as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except AudioApprovalConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/status")
    def audio_status(chapter_id: str) -> dict[str, object]:
        engine = create_engine_for(active_settings.data_root / "studio.sqlite3")
        factory = session_factory(engine)
        try:
            with factory() as session:
                chapter = session.get(Chapter, chapter_id)
                if chapter is None:
                    raise HTTPException(status_code=404, detail="chapter not found")
                artifact = None
                approved = False
                if chapter.approved_master_artifact_id:
                    artifact = session.get(
                        Artifact, chapter.approved_master_artifact_id
                    )
                    approved = artifact is not None
                if artifact is None:
                    artifact = (
                        session.execute(
                            select(Artifact)
                            .where(
                                Artifact.chapter_id == chapter_id,
                                Artifact.kind == ArtifactKind.MASTER_MP3.value,
                                Artifact.status == ArtifactStatus.READY.value,
                            )
                            .order_by(Artifact.updated_at.desc(), Artifact.id.desc())
                        )
                        .scalars()
                        .first()
                    )
                return {
                    "chapterId": chapter_id,
                    "masterArtifactId": artifact.id if artifact else None,
                    "masterSha256": artifact.sha256 if artifact else None,
                    "approved": approved,
                }
        finally:
            engine.dispose()

    def artifact_session_dependency() -> Iterator[Session]:
        engine = create_engine_for(active_settings.data_root / "studio.sqlite3")
        factory = session_factory(engine)
        try:
            with factory() as session:
                yield session
        finally:
            engine.dispose()

    @router.api_route(
        "/artifacts/{artifact_id}/content",
        methods=["GET", "HEAD"],
        name="audio_artifact_content",
    )
    def artifact_content(
        chapter_id: str,
        artifact_id: str,
        request: Request,
        range_header: str | None = Header(default=None, alias="Range"),
        if_none_match: str | None = Header(default=None, alias="If-None-Match"),
        if_range: str | None = Header(default=None, alias="If-Range"),
        session: Session = Depends(artifact_session_dependency),
    ) -> Response:
        """Serve the stored bytes of one READY artifact with single-range support.

        Decisions pinned by the A05 contract:

        * The artifact must exist, belong to this chapter, be READY, and its file must exist
          under the artifact root after confinement. Anything else answers 404, never 500.
        * A multi-range request (for example bytes=0-1,4-5) is answered with the whole
          representation and status 200: multipart/byteranges is deliberately not implemented,
          so a player re-requests a single range instead of parsing a multipart body.
        * If-None-Match is evaluated before Range for both GET and HEAD, so a matching ETag
          answers 304 even when a Range header is present.
        * If-Range that does not match the stored digest makes the server ignore Range and send
          the whole file; no Last-Modified validator is exposed (the file is immutable).
        * A malformed, non-bytes, or unsatisfiable Range answers 416 with Content-Range */total.
        """
        artifact = session.get(Artifact, artifact_id)
        if (
            artifact is None
            or artifact.chapter_id != chapter_id
            or artifact.status != ArtifactStatus.READY.value
        ):
            raise HTTPException(status_code=404, detail="ARTIFACT_NOT_FOUND")

        store = ArtifactStore(active_settings.data_root / "artifacts")
        try:
            artifact_path = store.resolve(artifact.relative_path)
        except UnsafeArtifactPath as exc:
            raise HTTPException(status_code=404, detail="ARTIFACT_NOT_FOUND") from exc
        if not artifact_path.is_file():
            raise HTTPException(status_code=404, detail="ARTIFACT_NOT_FOUND")

        total = artifact_path.stat().st_size
        etag = f'"{artifact.sha256}"'
        base_headers = {
            "Accept-Ranges": RANGE_UNIT,
            "Content-Type": artifact.mime_type,
            "ETag": etag,
            "Cache-Control": ARTIFACT_CACHE_CONTROL,
        }

        if _etag_matches(if_none_match, artifact.sha256):
            return Response(
                status_code=304,
                headers={
                    "Accept-Ranges": RANGE_UNIT,
                    "ETag": etag,
                    "Cache-Control": ARTIFACT_CACHE_CONTROL,
                },
            )

        effective_range = range_header
        if range_header is not None and not _if_range_matches(if_range, artifact.sha256):
            effective_range = None
        decision = _decide_range(effective_range, total)

        if decision.kind == "unsatisfiable":
            return Response(
                status_code=416,
                headers={
                    **base_headers,
                    "Content-Range": f"bytes */{total}",
                    "Content-Length": "0",
                },
            )

        start = 0
        content_length = total
        content_range: str | None = None
        if decision.kind == "partial":
            start = decision.start
            content_length = decision.end - decision.start + 1
            content_range = f"bytes {start}-{decision.end}/{total}"
        status_code = 206 if decision.kind == "partial" else 200

        headers = {**base_headers, "Content-Length": str(content_length)}
        if content_range is not None:
            headers["Content-Range"] = content_range
        if request.method == "HEAD":
            return Response(status_code=status_code, headers=headers)
        return StreamingResponse(
            _iter_file_slice(artifact_path, start, content_length),
            status_code=status_code,
            headers=headers,
        )

    return router


def _decide_range(range_header: str | None, total: int) -> _RangeDecision:
    """Interpret a HTTP Range header for symmetric single-range serving.

    Returns a partial range, a full-representation decision (no Range, or a multi-range
    request that this server deliberately does not answer with multipart/byteranges), or an
    unsatisfiable decision that the route maps to 416.
    """
    if range_header is None or not range_header.strip():
        return _RangeDecision("full")

    unit, separator, specification = range_header.strip().partition("=")
    if not separator or unit.strip().lower() != RANGE_UNIT:
        return _RangeDecision("unsatisfiable")

    specifications = [part.strip() for part in specification.split(",")]
    if len(specifications) != 1:
        return _RangeDecision("full")

    match = SINGLE_RANGE_PATTERN.match(specifications[0])
    if match is None:
        return _RangeDecision("unsatisfiable")

    raw_start = match.group("start")
    raw_end = match.group("end")
    if not raw_start and not raw_end:
        return _RangeDecision("unsatisfiable")

    if not raw_start:
        suffix_length = int(raw_end)
        if suffix_length == 0 or total == 0:
            return _RangeDecision("unsatisfiable")
        return _RangeDecision("partial", max(0, total - suffix_length), total - 1)

    start = int(raw_start)
    if start >= total:
        return _RangeDecision("unsatisfiable")
    end = total - 1 if not raw_end else min(int(raw_end), total - 1)
    if end < start:
        return _RangeDecision("unsatisfiable")
    return _RangeDecision("partial", start, end)


def _etag_matches(if_none_match: str | None, sha256: str) -> bool:
    """Return True when an If-None-Match validator matches the stored digest."""
    if if_none_match is None:
        return False
    for candidate in if_none_match.split(","):
        value = candidate.strip()
        if value == "*":
            return True
        if _etag_value(value) == sha256:
            return True
    return False


def _if_range_matches(if_range: str | None, sha256: str) -> bool:
    """Return True when the If-Range validator still describes the stored bytes."""
    if if_range is None:
        return True
    return _etag_value(if_range) == sha256


def _etag_value(validator: str) -> str:
    """Strip weak marker and quotes so ETag and If-Range/If-None-Match compare on the digest."""
    value = validator.strip()
    if value[:2].upper() == "W/":
        value = value[2:].strip()
    if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
        value = value[1:-1]
    return value


def _iter_file_slice(path: Path, start: int, length: int) -> Iterator[bytes]:
    """Yield at most length bytes from path starting at start, in bounded chunks."""
    remaining = length
    with path.open("rb") as file:
        file.seek(start)
        while remaining > 0:
            chunk = file.read(min(ARTIFACT_CHUNK_BYTES, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


def _camel_payload(value: object) -> dict[str, object]:
    if not is_dataclass(value) or isinstance(value, type):
        raise TypeError("payload must be a dataclass instance")
    camelized = _camelize(_convert(asdict(value)))
    if not isinstance(camelized, dict):
        raise TypeError("payload must serialize to an object")
    return {str(key): item for key, item in camelized.items()}


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
