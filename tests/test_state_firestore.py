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
        self._rolled_back = False
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
            if operation == "set":
                new_data = deepcopy(data)
            else:
                new_data = current_data
                new_data.update(deepcopy(data))
            current_version = 0 if current is None else int(current["version"])
            self._collection._documents[document_id] = {
                "data": new_data,
                "version": current_version + 1,
            }
        self._clean_up()

    def _commit(self) -> None:
        self.commit()

    def _rollback(self) -> None:
        self._rolled_back = True
        self._clean_up()

class FakeFirestoreClient:
    def __init__(self) -> None:
        self._collection = FakeCollection()
        self.abort_commit_attempts = 0

    def collection(self, _name: str) -> FakeCollection:
        return self._collection

    def transaction(self) -> FakeTransaction:
        return FakeTransaction(self._collection, self)


def test_constructor_passes_explicit_project_and_database(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class FakeFirestoreModule:
        class Client:
            def __init__(self, *, project: str | None = None, database: str | None = None) -> None:
                captured["project"] = project
                captured["database"] = database

            def collection(self, _name: str) -> FakeCollection:
                return FakeCollection()

    monkeypatch.setattr("app.state.firestore.firestore", FakeFirestoreModule)
    monkeypatch.setattr(
        "app.state.firestore.google_auth_default",
        lambda: (object(), "synthetic-project"),
    )

    FirestoreStateStore(30, firestore_database="birthday-automation")

    assert captured == {
        "project": "synthetic-project",
        "database": "birthday-automation",
    }


def test_first_claim_creates_pending_record() -> None:
    store, client = _build_store()

    result = store.claim("Client@Example.com", 8, 19, 2026)

    assert result.outcome is ClaimOutcome.CLAIMED
    assert result.claim_id == deterministic_claim_id("client@example.com", 8, 19, 2026)
    assert result.lease_token is not None

    row = _get_row(client, "client@example.com", 8, 19, 2026)
    assert row["status"] == "pending"
    assert row["email_normalized"] == "client@example.com"
    assert row["lease_token"] == result.lease_token


def test_duplicate_sent_claim_returns_already_sent() -> None:
    store, _client = _build_store()

    first = store.claim("client@example.com", 8, 19, 2026)
    assert first.claim_id is not None
    assert first.lease_token is not None
    store.mark_sent(first.claim_id, first.lease_token)

    second = store.claim("client@example.com", 8, 19, 2026)

    assert second.outcome is ClaimOutcome.ALREADY_SENT
    assert second.claim_id is None
    assert second.lease_token is None


def test_active_claim_returns_in_progress() -> None:
    store, _client = _build_store()

    first = store.claim("pending@example.com", 8, 19, 2026)
    assert first.outcome is ClaimOutcome.CLAIMED

    second = store.claim("pending@example.com", 8, 19, 2026)

    assert second.outcome is ClaimOutcome.IN_PROGRESS
    assert second.claim_id is None


def test_stale_claim_is_reclaimed() -> None:
    store, client = _build_store()

    first = store.claim("stale@example.com", 8, 19, 2026)
    assert first.claim_id is not None
    assert first.lease_token is not None
    _set_claimed_at(client, "stale@example.com", 8, 19, 2026, minutes_ago=31)

    second = store.claim("stale@example.com", 8, 19, 2026)

    assert second.outcome is ClaimOutcome.CLAIMED
    assert second.claim_id == first.claim_id
    assert second.lease_token is not None
    assert second.lease_token != first.lease_token


def test_mark_operations_validate_lease_token() -> None:
    store, client = _build_store()

    original = store.claim("lease@example.com", 8, 19, 2026)
    assert original.claim_id is not None
    assert original.lease_token is not None
    _set_claimed_at(client, "lease@example.com", 8, 19, 2026, minutes_ago=31)
    reclaimed = store.claim("lease@example.com", 8, 19, 2026)
    assert reclaimed.lease_token is not None

    with pytest.raises(LeaseLostError):
        store.mark_sent(original.claim_id, original.lease_token)

    with pytest.raises(LeaseLostError):
        store.mark_failed(original.claim_id, original.lease_token)


def test_mark_sent_updates_status_and_sent_at() -> None:
    store, client = _build_store()

    claim = store.claim("sent@example.com", 8, 19, 2026)
    assert claim.claim_id is not None
    assert claim.lease_token is not None

    store.mark_sent(claim.claim_id, claim.lease_token)

    row = _get_row(client, "sent@example.com", 8, 19, 2026)
    assert row["status"] == "sent"
    assert row["sent_at"] is not None


def test_mark_failed_updates_status() -> None:
    store, client = _build_store()

    claim = store.claim("failed@example.com", 8, 19, 2026)
    assert claim.claim_id is not None
    assert claim.lease_token is not None

    store.mark_failed(claim.claim_id, claim.lease_token)

    row = _get_row(client, "failed@example.com", 8, 19, 2026)
    assert row["status"] == "failed"


def test_transaction_abort_retries_claim() -> None:
    store, client = _build_store()
    client.abort_commit_attempts = 1

    result = store.claim("retry@example.com", 8, 19, 2026)

    assert result.outcome is ClaimOutcome.CLAIMED
    rows = list(client._collection._documents.values())
    assert len(rows) == 1


def test_transaction_abort_retries_mark_sent() -> None:
    store, client = _build_store()
    claim = store.claim("mark-retry@example.com", 8, 19, 2026)
    assert claim.claim_id is not None
    assert claim.lease_token is not None
    client.abort_commit_attempts = 1

    store.mark_sent(claim.claim_id, claim.lease_token)

    row = _get_row(client, "mark-retry@example.com", 8, 19, 2026)
    assert row["status"] == "sent"


def test_missing_record_raises_lease_lost_on_transition() -> None:
    store, _client = _build_store()

    with pytest.raises(LeaseLostError):
        store.mark_failed(123456, "missing-lease")


def _build_store() -> tuple[FirestoreStateStore, FakeFirestoreClient]:
    client = FakeFirestoreClient()
    return FirestoreStateStore(30, client=client), client


def _get_row(
    client: FakeFirestoreClient,
    email_normalized: str,
    month: int,
    day: int,
    year: int,
) -> dict[str, object]:
    document_id = deterministic_document_id(email_normalized, month, day, year)
    snapshot = client.collection("birthday_sends").document(document_id).get()
    row = snapshot.to_dict()
    assert row is not None
    return row


def _set_claimed_at(
    client: FakeFirestoreClient,
    email_normalized: str,
    month: int,
    day: int,
    year: int,
    *,
    minutes_ago: int,
) -> None:
    row = _get_row(client, email_normalized, month, day, year)
    row["claimed_at"] = (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat()
    document_id = deterministic_document_id(email_normalized, month, day, year)
    current = client._collection._documents[document_id]
    client._collection._documents[document_id] = {
        "data": row,
        "version": int(current["version"]) + 1,
    }
