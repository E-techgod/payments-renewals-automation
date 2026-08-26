from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from app.config import Config

_EXCEL_EPOCH = date(1899, 12, 30)
_MONTH_DAY_YEAR_PATTERN = re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$")
_LONG_MONTH_PATTERN = re.compile(r"^([A-Za-z]+) (\d{1,2}), (\d{4})$")
_ENGLISH_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


def parse_reminder_date(raw: object) -> date | None:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    if isinstance(raw, int | float) and not isinstance(raw, bool):
        return _parse_excel_serial(raw)
    if isinstance(raw, str):
        return _parse_reminder_date_string(raw)
    return None


def is_reminder_due_today(reminder_date: date, today: date) -> bool:
    scheduled_date = _scheduled_date_for_year(reminder_date, today.year)
    return (scheduled_date.month, scheduled_date.day) == (today.month, today.day)


class Clock(Protocol):
    def today(self) -> date: ...


@dataclass(frozen=True)
class SystemClock:
    timezone: ZoneInfo

    def today(self) -> date:
        return datetime.now(self.timezone).date()


@dataclass(frozen=True)
class FixedClock:
    fixed_today: date

    def today(self) -> date:
        return self.fixed_today


def build_clock(config: Config) -> Clock:
    if config.test_date is not None:
        return FixedClock(config.test_date)
    return SystemClock(ZoneInfo(config.app_timezone))


def _parse_excel_serial(raw: float) -> date | None:
    if not math.isfinite(raw):
        return None

    try:
        return _EXCEL_EPOCH + timedelta(days=float(raw))
    except (OverflowError, ValueError):
        return None


def _parse_reminder_date_string(raw: str) -> date | None:
    value = raw.strip()
    if not value:
        return None

    parsed_month_day_year = _parse_month_day_year_string(value)
    if parsed_month_day_year is not None:
        return parsed_month_day_year

    for parser in (date.fromisoformat, _parse_long_month_string):
        try:
            return parser(value)
        except ValueError:
            continue
    return None


def _parse_month_day_year_string(value: str) -> date | None:
    if _MONTH_DAY_YEAR_PATTERN.fullmatch(value) is None:
        return None

    parts = value.split("/")

    try:
        month = int(parts[0])
        day = int(parts[1])
        year = int(parts[2])
    except ValueError:
        return None

    try:
        return date(year, month, day)
    except ValueError:
        return None


def _parse_long_month_string(value: str) -> date:
    match = _LONG_MONTH_PATTERN.fullmatch(value)
    if match is None:
        raise ValueError(value)

    month_name, day_text, year_text = match.groups()
    month = _ENGLISH_MONTHS.get(month_name.casefold())
    if month is None:
        raise ValueError(value)

    day = int(day_text)
    year = int(year_text)
    return date(year, month, day)


def _scheduled_date_for_year(reminder_date: date, year: int) -> date:
    if reminder_date.month == 2 and reminder_date.day == 29:
        if _is_leap_year(year):
            return date(year, 2, 29)
        return date(year, 2, 28)
    return date(year, reminder_date.month, reminder_date.day)


def _is_leap_year(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
