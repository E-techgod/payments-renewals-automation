from __future__ import annotations

import locale
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from app.reminder_rules import (
    FixedClock,
    SystemClock,
    build_clock,
    is_reminder_due_today,
    parse_reminder_date,
)
from app.config import Config
from app.email_content import (
    BPReminderRecipients,
    DEFAULT_BIRTHDAY_IMAGE_ALT,
    EMAIL_SUBJECT_TEMPLATE_DEFAULT,
)
from app.models import ReminderMatch, Client, SendResult


def test_parse_reminder_date_supports_date_passthrough() -> None:
    reminder_date = date(2000, 1, 1)

    assert parse_reminder_date(reminder_date) == reminder_date


def test_parse_reminder_date_supports_datetime_passthrough() -> None:
    reminder_date = datetime(2000, 1, 1, 9, 30, 0, tzinfo=UTC)

    assert parse_reminder_date(reminder_date) == date(2000, 1, 1)


def test_parse_reminder_date_supports_excel_serial() -> None:
    assert parse_reminder_date(36526) == date(2000, 1, 1)


@pytest.mark.parametrize("raw_value", [float("nan"), float("inf"), float("-inf")])
def test_parse_reminder_date_returns_none_for_non_finite_excel_serials(
    raw_value: float,
) -> None:
    assert parse_reminder_date(raw_value) is None


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        ("01/02/2000", date(2000, 1, 2)),
        ("1/2/2000", date(2000, 1, 2)),
        ("2000-01-02", date(2000, 1, 2)),
        ("May 27, 2003", date(2003, 5, 27)),
        ("December 1, 1999", date(1999, 12, 1)),
    ],
)
def test_parse_reminder_date_supports_string_formats(raw_value: str, expected: date) -> None:
    assert parse_reminder_date(raw_value) == expected


def test_parse_reminder_date_returns_none_for_malformed_string() -> None:
    assert parse_reminder_date("definitely-not-a-date") is None


@pytest.mark.parametrize("raw_value", ["1/2/03", "13/45/2000", "February 30, 2000"])
def test_parse_reminder_date_returns_none_for_invalid_date_strings(raw_value: str) -> None:
    assert parse_reminder_date(raw_value) is None


def test_parse_reminder_date_long_month_parser_is_locale_independent() -> None:
    original_locale = locale.setlocale(locale.LC_TIME)
    target_locale = None
    for candidate in ("es_ES.UTF-8", "fr_FR.UTF-8", "de_DE.UTF-8"):
        try:
            locale.setlocale(locale.LC_TIME, candidate)
        except locale.Error:
            continue
        target_locale = candidate
        break

    if target_locale is None:
        pytest.skip("No non-English LC_TIME locale is installed on this machine")

    try:
        assert parse_reminder_date("May 27, 2003") == date(2003, 5, 27)
    finally:
        locale.setlocale(locale.LC_TIME, original_locale)


def test_parse_reminder_date_returns_none_for_missing_value() -> None:
    assert parse_reminder_date(None) is None


def test_is_reminder_due_today_matches_feb_29_on_leap_day() -> None:
    assert is_reminder_due_today(date(2000, 2, 29), date(2024, 2, 29))


def test_is_reminder_due_today_matches_feb_29_on_feb_28_in_non_leap_year() -> None:
    assert is_reminder_due_today(date(2000, 2, 29), date(2023, 2, 28))


def test_is_reminder_due_today_does_not_match_feb_29_on_feb_28_in_leap_year() -> None:
    assert not is_reminder_due_today(date(2000, 2, 29), date(2024, 2, 28))


def test_is_reminder_due_today_matches_only_exact_date_for_normal_reminder_dates() -> None:
    reminder_date = date(2000, 1, 2)

    assert is_reminder_due_today(reminder_date, date(2026, 1, 2))
    assert not is_reminder_due_today(reminder_date, date(2026, 1, 1))
    assert not is_reminder_due_today(reminder_date, date(2026, 1, 3))


def test_fixed_clock_returns_exact_fixed_date() -> None:
    clock = FixedClock(date(2026, 8, 19))

    assert clock.today() == date(2026, 8, 19)


def test_build_clock_uses_fixed_clock_when_test_date_is_set() -> None:
    config = _build_config(test_date=date(2026, 8, 19))

    clock = build_clock(config)

    assert isinstance(clock, FixedClock)
    assert clock.today() == date(2026, 8, 19)


def test_build_clock_uses_system_clock_when_test_date_is_not_set() -> None:
    config = _build_config(test_date=None)

    clock = build_clock(config)

    assert isinstance(clock, SystemClock)
    assert isinstance(clock.today(), date)


def test_domain_models_store_expected_fields() -> None:
    client = Client(
        name="Test Person",
        email="test.person@example.com",
        reminder_date=date(2000, 1, 1),
        row_index=7,
        last_sent_year=2025,
    )
    reminder_match = ReminderMatch(client=client, celebrated_year=2026)
    send_result = SendResult(
        client=client,
        celebrated_year=2026,
        success=False,
        error_message="synthetic failure",
    )

    assert reminder_match.client is client
    assert reminder_match.celebrated_year == 2026
    assert send_result.client is client
    assert not send_result.success
    assert send_result.error_message == "synthetic failure"


def test_client_display_name_combines_first_and_last_name() -> None:
    client = Client(
        name="Test",
        email="test.person@example.com",
        reminder_date=date(2000, 1, 1),
        row_index=7,
        last_name="Person",
    )

    assert client.display_name == "Test Person"


def test_client_display_name_falls_back_to_first_name_when_last_name_missing() -> None:
    client = Client(
        name="Test",
        email="test.person@example.com",
        reminder_date=date(2000, 1, 1),
        row_index=7,
        last_name=None,
    )

    assert client.display_name == "Test"


@pytest.mark.parametrize(
    ("name", "last_name", "expected"),
    [
        ("  Test  ", "  Person  ", "Test Person"),
        ("Test\tPerson", None, "Test Person"),
        ("Mary  Ann", "Van  Buren", "Mary Ann Van Buren"),
        ("Test", "   ", "Test"),
    ],
)
def test_client_display_name_normalizes_whitespace(
    name: str, last_name: str | None, expected: str
) -> None:
    client = Client(
        name=name,
        email="test.person@example.com",
        reminder_date=date(2000, 1, 1),
        row_index=7,
        last_name=last_name,
    )

    assert client.display_name == expected


def _build_config(test_date: date | None) -> Config:
    return Config(
        app_timezone="America/Chicago",
        dry_run=True,
        test_date=test_date,
        spreadsheet_mode="google_sheet",
        google_sheet_id="synthetic-sheet-id",
        google_sheet_tab="SyntheticTab",
        google_drive_file_id="",
        name_column="Name",
        last_name_column="Last Name",
        gender_column="Gender",
        service_line_column="Línea de servicio",
        mobile_phone_column="Móvil",
        email_column="Email",
        birthday_column="Birthday",
        last_sent_year_column="Last Birthday Email Year",
        email_provider="gmail",
        email_from_name="Test Sender",
        email_from_address="sender@example.com",
        email_subject_template=EMAIL_SUBJECT_TEMPLATE_DEFAULT,
        google_auth_mode="service_account",
        google_credentials_file=Path("synthetic-credentials.json"),
        google_impersonate_subject="sender@example.com",
        google_oauth_client_secrets_file=None,
        google_oauth_token_file=Path("synthetic-oauth-token.json"),
        google_oauth_token_persist=True,
        birthday_image_mode="none",
        birthday_image_path=Path("synthetic-banner.png"),
        birthday_image_url="",
        birthday_image_alt=DEFAULT_BIRTHDAY_IMAGE_ALT,
        birthday_image_width=600,
        state_backend="sqlite",
        state_db_path=Path("synthetic-state.db"),
        firestore_database="birthday-automation",
        stale_claim_timeout_minutes=30,
        retry_max_attempts=3,
        retry_base_delay_seconds=1.0,
        log_level="INFO",
        bp_reminder_recipients=BPReminderRecipients(),
    )
