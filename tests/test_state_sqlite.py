from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.state.sqlite import ClaimOutcome, LeaseLostError, StateStore


def test_duplicate_same_stage_same_occurrence_returns_already_sent(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db", stale_claim_timeout_minutes=30)

    first = store.claim("client@example.com", "POL-123", "2026-09-25", "30_days")
    assert first.outcome is ClaimOutcome.CLAIMED
    assert first.claim_id is not None
    assert first.lease_token is not None

    store.mark_sent(first.claim_id, first.lease_token)

    second = store.claim("client@example.com", "POL-123", "2026-09-25", "30_days")
    assert second.outcome is ClaimOutcome.ALREADY_SENT


def test_different_stage_for_same_occurrence_does_not_collide(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db", stale_claim_timeout_minutes=30)

    first = store.claim("client@example.com", "POL-123", "2026-09-25", "30_days")
    second = store.claim("client@example.com", "POL-123", "2026-09-25", "15_days")

    assert first.outcome is ClaimOutcome.CLAIMED
    assert second.outcome is ClaimOutcome.CLAIMED
    assert first.claim_id != second.claim_id


def test_next_occurrence_can_be_claimed_again(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db", stale_claim_timeout_minutes=30)

    first = store.claim("client@example.com", "POL-123", "2026-09-25", "30_days")
    second = store.claim("client@example.com", "POL-123", "2027-09-25", "30_days")

    assert first.outcome is ClaimOutcome.CLAIMED
    assert second.outcome is ClaimOutcome.CLAIMED
    assert first.claim_id != second.claim_id


def test_retry_after_failed_send_reclaims_same_key(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db", stale_claim_timeout_minutes=30)

    first = store.claim("retry@example.com", "POL-123", "2026-09-25", "30_days")
    assert first.claim_id is not None
    assert first.lease_token is not None

    store.mark_failed(first.claim_id, first.lease_token)

    second = store.claim("retry@example.com", "POL-123", "2026-09-25", "30_days")
    assert second.outcome is ClaimOutcome.CLAIMED
    assert second.claim_id == first.claim_id


def test_stale_pending_claim_is_reclaimed(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    store = StateStore(db_path, stale_claim_timeout_minutes=30)

    first = store.claim("stale@example.com", "POL-123", "2026-09-25", "30_days")
    assert first.claim_id is not None
    assert first.lease_token is not None

    stale_timestamp = (datetime.now(UTC) - timedelta(minutes=31)).isoformat()
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE renewal_reminder_sends SET claimed_at=? WHERE id=?",
            (stale_timestamp, first.claim_id),
        )
        connection.commit()

    second = store.claim("stale@example.com", "POL-123", "2026-09-25", "30_days")
    assert second.outcome is ClaimOutcome.CLAIMED
    assert second.claim_id == first.claim_id
    assert second.lease_token is not None
    assert second.lease_token != first.lease_token


def test_mark_operations_raise_when_lease_is_lost_after_reclaim(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    store = StateStore(db_path, stale_claim_timeout_minutes=30)

    original = store.claim("lease-loss@example.com", "POL-123", "2026-09-25", "30_days")
    assert original.claim_id is not None
    assert original.lease_token is not None

    stale_timestamp = (datetime.now(UTC) - timedelta(minutes=31)).isoformat()
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE renewal_reminder_sends SET claimed_at=? WHERE id=?",
            (stale_timestamp, original.claim_id),
        )
        connection.commit()

    reclaimed = store.claim("lease-loss@example.com", "POL-123", "2026-09-25", "30_days")
    assert reclaimed.lease_token is not None

    with pytest.raises(LeaseLostError):
        store.mark_sent(original.claim_id, original.lease_token)

    with pytest.raises(LeaseLostError):
        store.mark_failed(original.claim_id, original.lease_token)
