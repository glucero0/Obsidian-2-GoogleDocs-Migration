import threading
import time
from typing import Callable, TypeVar

from googleapiclient.errors import HttpError

from .constants import MIN_MILLISECONDS_BETWEEN_API_CALLS

T = TypeVar("T")

_lock = threading.Lock()
_last_api_call = 0.0

RETRYABLE_STATUS_CODES = {429, 500, 503, 403}


def throttle_api() -> None:
    """Enforce minimum spacing between API calls."""
    global _last_api_call
    with _lock:
        minimum_interval = MIN_MILLISECONDS_BETWEEN_API_CALLS / 1000.0
        elapsed = time.monotonic() - _last_api_call
        if elapsed < minimum_interval:
            time.sleep(minimum_interval - elapsed)
        _last_api_call = time.monotonic()


def throttle_and_execute(api_call: Callable[[], T]) -> T:
    """Throttle then execute an API call with exponential backoff on retryable errors."""
    throttle_api()

    delta = 2.0
    max_interval = 64.0
    max_elapsed = 300.0
    max_retries = 10
    elapsed_total = 0.0
    last_error: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            return api_call()
        except HttpError as exc:
            status = exc.resp.status if exc.resp else None
            if status not in RETRYABLE_STATUS_CODES or attempt >= max_retries:
                raise
            last_error = exc
            wait = min(delta * (2 ** attempt), max_interval)
            if elapsed_total + wait > max_elapsed:
                raise
            time.sleep(wait)
            elapsed_total += wait
        except Exception:
            raise

    if last_error:
        raise last_error
    raise RuntimeError("throttle_and_execute failed without result")
