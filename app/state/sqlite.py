from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TypeVar

from app.state.base import (
    ClaimOutcome,
    ClaimResult,
    LeaseLostError,
    new_lease_token,
    normalize_email,
    utc_now_isoformat,
)

_SQLITE_TIMEOUT_SECONDS = 30
_INIT_RETRY_DELAY_SECONDS = 0.1
_LOCK_RETRY_DELAY_SECONDS = 0.2
_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS birthday_sends (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email_normalized TEXT NOT NULL,
    birthday_month INTEGER NOT NULL,
    birthday_day INTEGER NOT NULL,
    send_year INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending','sent','failed')),
    claimed_at TEXT NOT NULL,
    lease_token TEXT NOT NULL,
    sent_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (email_normalized, birthday_month, birthday_day, send_year)
);
"""


class StateStore:
    def __init__(self, db_path: Path, stale_claim_timeout_minutes: int) -> None:
        self._db_path = db_path
        self._stale_claim_timeout = timedelta(minutes=stale_claim_timeout_minutes)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(
            self._db_path,
            timeout=_SQLITE_TIMEOUT_SECONDS,
            isolation_level=None,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute(
            f"PRAGMA busy_timeout={int(_SQLITE_TIMEOUT_SECONDS * 1000)}"
        )
        self._initialize_connection()

    def claim(self, email: str, month: int, day: int, year: int) -> ClaimResult:
        email_normalized = normalize_email(email)
        return self._run_with_locked_retry(
            lambda: self._claim_once(email_normalized, month, day, year)
        )

    def mark_sent(self, claim_id: int, lease_token: str) -> None:
        sent_at = utc_now_isoformat()
        self._run_pending_transition(
            """
            UPDATE birthday_sends
            SET status='sent', sent_at=?
            WHERE id=? AND status='pending' AND lease_token=?
            """,
            (sent_at, claim_id, lease_token),
            claim_id=claim_id,
            lease_token=lease_token,
        )

    def mark_failed(self, claim_id: int, lease_token: str) -> None:
        self._run_pending_transition(
            """
            UPDATE birthday_sends
            SET status='failed'
            WHERE id=? AND status='pending' AND lease_token=?
            """,
            (claim_id, lease_token),
            claim_id=claim_id,
            lease_token=lease_token,
        )

    def close(self) -> None:
        self._connection.close()

    def _claim_once(
        self,
        email_normalized: str,
        month: int,
        day: int,
        year: int,
    ) -> ClaimResult:
        now = datetime.now(UTC)
        now_iso = _isoformat(now)
        lease_token = new_lease_token()
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            try:
                cursor = self._connection.execute(
                    """
                    INSERT INTO birthday_sends (
                        email_normalized,
                        birthday_month,
                        birthday_day,
                        send_year,
                        status,
                        claimed_at,
                        lease_token,
                        created_at
                    ) VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)
                    """,
                    (email_normalized, month, day, year, now_iso, lease_token, now_iso),
                )
            except sqlite3.IntegrityError:
                result = self._handle_duplicate_claim(
                    email_normalized=email_normalized,
                    month=month,
                    day=day,
                    year=year,
                    now=now,
                    now_iso=now_iso,
                )
                self._connection.commit()
                return result

            self._connection.commit()
            last_row_id = cursor.lastrowid
            if last_row_id is None:
                raise sqlite3.OperationalError("insert did not return a row id")
            return ClaimResult(ClaimOutcome.CLAIMED, int(last_row_id), lease_token)
        except Exception:
            self._connection.rollback()
            raise

    def _handle_duplicate_claim(
        self,
        *,
        email_normalized: str,
        month: int,
        day: int,
        year: int,
        now: datetime,
        now_iso: str,
    ) -> ClaimResult:
        row = self._connection.execute(
            """
            SELECT id, status, claimed_at
            FROM birthday_sends
            WHERE email_normalized=? AND birthday_month=? AND birthday_day=? AND send_year=?
            """,
            (email_normalized, month, day, year),
        ).fetchone()
        if row is None:
            return ClaimResult(ClaimOutcome.IN_PROGRESS)

        claim_id_value = row["id"]
        if claim_id_value is None:
            return ClaimResult(ClaimOutcome.IN_PROGRESS)
        claim_id = int(claim_id_value)
        status = str(row["status"])
        if status == "sent":
            return ClaimResult(ClaimOutcome.ALREADY_SENT)

        if status == "pending":
            claimed_at = datetime.fromisoformat(str(row["claimed_at"]))
            if now - claimed_at < self._stale_claim_timeout:
                return ClaimResult(ClaimOutcome.IN_PROGRESS)

        if status in {"failed", "pending"}:
            lease_token = new_lease_token()
            cursor = self._connection.execute(
                """
                UPDATE birthday_sends
                SET status='pending', claimed_at=?, lease_token=?
                WHERE id=? AND status=?
                """,
                (now_iso, lease_token, claim_id, status),
            )
            if cursor.rowcount == 1:
                return ClaimResult(ClaimOutcome.CLAIMED, claim_id, lease_token)
            return ClaimResult(ClaimOutcome.IN_PROGRESS)

        return ClaimResult(ClaimOutcome.IN_PROGRESS)

    def _run_pending_transition(
        self,
        sql: str,
        parameters: tuple[object, ...],
        *,
        claim_id: int,
        lease_token: str,
    ) -> None:
        def run_transition() -> None:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = self._connection.execute(sql, parameters)
                if cursor.rowcount != 1:
                    raise LeaseLostError(
                        f"Lease lost for claim_id={claim_id} lease_token={lease_token}"
                    )
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise

        self._run_with_locked_retry(run_transition)

    def _run_with_locked_retry(self, operation: Callable[[], _T]) -> _T:
        for attempt in range(2):
            try:
                return operation()
            except sqlite3.OperationalError as exc:
                if "database is locked" not in str(exc).lower() or attempt == 1:
                    raise
                time.sleep(_LOCK_RETRY_DELAY_SECONDS)
        raise AssertionError("unreachable")

    def _initialize_connection(self) -> None:
        deadline = time.monotonic() + _SQLITE_TIMEOUT_SECONDS
        while True:
            try:
                journal_mode = self._connection.execute(
                    "PRAGMA journal_mode"
                ).fetchone()
                if journal_mode is None or str(journal_mode[0]).lower() != "wal":
                    self._connection.execute("PRAGMA journal_mode=WAL")
                self._connection.execute(_CREATE_TABLE_SQL)
                return
            except sqlite3.OperationalError as exc:
                if "database is locked" not in str(exc).lower():
                    raise
                if time.monotonic() >= deadline:
                    raise
                time.sleep(_INIT_RETRY_DELAY_SECONDS)
def _isoformat(value: datetime) -> str:
    return value.isoformat()


_T = TypeVar("_T")
