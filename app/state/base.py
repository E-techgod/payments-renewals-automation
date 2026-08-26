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
    def claim(
        self,
        client_key: str,
        policy_key: str,
        renewal_date_iso: str,
        stage_name: str,
    ) -> ClaimResult: ...

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


def deterministic_claim_id(
    client_key: str,
    policy_key: str,
    renewal_date_iso: str,
    stage_name: str,
) -> int:
    raw = (
        f"{client_key}\0{policy_key}\0{renewal_date_iso}\0{stage_name}".encode("utf-8")
    )
    digest = hashlib.sha256(raw).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=False) & ((1 << 63) - 1)


def deterministic_document_id(
    client_key: str,
    policy_key: str,
    renewal_date_iso: str,
    stage_name: str,
) -> str:
    encoded_client = quote(client_key, safe="")
    encoded_policy = quote(policy_key, safe="")
    encoded_renewal = quote(renewal_date_iso, safe="")
    encoded_stage = quote(stage_name, safe="")
    return f"{encoded_client}|{encoded_policy}|{encoded_renewal}|{encoded_stage}"
