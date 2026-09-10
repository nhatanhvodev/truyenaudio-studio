"""Thống kê mẫu đo cho harness benchmark (V01).

Percentile dùng nội suy tuyến tính giữa hai mẫu kề (method `linear` của NumPy) để
kết quả ổn định, không phụ thuộc thứ tự mẫu.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

__all__ = ["percentile", "summarize"]


def percentile(samples: Sequence[float], quantile: float) -> float:
    ordered = sorted(float(sample) for sample in samples)
    if not ordered:
        raise ValueError("cần ít nhất một mẫu để tính percentile")
    if not 0.0 <= quantile <= 1.0:
        raise ValueError("quantile phải nằm trong [0, 1]")
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[int(position)]
    weight = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * weight


def summarize(samples: Sequence[float], unit: str, *, digits: int = 3) -> dict[str, object]:
    values = [float(sample) for sample in samples]
    if not values:
        raise ValueError("cần ít nhất một mẫu để tổng hợp")
    return {
        "p50": round(percentile(values, 0.5), digits),
        "p95": round(percentile(values, 0.95), digits),
        "p99": round(percentile(values, 0.99), digits),
        "unit": unit,
        "samples": len(values),
        "min": round(min(values), digits),
        "max": round(max(values), digits),
        "mean": round(sum(values) / len(values), digits),
    }
