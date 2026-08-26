from __future__ import annotations

from datetime import UTC, date, datetime

from app.models import Client, PolicyRenewal
from app.reminder_config import ReminderStage
from app.reminder_rules import FixedClock, build_due_reminders, parse_reminder_date
from tests.support import build_config


def test_parse_reminder_date_supports_date_passthrough() -> None:
    renewal_date = date(2026, 9, 25)

    assert parse_reminder_date(renewal_date) == renewal_date


def test_parse_reminder_date_supports_datetime_passthrough() -> None:
    renewal_date = datetime(2026, 9, 25, 9, 30, 0, tzinfo=UTC)

    assert parse_reminder_date(renewal_date) == date(2026, 9, 25)


def test_parse_reminder_date_supports_excel_serial_number() -> None:
    assert parse_reminder_date(46290) == date(2026, 9, 25)


def test_build_due_reminders_matches_before_due_stage() -> None:
    renewal = PolicyRenewal(
        client=Client(name="Ana", email="ana@example.com"),
        policy_number="POL-123",
        renewal_date=date(2026, 9, 25),
        row_index=2,
    )
    stages = (ReminderStage("30_days", "30 days before", -30),)

    matches = build_due_reminders(renewal, stages, date(2026, 8, 26))

    assert len(matches) == 1
    assert matches[0].stage.name == "30_days"
    assert matches[0].days_remaining == 30


def test_build_due_reminders_matches_due_today_stage() -> None:
    renewal = PolicyRenewal(
        client=Client(name="Ana", email="ana@example.com"),
        policy_number="POL-123",
        renewal_date=date(2026, 8, 26),
        row_index=2,
    )

    matches = build_due_reminders(
        renewal,
        (ReminderStage("due_today", "Due today", 0),),
        date(2026, 8, 26),
    )

    assert len(matches) == 1
    assert matches[0].stage.name == "due_today"
    assert matches[0].days_remaining == 0


def test_build_clock_uses_test_date_when_present() -> None:
    clock = FixedClock(date(2026, 8, 26))

    assert clock.today() == date(2026, 8, 26)
    assert build_config(test_date=date(2026, 8, 26)).test_date == date(2026, 8, 26)
