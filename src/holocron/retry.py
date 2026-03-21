import functools
import time

import requests

from .logger import logger


def retry_on_failure(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    retryable_exceptions: tuple[type[Exception], ...] = (
        requests.ConnectionError,
        requests.Timeout,
    ),
):
    """Decorator that retries a function with exponential backoff on transient failures."""

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exception: Exception = Exception("Unexpected retry failure")
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except retryable_exceptions as e:
                    last_exception = e
                    if attempt < max_retries:
                        delay = min(base_delay * (2**attempt), max_delay)
                        logger.warning(
                            f"[Retry] {func.__name__} failed (attempt {attempt + 1}/{max_retries + 1}): {e}. "
                            f"Retrying in {delay:.1f}s..."
                        )
                        time.sleep(delay)
                    else:
                        logger.error(f"[Retry] {func.__name__} failed after {max_retries + 1} attempts: {e}")
            raise last_exception

        return wrapper

    return decorator


def handle_rate_limit(response: requests.Response) -> bool:
    """Checks for rate limiting headers and waits if necessary.

    Returns True if the request should be retried (429 received).
    """
    if response.status_code == 429:
        retry_after = response.headers.get("Retry-After")
        wait_time = int(retry_after) if retry_after else 60
        logger.warning(f"[Rate Limit] Hit rate limit. Waiting {wait_time}s...")
        time.sleep(wait_time)
        return True

    remaining = response.headers.get("X-RateLimit-Remaining")
    if remaining is not None and int(remaining) <= 5:
        reset_time = response.headers.get("X-RateLimit-Reset")
        if reset_time:
            wait_seconds = max(0, int(reset_time) - int(time.time())) + 1
            if wait_seconds > 0 and wait_seconds < 300:
                logger.warning(
                    f"[Rate Limit] Only {remaining} requests remaining. Waiting {wait_seconds}s for reset..."
                )
                time.sleep(wait_seconds)
    return False
