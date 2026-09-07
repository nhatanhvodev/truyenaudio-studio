from __future__ import annotations

from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from app.main import create_app
from app.settings.config import Settings
from app.modules.sources.archive_guard import ImportCandidate

LOOPBACK_ORIGIN = "http://127.0.0.1:8765"


def test_wenku_info_and_rankings_api(settings: Settings) -> None:
    client = TestClient(create_app(settings=settings, acquire_lock=False), base_url=LOOPBACK_ORIGIN)

    mock_info = {
        "bookId": "1059978960",
        "title": "Mock Title",
        "author": "Mock Author",
        "category": "武侠",
        "status": "连载",
        "description": "Mock description",
        "coverUrl": "https://example.com/cover.jpg",
        "latestChapter": "第10章",
        "wordCountText": "10万字",
        "totalChapters": 10,
        "catalog": [{"ordinal": 1, "title": "Chương 1", "url": "https://example.com/1", "isAppFree": False}],
        "sourceUrl": "https://wenku.read.qq.com/detail/1059978960",
    }

    mock_rankings = [
        {"bookId": "1059978960", "title": "Rank Book 1", "url": "https://example.com/1059978960"}
    ]

    mock_candidates = [
        ImportCandidate(
            ordinal=1,
            title="第1章 牢房",
            text="Nội dung chương 1.",
            source_path="wenku://1059978960/1",
            warnings=(),
        )
    ]

    with patch("app.api.wenku.WenkuService.get_book_info", return_value=mock_info), \
         patch("app.api.wenku.WenkuService.get_rankings", return_value=mock_rankings), \
         patch("app.api.wenku.WenkuService.crawl_candidates", return_value=mock_candidates):

        # 1. Info endpoint
        r_info = client.get("/api/wenku/info", params={"urlOrId": "1059978960"})
        assert r_info.status_code == 200
        assert r_info.json()["title"] == "Mock Title"
        assert r_info.json()["totalChapters"] == 10

        # 2. Rankings endpoint
        r_rank = client.get("/api/wenku/rankings", params={"type": "hot"})
        assert r_rank.status_code == 200
        assert len(r_rank.json()["rankings"]) == 1

        # 3. Preview endpoint (POST requires CSRF token)
        csrf = client.get("/api/security/bootstrap").json()["csrfToken"]
        r_prev = client.post(
            "/api/wenku/preview",
            json={"url_or_id": "1059978960", "start_chapter": 1, "end_chapter": 1},
            headers={"Origin": LOOPBACK_ORIGIN, "X-CSRF-Token": csrf},
        )
        assert r_prev.status_code == 200
        assert len(r_prev.json()["candidates"]) == 1
        assert r_prev.json()["candidates"][0]["title"] == "第1章 牢房"
