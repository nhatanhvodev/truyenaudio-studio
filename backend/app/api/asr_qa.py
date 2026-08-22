from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from app.api.payload import camel_payload
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
        return {"issues": [camel_payload(issue) for issue in issues]}

    return router
