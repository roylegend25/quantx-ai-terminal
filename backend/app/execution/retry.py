"""Configurable exponential-backoff retry helper for execution calls.

Only retries failures that are plausibly transient - network timeouts and
5xx/429 exchange responses. Anything else (validation errors, explicit
rejections) is treated as permanent and propagates on the first attempt.
"""
import asyncio
import random
from dataclasses import dataclass
from typing import Awaitable, Callable, TypeVar

import httpx

T = TypeVar("T")


class PermanentError(Exception):
    """Raised by callers to signal a failure retrying would never fix."""


RETRYABLE_EXCEPTIONS = (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError)


def is_retryable_status(status_code: int) -> bool:
    return status_code == 429 or 500 <= status_code < 600


@dataclass
class RetryConfig:
    max_attempts: int = 4
    base_delay_seconds: float = 0.5
    max_delay_seconds: float = 8.0
    multiplier: float = 2.0
    jitter: bool = True


@dataclass
class RetryOutcome:
    attempts: int = 0
    retries: int = 0
    total_delay_seconds: float = 0.0
    last_error: str | None = None


def _delay_for_attempt(attempt: int, config: RetryConfig) -> float:
    delay = min(config.base_delay_seconds * (config.multiplier**attempt), config.max_delay_seconds)
    if config.jitter:
        delay = delay * (0.5 + random.random() / 2)  # 50-100% of the computed delay
    return delay


async def with_retry(
    fn: Callable[[], Awaitable[T]],
    config: RetryConfig | None = None,
    retry_on_status: Callable[[int], bool] = is_retryable_status,
) -> tuple[T, RetryOutcome]:
    """Runs fn() with exponential backoff.

    fn should raise httpx.HTTPStatusError for HTTP-level failures, or one of
    RETRYABLE_EXCEPTIONS for network faults, to be considered for a retry.
    Any other exception (including PermanentError) aborts immediately.
    """
    config = config or RetryConfig()
    outcome = RetryOutcome()

    for attempt in range(config.max_attempts):
        outcome.attempts += 1
        try:
            result = await fn()
            return result, outcome
        except httpx.HTTPStatusError as e:
            outcome.last_error = repr(e)
            if not retry_on_status(e.response.status_code) or attempt == config.max_attempts - 1:
                raise
        except RETRYABLE_EXCEPTIONS as e:
            outcome.last_error = repr(e)
            if attempt == config.max_attempts - 1:
                raise

        delay = _delay_for_attempt(attempt, config)
        outcome.retries += 1
        outcome.total_delay_seconds += delay
        await asyncio.sleep(delay)

    raise RuntimeError("unreachable: with_retry loop must return or raise")
