from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.state.base import deterministic_claim_id, deterministic_document_id
from app.state.firestore import Aborted, ClaimOutcome, FirestoreStateStore, LeaseLostError


class FakeDocumentSnapshot:
    def __init__(
        self,
        reference: FakeDocumentReference,
        data: dict[str, object] | None,
        *,
        exists: bool,
    ) -> None:
        self.reference = reference
        self._data = deepcopy(data) if data is not None else None
        self.exists = exists

    def to_dict(self) -> dict[str, object] | None:
        return deepcopy(self._data)


class FakeDocumentReference:
    def __init__(self, collection: FakeCollection, document_id: str) -> None:
        self._collection = collection
        self.id = document_id

    def get(self) -> FakeDocumentSnapshot:
        return self._collection._get_snapshot(self.id)


class FakeQuery:
    def __init__(self, collection: FakeCollection, field: str, value: object) -> None:
        self._collection = collection
        self._field = field
        self._value = value
        self._limit: int | None = None

    def limit(self, value: int) -> FakeQuery:
        self._limit = value
        return self

    def execute(self) -> list[FakeDocumentSnapshot]:
        matches: list[FakeDocumentSnapshot] = []
        for document_id, row in self._collection._documents.items():
            data = row["data"]
            if data.get(self._field) == self._value:
                matches.append(self._collection._get_snapshot(document_id))
            if self._limit is not None and len(matches) >= self._limit:
                break
        return matches


class FakeCollection:
    def __init__(self) -> None:
        self._documents: dict[str, dict[str, object]] = {}

    def document(self, document_id: str) -> FakeDocumentReference:
        return FakeDocumentReference(self, document_id)

    def where(self, field: str, _operator: str, value: object) -> FakeQuery:
        return FakeQuery(self, field, value)

    def _get_snapshot(self, document_id: str) -> FakeDocumentSnapshot:
        row = self._documents.get(document_id)
        if row is None:
            return FakeDocumentSnapshot(self.document(document_id), None, exists=False)
        return FakeDocumentSnapshot(
            self.document(document_id),
            row["data"],  # type: ignore[arg-type]
            exists=True,
        )


class FakeTransaction:
    def __init__(self, collection: FakeCollection, client: FakeFirestoreClient) -> None:
        self._collection = collection
        self._client = client
        self._reads: dict[str, int | None] = {}
        self._writes: list[tuple[str, str, dict[str, object]]] = []
        self._id: bytes | None = None

    @property
    def in_progress(self) -> bool:
        return self._id is not None

    @property
    def id(self) -> bytes | None:
        return self._id

    def _clean_up(self) -> None:
        self._reads = {}
        self._writes = []
        self._id = None

    def _begin(self, retry_id: bytes | None = None) -> None:
        self._id = retry_id or b"synthetic-transaction-id"

    def get(self, target: FakeDocumentReference | FakeQuery) -> Any:
        if isinstance(target, FakeQuery):
            snapshots = target.execute()
            for snapshot in snapshots:
                row = self._collection._documents.get(snapshot.reference.id)
                version = None if row is None else int(row["version"])
                self._reads.setdefault(snapshot.reference.id, version)
            return snapshots

        snapshot = target.get()
        row = self._collection._documents.get(target.id)
        version = None if row is None else int(row["version"])
        self._reads.setdefault(target.id, version)
        return iter([snapshot])

    def set(self, document: FakeDocumentReference, data: dict[str, object]) -> None:
        self._writes.append(("set", document.id, deepcopy(data)))

    def update(self, document: FakeDocumentReference, data: dict[str, object]) -> None:
        self._writes.append(("update", document.id, deepcopy(data)))

    def commit(self) -> None:
        if self._client.abort_commit_attempts > 0:
            self._client.abort_commit_attempts -= 1
            raise Aborted("synthetic transaction abort")

        for document_id, expected_version in self._reads.items():
            current = self._collection._documents.get(document_id)
            current_version = None if current is None else int(current["version"])
            if current_version != expected_version:
                raise Aborted("synthetic transaction conflict")

        for operation, document_id, data in self._writes:
            current = self._collection._documents.get(document_id)
            current_data = {} if current is None else deepcopy(current["data"])
            new_data = deepcopy(data) if operation == "set" else current_data | deepcopy(data)
            current_version = 0 if current is None else int(current["version"])
            self._collection._documents[document_id] = {
                "data": new_data,
                "version": current_version + 1,
            }
        self._clean_up()

    def _commit(self) -> None:
        self.commit()

    def _rollback(self) -> None:
        self._clean_up()


class FakeFirestoreClient:
    def __init__(self) -> None:
        self._collection = FakeCollection()
        self.abort_commit_attempts = 0

    def collection(self, _name: str) -> FakeCollection:
        return self._collection

    def transaction(self) -> FakeTransaction:
        return FakeTransaction(self._collection, self)


def test_first_claim_creates_pending_record() -> None:
    store, client = _build_store()

    result = store.claim("client@example.com", "POL-123", "2026-09-25", "30_days")

    assert result.outcome is ClaimOutcome.CLAIMED
    assert result.claim_id == deterministic_claim_id(
        "client@example.com", "POL-123", "2026-09-25", "30_days"
    )
    row = _get_row(client, "client@example.com", "POL-123", "2026-09-25", "30_days")
    assert row["status"] == "pending"


def test_duplicate_sent_claim_returns_already_sent() -> None:
    store, _client = _build_store()

    first = store.claim("client@example.com", "POL-123", "2026-09-25", "30_days")
    assert first.claim_id is not None
    assert first.lease_token is not None
    store.mark_sent(first.claim_id, first.lease_token)

    second = store.claim("client@example.com", "POL-123", "2026-09-25", "30_days")

    assert second.outcome is ClaimOutcome.ALREADY_SENT


def test_different_stage_does_not_collide() -> None:
    store, _client = _build_store()

    first = store.claim("client@example.com", "POL-123", "2026-09-25", "30_days")
    second = store.claim("client@example.com", "POL-123", "2026-09-25", "15_days")

    assert first.outcome is ClaimOutcome.CLAIMED
    assert second.outcome is ClaimOutcome.CLAIMED
    assert first.claim_id != second.claim_id


def test_stale_claim_is_reclaimed() -> None:
    store, client = _build_store()

    first = store.claim("stale@example.com", "POL-123", "2026-09-25", "30_days")
    assert first.claim_id is not None
    assert first.lease_token is not None
    _set_claimed_at(
        client, "stale@example.com", "POL-123", "2026-09-25", "30_days", minutes_ago=31
    )

    second = store.claim("stale@example.com", "POL-123", "2026-09-25", "30_days")

    assert second.outcome is ClaimOutcome.CLAIMED
    assert second.claim_id == first.claim_id
    assert second.lease_token is not None
    assert second.lease_token != first.lease_token


def test_mark_operations_validate_lease_token() -> None:
    store, client = _build_store()

    original = store.claim("lease@example.com", "POL-123", "2026-09-25", "30_days")
    assert original.claim_id is not None
    assert original.lease_token is not None
    _set_claimed_at(
        client, "lease@example.com", "POL-123", "2026-09-25", "30_days", minutes_ago=31
    )
    reclaimed = store.claim("lease@example.com", "POL-123", "2026-09-25", "30_days")
    assert reclaimed.lease_token is not None

    with pytest.raises(LeaseLostError):
        store.mark_sent(original.claim_id, original.lease_token)

    with pytest.raises(LeaseLostError):
        store.mark_failed(original.claim_id, original.lease_token)


def _build_store() -> tuple[FirestoreStateStore, FakeFirestoreClient]:
    client = FakeFirestoreClient()
    store = FirestoreStateStore(30, client=client)
    return store, client


def _get_row(
    client: FakeFirestoreClient,
    client_key: str,
    policy_key: str,
    renewal_date_iso: str,
    stage_name: str,
) -> dict[str, object]:
    document_id = deterministic_document_id(
        client_key, policy_key, renewal_date_iso, stage_name
    )
    return deepcopy(client._collection._documents[document_id]["data"])  # type: ignore[index]


def _set_claimed_at(
    client: FakeFirestoreClient,
    client_key: str,
    policy_key: str,
    renewal_date_iso: str,
    stage_name: str,
    *,
    minutes_ago: int,
) -> None:
    document_id = deterministic_document_id(
        client_key, policy_key, renewal_date_iso, stage_name
    )
    row = client._collection._documents[document_id]["data"]
    row["claimed_at"] = (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat()
