from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app


LOOPBACK_ORIGIN = "http://127.0.0.1:8765"


def test_state_change_requires_exact_origin_and_csrf(settings) -> None:
    with TestClient(
        create_app(settings=settings, acquire_lock=False),
        base_url=LOOPBACK_ORIGIN,
    ) as client:
        token = client.get("/api/security/bootstrap").json()["csrfToken"]

        evil_origin = client.post(
            "/api/projects",
            json={},
            headers={"Origin": "http://evil.local", "X-CSRF-Token": token},
        )
        missing_token = client.post(
            "/api/projects",
            json={},
            headers={"Origin": LOOPBACK_ORIGIN},
        )
        valid_request = client.post(
            "/api/projects",
            json={},
            headers={"Origin": LOOPBACK_ORIGIN, "X-CSRF-Token": token},
        )

    assert evil_origin.status_code == 403
    assert missing_token.status_code == 403
    assert valid_request.status_code == 422
