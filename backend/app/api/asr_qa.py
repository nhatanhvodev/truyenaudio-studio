from __future__ import annotations

from dataclasses import asdict
from enum import Enum

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from app.modules.audio.asr_qa import AsrQaService


class CompareAsrRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    expected_text: str = Field(alias="expectedText")
    transcript: str
    speech_segment_id: str | None = Field(default=None, alias="speechSegmentId")


def create_asr_qa_router() -> APIRouter:
    router = APIRouter(prefix="/api/asr-qa")

    @router.post("/compare")
    def compare(request: CompareAsrRequest) -> dict[str, object]:
        issues = AsrQaService().compare(
            request.expected_text,
            request.transcript,
            speech_segment_id=request.speech_segment_id,
        )
        return {"issues": [_camelize(_convert(asdict(issue))) for issue in issues]}

    return router


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
    parts = value.split("_")
    return parts[0] + "".join(part[:1].upper() + part[1:] for part in parts[1:])
