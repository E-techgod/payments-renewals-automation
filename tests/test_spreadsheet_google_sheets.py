from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from httplib2 import HttpLib2Error  # type: ignore[import-untyped]

from app.reminder_rules import parse_reminder_date
from app.config import Config
from app.email_content import (
    BPReminderRecipients,
    DEFAULT_BIRTHDAY_IMAGE_ALT,
    EMAIL_SUBJECT_TEMPLATE_DEFAULT,
)
from app.spreadsheet.base import SpreadsheetError
from app.spreadsheet.google_sheets import (
    GoogleSheetsProvider,
    _build_sheet_range,
)


def test_google_sheets_load_rows_maps_headers_with_normalization() -> None:
    provider = GoogleSheetsProvider(
        config=_build_config(),
        service_factory=lambda: _FakeSheetsService(
            [
                [
                    " name ",
                    "EMAIL",
                    " birthday ",
                    "Ignored",
                    " last birthday email year ",
                ],
                ["Test Person", "test.person@example.com", "2000-01-02", "x", "2025"],
            ]
        ),
    )

    rows = provider.load_rows()

    assert rows == [
        {
            "Name": "Test Person",
            "Email": "test.person@example.com",
            "Birthday": "2000-01-02",
            "Last Birthday Email Year": "2025",
        }
    ]


def test_google_sheets_load_rows_raises_for_missing_required_header() -> None:
    provider = GoogleSheetsProvider(
        config=_build_config(),
        service_factory=lambda: _FakeSheetsService(
            [["Name", "Email"], ["Test Person", "test.person@example.com"]]
        ),
    )

    with pytest.raises(
        SpreadsheetError, match="Missing required spreadsheet header: Birthday"
    ):
        provider.load_rows()


def test_google_sheets_load_rows_raises_for_duplicate_normalized_headers() -> None:
    provider = GoogleSheetsProvider(
        config=_build_config(),
        service_factory=lambda: _FakeSheetsService(
            [
                ["Name", "Email", " email ", "Birthday"],
                ["Test Person", "one@example.com", "two@example.com", "2000-01-02"],
            ]
        ),
    )

    with pytest.raises(
        SpreadsheetError,
        match=r"Duplicate spreadsheet headers after normalization: 'Email', ' email '",
    ):
        provider.load_rows()


def test_google_sheets_load_rows_allows_missing_optional_last_sent_year() -> None:
    provider = GoogleSheetsProvider(
        config=_build_config(),
        service_factory=lambda: _FakeSheetsService(
            [
                ["Name", "Email", "Birthday"],
                ["Test Person", "test.person@example.com", "2000-01-02"],
            ]
        ),
    )

    rows = provider.load_rows()

    assert rows == [
        {
            "Name": "Test Person",
            "Email": "test.person@example.com",
            "Birthday": "2000-01-02",
        }
    ]


def test_google_sheets_load_rows_preserves_numeric_birthday_serial() -> None:
    provider = GoogleSheetsProvider(
        config=_build_config(),
        service_factory=lambda: _FakeSheetsService(
            [
                ["Name", "Email", "Birthday"],
                ["  Test Person  ", " test.person@example.com ", 36526],
            ]
        ),
    )

    rows = provider.load_rows()

    assert rows[0]["Name"] == "Test Person"
    assert rows[0]["Email"] == "test.person@example.com"
    assert isinstance(rows[0]["Birthday"], int | float)
    assert rows[0]["Birthday"] == 36526
    assert parse_reminder_date(rows[0]["Birthday"]) == date(2000, 1, 1)


def test_google_sheets_load_rows_stops_at_first_row_without_name_or_last_name() -> None:
    provider = GoogleSheetsProvider(
        config=_build_config(),
        service_factory=lambda: _FakeSheetsService(
            [
                ["Name", "Last Name", "Email", "Birthday"],
                ["Test", "Person", "test.person@example.com", "2000-01-02"],
                ["", "", "", ""],
                ["Second", "Person", "second@example.com", "1999-05-06"],
            ]
        ),
    )

    rows = provider.load_rows()

    assert rows == [
        {
            "Name": "Test",
            "Last Name": "Person",
            "Email": "test.person@example.com",
            "Birthday": "2000-01-02",
        }
    ]


def test_google_sheets_fetch_values_requests_unformatted_serialized_values() -> None:
    service = _FakeSheetsService(
        [
            ["Name", "Email", "Birthday"],
            ["Test Person", "test.person@example.com", 36527],
        ]
    )
    provider = GoogleSheetsProvider(
        config=_build_config(),
        service_factory=lambda: service,
    )

    provider.load_rows()

    assert service.last_get_kwargs == {
        "spreadsheetId": "test-sheet-id",
        "range": "Birthdays!A:ZZ",
        "valueRenderOption": "UNFORMATTED_VALUE",
        "dateTimeRenderOption": "SERIAL_NUMBER",
    }


def test_build_sheet_range_quotes_tab_names_with_spaces() -> None:
    config = _build_config(google_sheet_tab="Team Birthdays")

    assert _build_sheet_range(config) == "'Team Birthdays'!A:ZZ"


def test_build_sheet_range_escapes_apostrophes_in_tab_name() -> None:
    config = _build_config(google_sheet_tab="People's Birthdays")

    assert _build_sheet_range(config) == "'People''s Birthdays'!A:ZZ"


def test_google_sheets_load_rows_retries_transport_error_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleep_calls: list[float] = []
    monkeypatch.setattr("app.retry.time.sleep", sleep_calls.append)
    service = _FakeSheetsService(
        [["Name", "Email", "Birthday"], ["Test Person", "test.person@example.com", 1]],
        execute_side_effects=[HttpLib2Error("synthetic transport failure")],
    )
    provider = GoogleSheetsProvider(
        config=_build_config(),
        service_factory=lambda: service,
    )

    rows = provider.load_rows()

    assert rows == [
        {
            "Name": "Test Person",
            "Email": "test.person@example.com",
            "Birthday": 1,
        }
    ]
    assert service.execute_calls == 2
    assert sleep_calls == [1.0]


def test_google_sheets_load_rows_retries_transport_error_until_attempts_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleep_calls: list[float] = []
    monkeypatch.setattr("app.retry.time.sleep", sleep_calls.append)
    service = _FakeSheetsService(
        [["Name", "Email", "Birthday"], ["Test Person", "test.person@example.com", 1]],
        execute_side_effects=[
            HttpLib2Error("synthetic transport failure"),
            HttpLib2Error("synthetic transport failure"),
            HttpLib2Error("synthetic transport failure"),
        ],
    )
    provider = GoogleSheetsProvider(
        config=_build_config(),
        service_factory=lambda: service,
    )

    with pytest.raises(
        SpreadsheetError, match="Sheets API request failed after retries were exhausted"
    ):
        provider.load_rows()

    assert service.execute_calls == 3
    assert sleep_calls == [1.0, 2.0]


def test_google_sheets_load_rows_wraps_timeout_after_retries_are_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleep_calls: list[float] = []
    monkeypatch.setattr("app.retry.time.sleep", sleep_calls.append)
    service = _FakeSheetsService(
        [["Name", "Email", "Birthday"], ["Test Person", "test.person@example.com", 1]],
        execute_side_effects=[
            TimeoutError("synthetic timeout"),
            TimeoutError("synthetic timeout"),
            TimeoutError("synthetic timeout"),
        ],
    )
    provider = GoogleSheetsProvider(
        config=_build_config(),
        service_factory=lambda: service,
    )

    with pytest.raises(
        SpreadsheetError, match="Sheets API request failed after retries were exhausted"
    ):
        provider.load_rows()

    assert service.execute_calls == 3
    assert sleep_calls == [1.0, 2.0]


class _FakeSheetsService:
    def __init__(
        self,
        values: list[list[object]],
        *,
        execute_side_effects: list[Exception] | None = None,
    ) -> None:
        self._values = values
        self.execute_side_effects = list(execute_side_effects or [])
        self.execute_calls = 0
        self.last_get_kwargs: dict[str, object] | None = None

    def spreadsheets(self) -> _FakeSheetsService:
        return self

    def values(self) -> _FakeSheetsService:
        return self

    def get(self, **kwargs: object) -> _FakeSheetsRequest:
        self.last_get_kwargs = kwargs
        assert kwargs["spreadsheetId"] == "test-sheet-id"
        assert kwargs["range"] == "Birthdays!A:ZZ"
        return _FakeSheetsRequest(self)


class _FakeSheetsRequest:
    def __init__(self, service: _FakeSheetsService) -> None:
        self._service = service

    def execute(self) -> dict[str, list[list[object]]]:
        self._service.execute_calls += 1
        if self._service.execute_side_effects:
            raise self._service.execute_side_effects.pop(0)
        return {"values": self._service._values}


def _build_config(*, google_sheet_tab: str = "Birthdays") -> Config:
    return Config(
        app_timezone="America/Chicago",
        dry_run=True,
        test_date=None,
        spreadsheet_mode="google_sheet",
        google_sheet_id="test-sheet-id",
        google_sheet_tab=google_sheet_tab,
        google_drive_file_id="test-drive-id",
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
