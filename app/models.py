from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

from app.reminder_config import ReminderStage

DeliveryStatus = Literal["pending", "sent", "failed"]


def build_display_name(first_name: str, last_name: str | None) -> str:
    normalized_first = _normalize_name_part(first_name)
    normalized_last = _normalize_name_part(last_name) if last_name else ""
    if not normalized_last:
        return normalized_first
    return f"{normalized_first} {normalized_last}"


def _normalize_name_part(value: str) -> str:
    return " ".join(value.split())


@dataclass(frozen=True)
class Client:
    name: str
    email: str
    last_name: str | None = None
    mobile_phone: str | None = None
    service_lines: tuple[str, ...] = ()

    @property
    def display_name(self) -> str:
        return build_display_name(self.name, self.last_name)


@dataclass(frozen=True)
class PolicyRenewal:
    client: Client
    policy_number: str
    renewal_date: date
    row_index: int


@dataclass(frozen=True)
class ReminderMatch:
    renewal: PolicyRenewal
    stage: ReminderStage
    reminder_due_date: date
    days_remaining: int


@dataclass(frozen=True)
class SendResult:
    match: ReminderMatch
    success: bool
    error_message: str | None = None
