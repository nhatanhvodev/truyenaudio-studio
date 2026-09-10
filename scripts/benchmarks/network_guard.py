"""Guard chặn kết nối mạng ngoài loopback cho benchmark offline (V01).

Fixture benchmark phải chứng minh được là offline: không cloud trả phí, không tải
model, không DNS. `install_network_guard()` được gọi ngay khi CLI khởi động, trước
khi dựng fixture, và mọi ý định kết nối ra ngoài loopback bị chặn kèm thông báo
tiếng Việt. Số lần bị chặn được ghi vào report; nếu lớn hơn 0 thì CLI thoát khác 0.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from ipaddress import ip_address
import socket
import threading
from typing import Any

__all__ = [
    "BlockedAttempt",
    "NetworkBlockedError",
    "NetworkGuard",
    "install_network_guard",
    "is_loopback_address",
]

_LOOPBACK_HOSTNAMES = frozenset({"localhost", "localhost.localdomain", "ip6-localhost", "::1"})


class NetworkBlockedError(RuntimeError):
    """Fixture cố truy cập mạng ngoài loopback — benchmark phải chạy offline."""


def is_loopback_address(address: object) -> bool:
    """True nếu địa chỉ đích nằm trong loopback (127.0.0.0/8, ::1, localhost)."""

    host: object
    if isinstance(address, tuple):
        if not address:
            return False
        host = address[0]
    elif isinstance(address, (bytes, bytearray)):
        return False
    else:
        # Chuỗi trần là đường dẫn AF_UNIX, không phải loopback.
        return False

    if isinstance(host, (bytes, bytearray)):
        host = bytes(host).decode("utf-8", "replace")
    if not isinstance(host, str):
        return False

    normalized = host.strip().strip("[]").lower()
    if normalized in _LOOPBACK_HOSTNAMES:
        return True
    try:
        return ip_address(normalized).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True)
class BlockedAttempt:
    kind: str
    target: str
    at: str


@dataclass
class NetworkGuard:
    """Bản cài guard; giữ hàm gốc để gỡ được sau khi đo xong."""

    attempts: list[BlockedAttempt] = field(default_factory=list)
    _installed: bool = field(default=False, repr=False)
    _saved: dict[str, Any] = field(default_factory=dict, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def install(self) -> NetworkGuard:
        if self._installed:
            return self
        self._saved = {
            "connect": socket.socket.connect,
            "connect_ex": socket.socket.connect_ex,
            "create_connection": socket.create_connection,
            "getaddrinfo": socket.getaddrinfo,
        }
        original_connect = self._saved["connect"]
        original_connect_ex = self._saved["connect_ex"]
        original_create_connection = self._saved["create_connection"]
        original_getaddrinfo = self._saved["getaddrinfo"]

        def guarded_connect(sock: socket.socket, address: object) -> Any:
            self._check("connect", address)
            return original_connect(sock, address)

        def guarded_connect_ex(sock: socket.socket, address: object) -> Any:
            self._check("connect_ex", address)
            return original_connect_ex(sock, address)

        def guarded_create_connection(address: object, *args: object, **kwargs: object) -> Any:
            self._check("create_connection", address)
            return original_create_connection(address, *args, **kwargs)

        def guarded_getaddrinfo(host: object, port: object, *args: object, **kwargs: object) -> Any:
            if not is_loopback_address((host, port)):
                self._block("getaddrinfo", f"{host}:{port}")
            return original_getaddrinfo(host, port, *args, **kwargs)

        setattr(socket.socket, "connect", guarded_connect)
        setattr(socket.socket, "connect_ex", guarded_connect_ex)
        setattr(socket, "create_connection", guarded_create_connection)
        setattr(socket, "getaddrinfo", guarded_getaddrinfo)
        self._installed = True
        return self

    def uninstall(self) -> None:
        if not self._installed:
            return
        setattr(socket.socket, "connect", self._saved["connect"])
        setattr(socket.socket, "connect_ex", self._saved["connect_ex"])
        setattr(socket, "create_connection", self._saved["create_connection"])
        setattr(socket, "getaddrinfo", self._saved["getaddrinfo"])
        self._installed = False

    @property
    def blocked_count(self) -> int:
        with self._lock:
            return len(self.attempts)

    def blocked_targets(self) -> list[str]:
        with self._lock:
            return [f"{attempt.kind}:{attempt.target}" for attempt in self.attempts]

    def as_report(self) -> dict[str, object]:
        with self._lock:
            return {
                "mode": "offline-guard",
                "blockedAttempts": len(self.attempts),
                "targets": [
                    {"kind": attempt.kind, "target": attempt.target, "at": attempt.at}
                    for attempt in self.attempts
                ],
                "note": "Chặn socket.connect/connect_ex/create_connection/getaddrinfo ngoài loopback.",
            }

    def _check(self, kind: str, address: object) -> None:
        if not is_loopback_address(address):
            self._block(kind, repr(address))

    def _block(self, kind: str, target: str) -> None:
        with self._lock:
            self.attempts.append(
                BlockedAttempt(kind=kind, target=target, at=datetime.now(UTC).isoformat().replace("+00:00", "Z"))
            )
        raise NetworkBlockedError(
            f"Benchmark chạy offline: đã chặn {kind} tới {target}. "
            "Fixture benchmark không được gọi mạng/cloud; xem lại fixture đang chạy."
        )


def install_network_guard() -> NetworkGuard:
    """Cài guard và trả về đối tượng để đọc số lần chặn/gỡ cài đặt."""

    return NetworkGuard().install()
