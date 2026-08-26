from __future__ import annotations

from datetime import UTC, datetime, timedelta
from collections.abc import Iterator
from typing import Any, TypeVar

from google.auth import default as google_auth_default  # type: ignore[import-untyped]

from app.state.base import (
    ClaimOutcome,
    ClaimResult,
    LeaseLostError,
    deterministic_claim_id,
    deterministic_document_id,
    isoformat,
    new_lease_token,
    normalize_email,
    utc_now_isoformat,
)

try:
    from google.api_core.exceptions import Aborted  # type: ignore[import-untyped]
    from google.cloud import firestore  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    firestore = None

    class Aborted(Exception):
        pass


_DEFAULT_COLLECTION_NAME = "birthday_sends"
_MAX_TRANSACTION_RETRIES = 3
_T = TypeVar("_T")


class FirestoreStateStore:
    def __init__(
        self,
        stale_claim_timeout_minutes: int,
        *,
        client: Any | None = None,
        collection_name: str = _DEFAULT_COLLECTION_NAME,
        firestore_database: str = "(default)",
    ) -> None:
        if client is None:
            if firestore is None:
                raise RuntimeError(
                    "STATE_BACKEND=firestore requires the 'google-cloud-firestore' package"
                )
            _credentials, project_id = google_auth_default()
            client = firestore.Client(
                project=project_id,
                database=firestore_database,
            )
        self._client = client
        self._collection = client.collection(collection_name)
        self._stale_claim_timeout = timedelta(minutes=stale_claim_timeout_minutes)

    def claim(self, email: str, month: int, day: int, year: int) -> ClaimResult:
        email_normalized = normalize_email(email)
        claim_id = deterministic_claim_id(email_normalized, month, day, year)
        document_id = deterministic_document_id(email_normalized, month, day, year)
        now = datetime.now(UTC)
        now_iso = isoformat(now)

        def operation(transaction: Any) -> ClaimResult:
            document = self._collection.document(document_id)
            snapshot = _first_snapshot(transaction.get(document))
            if snapshot is None or not snapshot.exists:
                lease_token = new_lease_token()
                transaction.set(
                    document,
                    {
                        "claim_id": claim_id,
                        "email_normalized": email_normalized,
                        "birthday_month": month,
                        "birthday_day": day,
                        "send_year": year,
                        "status": "pending",
                        "claimed_at": now_iso,
                        "lease_token": lease_token,
                        "sent_at": None,
                        "created_at": now_iso,
                    },
                )
                return ClaimResult(ClaimOutcome.CLAIMED, claim_id, lease_token)

            data = snapshot.to_dict() or {}
            status = str(data.get("status"))
            existing_claim_id = int(data.get("claim_id", claim_id))
            if status == "sent":
                return ClaimResult(ClaimOutcome.ALREADY_SENT)

            if status == "pending":
                claimed_at = datetime.fromisoformat(str(data["claimed_at"]))
                if now - claimed_at < self._stale_claim_timeout:
                    return ClaimResult(ClaimOutcome.IN_PROGRESS)

            if status in {"failed", "pending"}:
                lease_token = new_lease_token()
                transaction.update(
                    document,
                    {
                        "status": "pending",
                        "claimed_at": now_iso,
                        "lease_token": lease_token,
                    },
                )
                return ClaimResult(ClaimOutcome.CLAIMED, existing_claim_id, lease_token)

            return ClaimResult(ClaimOutcome.IN_PROGRESS)

        return self._run_transaction_with_retry(operation)

    def mark_sent(self, claim_id: int, lease_token: str) -> None:
        self._run_pending_transition(
            claim_id,
            lease_token,
            {"status": "sent", "sent_at": utc_now_isoformat()},
        )

    def mark_failed(self, claim_id: int, lease_token: str) -> None:
        self._run_pending_transition(claim_id, lease_token, {"status": "failed"})

    def close(self) -> None:
        return None

    def _run_pending_transition(
        self,
        claim_id: int,
        lease_token: str,
        updates: dict[str, object],
    ) -> None:
        def operation(transaction: Any) -> None:
            query = self._collection.where("claim_id", "==", claim_id).limit(1)
            snapshots = list(transaction.get(query))
            if not snapshots:
                raise LeaseLostError(
                    f"Lease lost for claim_id={claim_id} lease_token={lease_token}"
                )

            snapshot = snapshots[0]
            data = snapshot.to_dict() or {}
            if (
                str(data.get("status")) != "pending"
                or str(data.get("lease_token")) != lease_token
            ):
                raise LeaseLostError(
                    f"Lease lost for claim_id={claim_id} lease_token={lease_token}"
                )
            transaction.update(snapshot.reference, updates)

        self._run_transaction_with_retry(operation)

    def _run_transaction_with_retry(self, operation: Any) -> _T:
        retry_id: bytes | None = None
        for attempt in range(_MAX_TRANSACTION_RETRIES):
            transaction = self._client.transaction()
            try:
                current_id = _begin_transaction(transaction, retry_id)
                if retry_id is None:
                    retry_id = current_id
                result = operation(transaction)
                _commit_transaction(transaction)
                return result
            except Aborted:
                _best_effort_rollback(transaction)
                if attempt == _MAX_TRANSACTION_RETRIES - 1:
                    raise
            except Exception:
                _best_effort_rollback(transaction)
                raise
        raise AssertionError("unreachable")


def _first_snapshot(snapshots: Any) -> Any | None:
    if hasattr(snapshots, "exists"):
        return snapshots
    return next(iter(snapshots), None)


def _best_effort_rollback(transaction: Any) -> None:
    rollback = getattr(transaction, "_rollback", None)
    if rollback is None:
        return
    in_progress = getattr(transaction, "in_progress", False)
    if not in_progress:
        return
    rollback()


def _begin_transaction(transaction: Any, retry_id: bytes | None) -> bytes | None:
    cleanup = getattr(transaction, "_clean_up", None)
    if cleanup is not None:
        cleanup()

    begin = getattr(transaction, "_begin", None)
    if begin is None:
        return getattr(transaction, "id", None)
    begin(retry_id=retry_id)
    return getattr(transaction, "id", None)


def _commit_transaction(transaction: Any) -> None:
    commit = getattr(transaction, "_commit", None)
    if commit is not None:
        commit()
        return
    transaction.commit()
