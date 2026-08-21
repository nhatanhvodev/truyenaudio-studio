from __future__ import annotations

import hmac
import secrets

from fastapi import Request


LOOPBACK_ORIGIN = "http://127.0.0.1:8765"
LOOPBACK_HOST = "127.0.0.1:8765"
STATE_CHANGING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class CsrfService:
    def __init__(self) -> None:
        self._token = secrets.token_urlsafe(32)

    @property
    def token(self) -> str:
        return self._token

    def allows_bootstrap(self, request: Request) -> bool:
        host = request.headers.get("host", "")
        origin = request.headers.get("origin")
        return host == LOOPBACK_HOST and origin in {None, LOOPBACK_ORIGIN}

    def validates_state_change(self, request: Request) -> bool:
        origin = request.headers.get("origin")
        supplied = request.headers.get("x-csrf-token")
        if origin != LOOPBACK_ORIGIN or supplied is None:
            return False
        return hmac.compare_digest(supplied, self._token)
