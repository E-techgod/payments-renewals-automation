from __future__ import annotations

import time
from collections.abc import Callable
from functools import wraps
from typing import ParamSpec, TypeVar

P = ParamSpec("P")
R = TypeVar("R")


def retry_with_backoff(
    max_attempts: int,
    base_delay_seconds: float,
    retryable_exceptions: tuple[type[BaseException], ...],
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    if base_delay_seconds < 0:
        raise ValueError("base_delay_seconds must be non-negative")

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        @wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            attempt = 0
            while True:
                try:
                    return func(*args, **kwargs)
                except retryable_exceptions:
                    attempt += 1
                    if attempt >= max_attempts:
                        raise
                    time.sleep(base_delay_seconds * (2 ** (attempt - 1)))

        return wrapper

    return decorator
