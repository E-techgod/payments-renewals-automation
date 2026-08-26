from __future__ import annotations

from datetime import date, datetime
from io import BytesIO
from unittest.mock import Mock, patch

import pytest
from openpyxl import Workbook

from app.reminder_rules import parse_reminder_date
from app.spreadsheet.base import SpreadsheetError
from app.spreadsheet.xlsx_drive import XlsxDriveProvider
from tests.support import build_config


def test_xlsx_drive_load_rows_maps_headers_with_normalization() -> None:
    provider = XlsxDriveProvider(
        config=build_config(),
        service_factory=lambda: _FakeDriveService(
            _build_workbook_bytes(
                [" client ", "EMAIL", " policy number ", " renewal date "],
                ["Test Person", "test.person@example.com", "POL-123", "2026-09-25"],
            )
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


def test_xlsx_drive_load_rows_raises_for_missing_required_header() -> None:
    provider = XlsxDriveProvider(
        config=build_config(),
        service_factory=lambda: _FakeDriveService(
            _build_workbook_bytes(
                ["Client", "Email"],
                ["Test Person", "test.person@example.com"],
            )
        ),
    )

    with pytest.raises(
        SpreadsheetError, match="Missing required spreadsheet header: Policy Number"
    ):
        provider.load_rows()


def test_xlsx_drive_load_rows_preserves_native_renewal_datetime() -> None:
    renewal_date_value = date(2026, 9, 25)
    provider = XlsxDriveProvider(
        config=build_config(),
        service_factory=lambda: _FakeDriveService(
            _build_workbook_bytes(
                ["Client", "Email", "Policy Number", "Renewal Date"],
                ["  Test Person  ", " test.person@example.com ", "POL-123", renewal_date_value],
            )
        ),
    )

    rows = provider.load_rows()

    assert rows[0]["Client"] == "Test Person"
    assert rows[0]["Email"] == "test.person@example.com"
    assert isinstance(rows[0]["Renewal Date"], date | datetime)
    loaded_date = rows[0]["Renewal Date"]
    if isinstance(loaded_date, datetime):
        assert loaded_date.date() == renewal_date_value
    else:
        assert loaded_date == renewal_date_value
    assert parse_reminder_date(rows[0]["Renewal Date"]) == date(2026, 9, 25)


def test_xlsx_drive_load_rows_closes_workbook_on_header_resolution_failure() -> None:
    workbook = Mock()
    worksheet = Mock()
    worksheet.iter_rows.return_value = iter([("Client", "Email")])
    workbook.worksheets = [worksheet]

    provider = XlsxDriveProvider(
        config=build_config(),
        service_factory=lambda: _FakeDriveService(b"synthetic-workbook-bytes"),
    )

    with (
        patch("app.spreadsheet.xlsx_drive.load_workbook", return_value=workbook),
        pytest.raises(
            SpreadsheetError, match="Missing required spreadsheet header: Policy Number"
        ),
    ):
        provider.load_rows()

    workbook.close.assert_called_once_with()


def _build_workbook_bytes(headers: list[object], row: list[object]) -> bytes:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(headers)
    worksheet.append(row)

    buffer = BytesIO()
    workbook.save(buffer)
    workbook.close()
    return buffer.getvalue()


class _FakeDriveService:
    def __init__(self, workbook_bytes: bytes) -> None:
        self._workbook_bytes = workbook_bytes

    def files(self) -> _FakeDriveService:
        return self

    def get_media(self, *, fileId: str) -> _FakeDriveRequest:
        assert fileId == "synthetic-drive-id"
        return _FakeDriveRequest(self._workbook_bytes)


class _FakeDriveRequest:
    def __init__(self, workbook_bytes: bytes) -> None:
        self._workbook_bytes = workbook_bytes

    def execute(self) -> bytes:
        return self._workbook_bytes
