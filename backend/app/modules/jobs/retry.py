from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


MAX_ATTEMPTS = 3
RETRY_DELAYS_SECONDS = (2, 10, 30)
MAX_RETRY_AFTER_SECONDS = 600


class RetryDisposition(StrEnum):
    RETRY = "RETRY"
    FAIL = "FAIL"


@dataclass(frozen=True)
class RetryDecision:
    disposition: RetryDisposition
    delay_seconds: int | None


RETRYABLE_CODES = {"PROVIDER_NETWORK", "PROVIDER_5XX", "DB_BUSY", "PROVIDER_RATE_LIMIT"}


def classify_retry(
    error_code: str,
    *,
    attempt_no: int,
    retry_after_seconds: int | None = None,
) -> RetryDecision:
    if attempt_no > MAX_ATTEMPTS or error_code not in RETRYABLE_CODES:
        return RetryDecision(RetryDisposition.FAIL, None)

    if error_code == "PROVIDER_RATE_LIMIT":
        delay = retry_after_seconds if retry_after_seconds is not None else RETRY_DELAYS_SECONDS[attempt_no - 1]
        return RetryDecision(RetryDisposition.RETRY, min(delay, MAX_RETRY_AFTER_SECONDS))

    return RetryDecision(RetryDisposition.RETRY, RETRY_DELAYS_SECONDS[attempt_no - 1])
