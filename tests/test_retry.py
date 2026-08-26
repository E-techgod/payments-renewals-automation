from __future__ import annotations

import pytest

from app.retry import retry_with_backoff


def test_retry_with_backoff_retries_retryable_exception_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleep_calls: list[float] = []
    monkeypatch.setattr("app.retry.time.sleep", sleep_calls.append)
    attempts = {"count": 0}

    @retry_with_backoff(
        max_attempts=3,
        base_delay_seconds=0.5,
        retryable_exceptions=(TimeoutError,),
    )
    def flaky_operation() -> str:
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise TimeoutError("synthetic timeout")
        return "ok"

    assert flaky_operation() == "ok"
    assert attempts["count"] == 3
    assert sleep_calls == [0.5, 1.0]


def test_retry_with_backoff_does_not_retry_non_retryable_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleep_calls: list[float] = []
    monkeypatch.setattr("app.retry.time.sleep", sleep_calls.append)
    attempts = {"count": 0}

    @retry_with_backoff(
        max_attempts=3,
        base_delay_seconds=0.5,
        retryable_exceptions=(TimeoutError,),
    )
    def invalid_operation() -> None:
        attempts["count"] += 1
        raise ValueError("synthetic permanent failure")

    with pytest.raises(ValueError, match="synthetic permanent failure"):
        invalid_operation()

    assert attempts["count"] == 1
    assert sleep_calls == []
