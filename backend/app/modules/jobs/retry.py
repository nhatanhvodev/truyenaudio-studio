from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


MAX_RETRYABLE_ATTEMPT = 3
RETRY_DELAYS_SECONDS = (2, 10, 30)
MAX_RETRY_AFTER_SECONDS = 600


class RetryDisposition(StrEnum):
    RETRY = "RETRY"
    FAIL = "FAIL"


@dataclass(frozen=True)
class RetryDecision:
    disposition: RetryDisposition
    delay_seconds: int | None


RETRYABLE_CODES = {"PROVIDER_NETWORK", "PROVIDER_5XX", "DB_BUSY"}


def classify_retry(
    error_code: str,
    *,
    attempt_no: int,
    retry_after_seconds: int | None = None,
) -> RetryDecision:
    if attempt_no <= 0 or attempt_no > MAX_RETRYABLE_ATTEMPT:
        return RetryDecision(RetryDisposition.FAIL, None)

    if error_code == "PROVIDER_RATE_LIMIT":
        if retry_after_seconds is None or retry_after_seconds < 0:
            return RetryDecision(RetryDisposition.FAIL, None)
        delay = retry_after_seconds
        return RetryDecision(RetryDisposition.RETRY, min(delay, MAX_RETRY_AFTER_SECONDS))

    if error_code not in RETRYABLE_CODES:
        return RetryDecision(RetryDisposition.FAIL, None)

    return RetryDecision(RetryDisposition.RETRY, RETRY_DELAYS_SECONDS[attempt_no - 1])
