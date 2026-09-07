from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.modules.sources.wenku import WenkuService
from app.settings.config import Settings


class WenkuPreviewRequest(BaseModel):
    url_or_id: str
    start_chapter: int = 1
    end_chapter: int | None = None


def create_wenku_router(settings: Settings | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/wenku", tags=["wenku"])
    service = WenkuService()

    @router.get("/info")
    def get_novel_info(url_or_id: str = Query(..., alias="urlOrId")) -> dict[str, object]:
        try:
            return service.get_book_info(url_or_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"WENKU_FETCH_ERROR: {exc}") from exc

    @router.get("/rankings")
    def get_rankings(type: str = Query(default="hot")) -> dict[str, object]:
        try:
            results = service.get_rankings(type)
            return {"rankings": results}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"WENKU_RANKINGS_ERROR: {exc}") from exc

    @router.post("/preview")
    def preview_wenku_chapters(request: WenkuPreviewRequest) -> dict[str, object]:
        try:
            candidates = service.crawl_candidates(
                request.url_or_id,
                start_chapter=request.start_chapter,
                end_chapter=request.end_chapter,
            )
            return {
                "candidates": [
                    {
                        "ordinal": c.ordinal,
                        "title": c.title,
                        "text": c.text,
                        "sourcePath": c.source_path,
                        "warnings": list(c.warnings),
                    }
                    for c in candidates
                ]
            }
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"WENKU_CRAWL_ERROR: {exc}") from exc

    return router
