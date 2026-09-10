"""Harness benchmark offline cho Truyện Audio Studio (task V01).

Gói này chứa CLI `run.py` cùng các fixture/tiện ích đo hiệu năng. Nguyên tắc:

- dựng dữ liệu bằng chính ORM/migration của app (`alembic upgrade head`) trên một
  data root TẠM (tempdir) — không bao giờ mở data root thật của studio;
- chạy offline: `run.py` cài network guard chặn mọi kết nối ra ngoài loopback;
- mọi fixture có seed + fixtureHash để tái lập, không gọi cloud, không tải model.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"


def ensure_importable() -> None:
    """Đưa repo root và backend vào sys.path để CLI chạy trực tiếp vẫn import được `app.*`."""

    for candidate in (BACKEND_DIR, REPO_ROOT):
        text = str(candidate)
        if text not in sys.path:
            sys.path.insert(0, text)


ensure_importable()

__all__ = ["REPO_ROOT", "BACKEND_DIR", "ensure_importable"]
