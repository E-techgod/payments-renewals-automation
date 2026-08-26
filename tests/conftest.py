from __future__ import annotations

import faulthandler
import signal
import sys
import threading
from collections.abc import Generator

import pytest

_DEFAULT_TIMEOUT_SECONDS = "10"


class TestTimeoutExpired(Exception):
    pass


@pytest.fixture(autouse=True)
def _disable_project_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    # Keep unit tests isolated from the developer's local .env file.
    monkeypatch.setattr("app.config.load_dotenv", lambda **_kwargs: False)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addini(
        "timeout",
        "Default per-test timeout in seconds.",
        default=_DEFAULT_TIMEOUT_SECONDS,
    )
    parser.addini(
        "timeout_method",
        "Per-test timeout implementation.",
        default="signal",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "timeout(seconds): override the default per-test timeout in seconds",
    )


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item: pytest.Item) -> Generator[None]:
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    if not hasattr(signal, "setitimer"):
        yield
        return

    timeout_seconds = _timeout_for_item(item)
    if timeout_seconds <= 0:
        yield
        return

    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_delay, previous_interval = signal.getitimer(signal.ITIMER_REAL)

    def handle_timeout(signum: int, frame: object | None) -> None:
        faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
        raise TestTimeoutExpired(f"test timed out after {timeout_seconds:g}s")

    signal.signal(signal.SIGALRM, handle_timeout)
    signal.setitimer(signal.ITIMER_REAL, timeout_seconds)
    try:
        yield
    except TestTimeoutExpired as exc:
        pytest.fail(str(exc))
    finally:
        signal.setitimer(signal.ITIMER_REAL, previous_delay, previous_interval)
        signal.signal(signal.SIGALRM, previous_handler)


def _timeout_for_item(item: pytest.Item) -> float:
    marker = item.get_closest_marker("timeout")
    if marker is not None and marker.args:
        return float(marker.args[0])
    return float(item.config.getini("timeout"))
