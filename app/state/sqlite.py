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
CREATE TABLE IF NOT EXISTS renewal_reminder_sends (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_key TEXT NOT NULL,
    policy_key TEXT NOT NULL,
    renewal_date TEXT NOT NULL,
    reminder_stage TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending','sent','failed')),
    claimed_at TEXT NOT NULL,
    lease_token TEXT NOT NULL,
    sent_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (client_key, policy_key, renewal_date, reminder_stage)
);
"""


class StateStore:
    def __init__(
        self,
        db_path: Path,
        stale_claim_timeout_minutes: int,
        *,
        table_name: str = "renewal_reminder_sends",
    ) -> None:
        self._db_path = db_path
        self._stale_claim_timeout = timedelta(minutes=stale_claim_timeout_minutes)
        self._table_name = table_name
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

    def claim(
        self,
        client_key: str,
        policy_key: str,
        renewal_date_iso: str,
        stage_name: str,
    ) -> ClaimResult:
        return self._run_with_locked_retry(
            lambda: self._claim_once(
                normalize_email(client_key),
                policy_key.strip(),
                renewal_date_iso,
                stage_name,
            )
        )

    def mark_sent(self, claim_id: int, lease_token: str) -> None:
        sent_at = utc_now_isoformat()
        self._run_pending_transition(
            f"""
            UPDATE {self._table_name}
            SET status='sent', sent_at=?
            WHERE id=? AND status='pending' AND lease_token=?
            """,
            (sent_at, claim_id, lease_token),
            claim_id=claim_id,
            lease_token=lease_token,
        )

    def mark_failed(self, claim_id: int, lease_token: str) -> None:
        self._run_pending_transition(
            f"""
            UPDATE {self._table_name}
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
        client_key: str,
        policy_key: str,
        renewal_date_iso: str,
        stage_name: str,
    ) -> ClaimResult:
        now = datetime.now(UTC)
        now_iso = _isoformat(now)
        lease_token = new_lease_token()
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            try:
                cursor = self._connection.execute(
                    f"""
                    INSERT INTO {self._table_name} (
                        client_key,
                        policy_key,
                        renewal_date,
                        reminder_stage,
                        status,
                        claimed_at,
                        lease_token,
                        created_at
                    ) VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)
                    """,
                    (
                        client_key,
                        policy_key,
                        renewal_date_iso,
                        stage_name,
                        now_iso,
                        lease_token,
                        now_iso,
                    ),
                )
            except sqlite3.IntegrityError:
                result = self._handle_duplicate_claim(
                    client_key=client_key,
                    policy_key=policy_key,
                    renewal_date_iso=renewal_date_iso,
                    stage_name=stage_name,
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
        client_key: str,
        policy_key: str,
        renewal_date_iso: str,
        stage_name: str,
        now: datetime,
        now_iso: str,
    ) -> ClaimResult:
        row = self._connection.execute(
            f"""
            SELECT id, status, claimed_at
            FROM {self._table_name}
            WHERE client_key=? AND policy_key=? AND renewal_date=? AND reminder_stage=?
            """,
            (client_key, policy_key, renewal_date_iso, stage_name),
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
                f"""
                UPDATE {self._table_name}
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
                self._connection.execute(
                    _CREATE_TABLE_SQL.replace(
                        "renewal_reminder_sends", self._table_name
                    )
                )
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
