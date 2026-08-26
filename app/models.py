from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

from app.email_content import (
    DEFAULT_SALUTATION,
    FEMALE_SALUTATION,
    MALE_SALUTATION,
)

DeliveryRoute = Literal["standard_reminder", "bp_call_reminder"]


def build_display_name(first_name: str, last_name: str | None) -> str:
    normalized_first = _normalize_name_part(first_name)
    normalized_last = _normalize_name_part(last_name) if last_name else ""
    if not normalized_last:
        return normalized_first
    return f"{normalized_first} {normalized_last}"


def _normalize_name_part(value: str) -> str:
    return " ".join(value.split())


_FEMALE_GENDER_VALUES = frozenset({"mujer", "femenino", "f", "female"})
_MALE_GENDER_VALUES = frozenset({"hombre", "masculino", "m", "male"})


def normalize_gender(gender: str | None) -> str | None:
    if gender is None:
        return None
    normalized = gender.strip().casefold()
    return normalized or None


def resolve_salutation(gender: str | None) -> str:
    normalized = normalize_gender(gender)
    if normalized in _FEMALE_GENDER_VALUES:
        return FEMALE_SALUTATION
    if normalized in _MALE_GENDER_VALUES:
        return MALE_SALUTATION
    return DEFAULT_SALUTATION


@dataclass(frozen=True)
class Client:
    name: str
    email: str
    reminder_date: date
    row_index: int
    last_sent_year: int | None = None
    last_name: str | None = None
    gender: str | None = None
    mobile_phone: str | None = None
    service_lines: tuple[str, ...] = ()
    delivery_route: DeliveryRoute = "standard_reminder"

    @property
    def display_name(self) -> str:
        return build_display_name(self.name, self.last_name)

    @property
    def salutation(self) -> str:
        return resolve_salutation(self.gender)


@dataclass(frozen=True)
class ReminderMatch:
    client: Client
    celebrated_year: int


@dataclass(frozen=True)
class SendResult:
    client: Client
    celebrated_year: int
    success: bool
    error_message: str | None = None
