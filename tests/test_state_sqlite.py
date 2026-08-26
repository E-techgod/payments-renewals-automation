from __future__ import annotations

import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from queue import Queue
from typing import Any

import pytest

from app.state.sqlite import ClaimOutcome, LeaseLostError, StateStore


class CommitFailsOnceConnection:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._failed = False

    def execute(self, sql: str, parameters: tuple[object, ...] = ()) -> sqlite3.Cursor:
        return self._connection.execute(sql, parameters)

    def commit(self) -> None:
        if not self._failed:
            self._failed = True
            raise sqlite3.OperationalError("database is locked")
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()

    def close(self) -> None:
        self._connection.close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)


def test_duplicate_send_same_year_returns_already_sent(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db", stale_claim_timeout_minutes=30)

    first = store.claim("client@example.com", 8, 19, 2026)
    assert first.outcome is ClaimOutcome.CLAIMED
    assert first.claim_id is not None
    assert first.lease_token is not None

    store.mark_sent(first.claim_id, first.lease_token)

    second = store.claim("client@example.com", 8, 19, 2026)
    assert second.outcome is ClaimOutcome.ALREADY_SENT
    assert second.claim_id is None

    store.close()


def test_retry_after_failed_send_reclaims_same_row(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db", stale_claim_timeout_minutes=30)

    first = store.claim("retry@example.com", 8, 19, 2026)
    assert first.outcome is ClaimOutcome.CLAIMED
    assert first.claim_id is not None
    assert first.lease_token is not None

    store.mark_failed(first.claim_id, first.lease_token)

    second = store.claim("retry@example.com", 8, 19, 2026)
    assert second.outcome is ClaimOutcome.CLAIMED
    assert second.claim_id == first.claim_id

    status = _fetch_status(tmp_path / "state.db", first.claim_id)
    assert status == "pending"

    store.close()


def test_stale_pending_claim_is_reclaimed(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    store = StateStore(db_path, stale_claim_timeout_minutes=30)

    first = store.claim("stale@example.com", 8, 19, 2026)
    assert first.outcome is ClaimOutcome.CLAIMED
    assert first.claim_id is not None
    assert first.lease_token is not None

    stale_timestamp = (datetime.now(UTC) - timedelta(minutes=31)).isoformat()
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE birthday_sends SET claimed_at=? WHERE id=?",
            (stale_timestamp, first.claim_id),
        )
        connection.commit()

    second = store.claim("stale@example.com", 8, 19, 2026)
    assert second.outcome is ClaimOutcome.CLAIMED
    assert second.claim_id == first.claim_id
    assert second.lease_token is not None
    assert second.lease_token != first.lease_token

    store.close()


def test_pending_claim_in_progress_when_not_stale(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db", stale_claim_timeout_minutes=30)

    first = store.claim("pending@example.com", 8, 19, 2026)
    assert first.outcome is ClaimOutcome.CLAIMED

    second = store.claim("pending@example.com", 8, 19, 2026)
    assert second.outcome is ClaimOutcome.IN_PROGRESS
    assert second.claim_id is None

    store.close()


def test_mark_operations_raise_when_lease_is_lost_after_reclaim(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    store = StateStore(db_path, stale_claim_timeout_minutes=30)

    original = store.claim("lease-loss@example.com", 8, 19, 2026)
    assert original.outcome is ClaimOutcome.CLAIMED
    assert original.claim_id is not None
    assert original.lease_token is not None

    stale_timestamp = (datetime.now(UTC) - timedelta(minutes=31)).isoformat()
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE birthday_sends SET claimed_at=? WHERE id=?",
            (stale_timestamp, original.claim_id),
        )
        connection.commit()

    reclaimed = store.claim("lease-loss@example.com", 8, 19, 2026)
    assert reclaimed.outcome is ClaimOutcome.CLAIMED
    assert reclaimed.claim_id == original.claim_id
    assert reclaimed.lease_token is not None
    assert reclaimed.lease_token != original.lease_token

    with pytest.raises(LeaseLostError):
        store.mark_sent(original.claim_id, original.lease_token)

    with pytest.raises(LeaseLostError):
        store.mark_failed(original.claim_id, original.lease_token)

    store.close()


def test_reclaim_rotates_lease_token_even_when_claimed_at_collides(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "state.db"
    store = StateStore(db_path, stale_claim_timeout_minutes=30)

    original = store.claim("claimed-at-collision@example.com", 8, 19, 2026)
    assert original.outcome is ClaimOutcome.CLAIMED
    assert original.claim_id is not None
    assert original.lease_token is not None

    identical_claimed_at = "2026-08-19T12:00:00+00:00"
    stale_timestamp = "2026-08-19T11:00:00+00:00"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE birthday_sends SET status='failed', claimed_at=? WHERE id=?",
            (stale_timestamp, original.claim_id),
        )
        connection.commit()

    original_datetime = datetime

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz: Any | None = None) -> datetime:
            frozen = original_datetime.fromisoformat(identical_claimed_at)
            if tz is not None:
                return frozen.astimezone(tz)
            return frozen.replace(tzinfo=None)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("app.state.sqlite.datetime", FrozenDateTime)
    try:
        reclaimed = store.claim("claimed-at-collision@example.com", 8, 19, 2026)
    finally:
        monkeypatch.undo()

    assert reclaimed.outcome is ClaimOutcome.CLAIMED
    assert reclaimed.claim_id == original.claim_id
    assert reclaimed.lease_token is not None
    assert reclaimed.lease_token != original.lease_token

    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT claimed_at, lease_token, status FROM birthday_sends WHERE id=?",
            (original.claim_id,),
        ).fetchone()

    assert row is not None
    assert row[0] == identical_claimed_at
    assert row[1] == reclaimed.lease_token
    assert row[2] == "pending"

    with pytest.raises(LeaseLostError):
        store.mark_sent(original.claim_id, original.lease_token)

    with pytest.raises(LeaseLostError):
        store.mark_failed(original.claim_id, original.lease_token)

    store.mark_failed(reclaimed.claim_id, reclaimed.lease_token)
    assert _fetch_status(db_path, reclaimed.claim_id) == "failed"

    store.close()


def test_concurrent_claim_is_exclusive(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    barrier = threading.Barrier(2)
    results: Queue[tuple[ClaimOutcome, int | None]] = Queue()

    def worker() -> None:
        store = StateStore(db_path, stale_claim_timeout_minutes=30)
        try:
            barrier.wait(timeout=5)
            result = store.claim("race@example.com", 8, 19, 2026)
            results.put((result.outcome, result.claim_id))
        finally:
            store.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    outcomes = [results.get_nowait() for _ in range(2)]
    claimed_count = sum(1 for outcome, _ in outcomes if outcome is ClaimOutcome.CLAIMED)
    in_progress_count = sum(
        1 for outcome, _ in outcomes if outcome is ClaimOutcome.IN_PROGRESS
    )

    assert claimed_count == 1
    assert in_progress_count == 1


def test_different_keys_do_not_collide(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db", stale_claim_timeout_minutes=30)

    first = store.claim("alpha@example.com", 8, 19, 2026)
    second = store.claim("beta@example.com", 8, 19, 2026)

    assert first.outcome is ClaimOutcome.CLAIMED
    assert second.outcome is ClaimOutcome.CLAIMED
    assert first.claim_id != second.claim_id
    assert first.lease_token is not None
    assert second.lease_token is not None

    store.close()


def test_mark_operations_only_affect_pending_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    store = StateStore(db_path, stale_claim_timeout_minutes=30)

    first = store.claim("status@example.com", 8, 19, 2026)
    assert first.claim_id is not None
    assert first.lease_token is not None
    store.mark_sent(first.claim_id, first.lease_token)

    with pytest.raises(LeaseLostError):
        store.mark_failed(first.claim_id, first.lease_token)

    with pytest.raises(LeaseLostError):
        store.mark_sent(999999, first.lease_token)

    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT status, sent_at FROM birthday_sends WHERE id=?",
            (first.claim_id,),
        ).fetchone()

    assert row is not None
    assert row[0] == "sent"
    assert row[1] is not None

    store.close()


def test_claim_retries_cleanly_after_commit_lock_failure(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    store = StateStore(db_path, stale_claim_timeout_minutes=30)
    store._connection = CommitFailsOnceConnection(store._connection)

    first = store.claim("commit-lock-claim@example.com", 8, 19, 2026)
    assert first.outcome is ClaimOutcome.CLAIMED
    assert first.claim_id is not None
    assert first.lease_token is not None

    second = store.claim("followup-claim@example.com", 8, 19, 2026)
    assert second.outcome is ClaimOutcome.CLAIMED
    assert second.claim_id is not None
    assert second.lease_token is not None

    assert _count_rows_for_email(db_path, "commit-lock-claim@example.com") == 1
    assert _count_rows_for_email(db_path, "followup-claim@example.com") == 1

    store.close()


@pytest.mark.parametrize("transition_method", ["mark_sent", "mark_failed"])
def test_pending_transition_retries_cleanly_after_commit_lock_failure(
    tmp_path: Path,
    transition_method: str,
) -> None:
    db_path = tmp_path / "state.db"
    store = StateStore(db_path, stale_claim_timeout_minutes=30)
    claim = store.claim(f"commit-lock-{transition_method}@example.com", 8, 19, 2026)
    assert claim.claim_id is not None
    assert claim.lease_token is not None

    store._connection = CommitFailsOnceConnection(store._connection)

    transition = getattr(store, transition_method)
    transition(claim.claim_id, claim.lease_token)
    assert _fetch_status(db_path, claim.claim_id) == (
        "sent" if transition_method == "mark_sent" else "failed"
    )

    follow_up = store.claim(f"followup-{transition_method}@example.com", 8, 19, 2026)
    assert follow_up.outcome is ClaimOutcome.CLAIMED
    assert follow_up.claim_id is not None
    assert follow_up.lease_token is not None

    store.close()


def _fetch_status(db_path: Path, claim_id: int) -> str:
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT status FROM birthday_sends WHERE id=?",
            (claim_id,),
        ).fetchone()
    assert row is not None
    return str(row[0])


def _count_rows_for_email(db_path: Path, email_normalized: str) -> int:
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT COUNT(*) FROM birthday_sends WHERE email_normalized=?",
            (email_normalized,),
        ).fetchone()
    assert row is not None
    return int(row[0])
