from __future__ import annotations

from datetime import date

import pytest
from httplib2 import HttpLib2Error  # type: ignore[import-untyped]

from app.reminder_rules import parse_reminder_date
from app.spreadsheet.base import SpreadsheetError
from app.spreadsheet.google_sheets import GoogleSheetsProvider, _build_sheet_range
from tests.support import build_config


def test_google_sheets_load_rows_maps_headers_with_normalization() -> None:
    provider = GoogleSheetsProvider(
        config=build_config(),
        service_factory=lambda: _FakeSheetsService(
            [
                [
                    " client ",
                    "EMAIL",
                    " policy number ",
                    " renewal date ",
                ],
                ["Test Person", "test.person@example.com", "POL-123", "2026-09-25"],
            ]
        ),
    )

    rows = provider.load_rows()

    assert rows == [
        {
            "Client": "Test Person",
            "Email": "test.person@example.com",
            "Policy Number": "POL-123",
            "Renewal Date": "2026-09-25",
        }
    ]


def test_google_sheets_load_rows_raises_for_missing_required_header() -> None:
    provider = GoogleSheetsProvider(
        config=build_config(),
        service_factory=lambda: _FakeSheetsService(
            [["Client", "Email"], ["Test Person", "test.person@example.com"]]
        ),
    )

    with pytest.raises(
        SpreadsheetError, match="Missing required spreadsheet header: Policy Number"
    ):
        provider.load_rows()


def test_google_sheets_load_rows_preserves_numeric_renewal_serial() -> None:
    provider = GoogleSheetsProvider(
        config=build_config(),
        service_factory=lambda: _FakeSheetsService(
            [
                ["Client", "Email", "Policy Number", "Renewal Date"],
                ["  Test Person  ", " test.person@example.com ", "POL-123", 46290],
            ]
        ),
    )

    rows = provider.load_rows()

    assert rows[0]["Client"] == "Test Person"
    assert rows[0]["Email"] == "test.person@example.com"
    assert isinstance(rows[0]["Renewal Date"], int | float)
    assert parse_reminder_date(rows[0]["Renewal Date"]) == date(2026, 9, 25)


def test_build_sheet_range_quotes_tab_names_with_spaces() -> None:
    config = build_config()
    config = config.__class__(**{**config.__dict__, "google_sheet_tab": "Team Renewals"})

    assert _build_sheet_range(config) == "'Team Renewals'!A:ZZ"


def test_google_sheets_load_rows_retries_transport_error_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleep_calls: list[float] = []
    monkeypatch.setattr("app.retry.time.sleep", sleep_calls.append)
    service = _FakeSheetsService(
        [
            ["Client", "Email", "Policy Number", "Renewal Date"],
            ["Test Person", "test.person@example.com", "POL-123", 1],
        ],
        execute_side_effects=[HttpLib2Error("synthetic transport failure")],
    )
    provider = GoogleSheetsProvider(
        config=build_config(),
        service_factory=lambda: service,
    )

    rows = provider.load_rows()

    assert rows == [
        {
            "Client": "Test Person",
            "Email": "test.person@example.com",
            "Policy Number": "POL-123",
            "Renewal Date": 1,
        }
    ]
    assert service.execute_calls == 2
    assert sleep_calls == [0.25]


class _FakeSheetsService:
    def __init__(
        self,
        rows: list[list[object]],
        *,
        execute_side_effects: list[Exception] | None = None,
    ) -> None:
        self._rows = rows
        self.execute_side_effects = list(execute_side_effects or [])
        self.execute_calls = 0
        self.last_get_kwargs: dict[str, object] | None = None

    def spreadsheets(self) -> _FakeSheetsService:
        return self

    def values(self) -> _FakeSheetsService:
        return self

    def get(self, **kwargs: object) -> _FakeSheetsService:
        self.last_get_kwargs = dict(kwargs)
        return self

    def execute(self) -> dict[str, object]:
        self.execute_calls += 1
        if self.execute_side_effects:
            raise self.execute_side_effects.pop(0)
        return {"values": self._rows}
