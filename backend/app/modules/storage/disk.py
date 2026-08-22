from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import shutil


GIB = 1024**3
WARNING_USED_BYTES = 15 * GIB
HARD_USED_BYTES = 20 * GIB
MIN_FREE_AFTER_CREATE_BYTES = 5 * GIB


@dataclass(frozen=True)
class DiskDecision:
    allowed: bool
    level: str
    used_bytes: int
    free_bytes: int
    estimated_bytes: int
    reasons: tuple[str, ...]


class DiskGuard:
    def __init__(
        self,
        root: Path | str,
        *,
        usage_provider: Callable[[Path], object] | None = None,
    ) -> None:
        self.root = Path(root)
        self.usage_provider = usage_provider or shutil.disk_usage

    def can_create(self, estimated_bytes: int) -> DiskDecision:
        if estimated_bytes < 0:
            raise ValueError("estimated_bytes must be non-negative")

        usage = self.usage_provider(self.root)
        used_bytes = int(usage.used)
        free_bytes = int(usage.free)
        reasons: list[str] = []
        hard = False

        if used_bytes + estimated_bytes >= HARD_USED_BYTES:
            hard = True
            reasons.append("DISK_USED_HARD_LIMIT")
        if free_bytes - estimated_bytes < MIN_FREE_AFTER_CREATE_BYTES:
            hard = True
            reasons.append("DISK_FREE_HARD_LIMIT")
        if hard:
            return DiskDecision(False, "hard", used_bytes, free_bytes, estimated_bytes, tuple(reasons))

        if used_bytes >= WARNING_USED_BYTES:
            reasons.append("DISK_USED_WARNING")
            return DiskDecision(True, "warning", used_bytes, free_bytes, estimated_bytes, tuple(reasons))

        return DiskDecision(True, "ok", used_bytes, free_bytes, estimated_bytes, ())
