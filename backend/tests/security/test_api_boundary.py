from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app
from app.settings.config import Settings


LOOPBACK = "http://127.0.0.1:8765"


def test_api_rejects_noncanonical_host_and_unknown_or_encoded_paths(settings) -> None:
    with TestClient(create_app(settings=settings, acquire_lock=False), base_url=LOOPBACK) as client:
        assert client.get("/api/health/ready", headers={"Host": "localhost:8765"}).status_code == 403
        assert client.get("/api/health/ready", headers={"Host": "127.0.0.1:8766"}).status_code == 403
        assert client.get("/api/health/ready", headers={"Host": "[::1]:8765"}).status_code == 403
        assert client.get("/api/not-a-real-route").status_code == 404
        assert client.get("/api/%2e%2e/security/bootstrap").status_code == 404
        assert client.get("/api/health/ready?apiKey=query-secret").status_code == 404


def test_api_state_changes_require_exact_host_origin_and_csrf(settings) -> None:
    with TestClient(create_app(settings=settings, acquire_lock=False), base_url=LOOPBACK) as client:
        token = client.get("/api/security/bootstrap").json()["csrfToken"]
        assert client.post("/api/projects", json={}, headers={"Origin": LOOPBACK, "X-CSRF-Token": token, "Host": "localhost:8765"}).status_code == 403
        assert client.post("/api/projects", json={}, headers={"Origin": "http://localhost:8765", "X-CSRF-Token": token}).status_code == 403
        assert client.post("/api/projects", json={}, headers={"Origin": LOOPBACK}).status_code == 403


def test_inference_requires_profile_and_rejects_raw_secret_fields(monkeypatch, tmp_path) -> None:
    captured: list[tuple[str, str, str | None]] = []

    class CapturingWorkflow:
        def __enter__(self):
            raise ValueError("CAPTURED")

        def __exit__(self, *args):
            return None

    def capture(_settings, _chapter_id, profile_id, model_preference=None):
        captured.append((_chapter_id, profile_id, model_preference))
        return CapturingWorkflow()

    import app.api.translation as translation

    monkeypatch.setattr(translation, "_gemini_workflow", capture)
    app = create_app(settings=Settings(data_root=tmp_path), acquire_lock=False)
    with TestClient(app, base_url=LOOPBACK) as client:
        token = client.get("/api/security/bootstrap").json()["csrfToken"]
        headers = {"Origin": LOOPBACK, "X-CSRF-Token": token}
        raw_secret = client.post(
            "/api/chapters/chapter-1/translation/gemini",
            json={"profileId": "profile-1", "cloudConsentId": "consent-1", "budgetAuthorizationId": "budget-1", "apiKey": "must-never-be-accepted"},
            headers=headers,
        )
        missing_profile = client.post(
            "/api/chapters/chapter-1/translation/gemini",
            json={"cloudConsentId": "consent-1", "budgetAuthorizationId": "budget-1"},
            headers=headers,
        )
        selected = client.post(
            "/api/chapters/chapter-1/translation/gemini",
            json={"profileId": "profile-1", "cloudConsentId": "consent-1", "budgetAuthorizationId": "budget-1", "model": "gemini-2.5-flash"},
            headers=headers,
        )

    assert raw_secret.status_code == 422
    assert "must-never-be-accepted" not in raw_secret.text
    assert missing_profile.status_code == 422
    assert selected.status_code == 400
    assert captured == [("chapter-1", "profile-1", "gemini-2.5-flash")]
