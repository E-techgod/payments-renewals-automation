from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Protocol
from urllib.parse import quote


class ClaimOutcome(str, Enum):
    CLAIMED = "claimed"
    ALREADY_SENT = "already_sent"
    IN_PROGRESS = "in_progress"


class LeaseLostError(RuntimeError):
    pass


@dataclass(frozen=True)
class ClaimResult:
    outcome: ClaimOutcome
    claim_id: int | None = None
    lease_token: str | None = None


class StateStore(Protocol):
    def claim(self, email: str, month: int, day: int, year: int) -> ClaimResult: ...

    def mark_sent(self, claim_id: int, lease_token: str) -> None: ...

    def mark_failed(self, claim_id: int, lease_token: str) -> None: ...

    def close(self) -> None: ...


def normalize_email(email: str) -> str:
    return email.strip().lower()


def utc_now_isoformat() -> str:
    return isoformat(datetime.now(UTC))


def isoformat(value: datetime) -> str:
    return value.isoformat()


def new_lease_token() -> str:
    return uuid.uuid4().hex


def deterministic_claim_id(email_normalized: str, month: int, day: int, year: int) -> int:
    raw = f"{email_normalized}\0{month}\0{day}\0{year}".encode("utf-8")
    digest = hashlib.sha256(raw).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=False) & ((1 << 63) - 1)


def deterministic_document_id(
    email_normalized: str, month: int, day: int, year: int
) -> str:
    encoded_email = quote(email_normalized, safe="")
    return f"{encoded_email}|{month}|{day}|{year}"
