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


RETRYABLE_CODES = {"PROVIDER_NETWORK", "PROVIDER_5XX", "PROVIDER_TIMEOUT", "DB_BUSY"}


def classify_retry(
    error_code: str,
    *,
    attempt_no: int,
    retry_after_seconds: int | None = None,
) -> RetryDecision:
    if attempt_no <= 0 or attempt_no > MAX_RETRYABLE_ATTEMPT:
        return RetryDecision(RetryDisposition.FAIL, None)

    if error_code == "PROVIDER_RATE_LIMIT" or error_code == "HTTP_429":
        if error_code == "HTTP_429" and retry_after_seconds is None:
            # No Retry-After header: still retry with the shared backoff policy.
            return RetryDecision(RetryDisposition.RETRY, RETRY_DELAYS_SECONDS[attempt_no - 1])
        if retry_after_seconds is None or retry_after_seconds < 0:
            return RetryDecision(RetryDisposition.FAIL, None)
        return RetryDecision(RetryDisposition.RETRY, min(retry_after_seconds, MAX_RETRY_AFTER_SECONDS))

    if error_code == "HTTP_408" or (error_code.startswith("HTTP_5")):
        return RetryDecision(RetryDisposition.RETRY, RETRY_DELAYS_SECONDS[attempt_no - 1])

    if error_code.startswith("HTTP_"):
        # 400/401/403/404/... are definitive rejections; never loop on them.
        return RetryDecision(RetryDisposition.FAIL, None)

    if error_code not in RETRYABLE_CODES:
        return RetryDecision(RetryDisposition.FAIL, None)

    return RetryDecision(RetryDisposition.RETRY, RETRY_DELAYS_SECONDS[attempt_no - 1])
