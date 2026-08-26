from __future__ import annotations

import pytest

from app.spreadsheet.base import SpreadsheetError, build_row_dict, resolve_headers
from tests.support import build_config


def test_resolve_headers_raises_for_colliding_configured_columns() -> None:
    config = build_config()
    config = config.__class__(**{**config.__dict__, "renewal_date_column": " email "})

    with pytest.raises(
        SpreadsheetError,
        match=r"Configured spreadsheet columns collide after normalization: 'Email' and ' email '",
    ):
        resolve_headers(["Client", "Email", "Policy Number", "Renewal Date"], config)


def test_resolve_headers_includes_optional_columns_when_present() -> None:
    config = build_config()

    resolved = resolve_headers(
        ["Client", "Last Name", "Service Line", "Mobile Phone", "Email", "Policy Number", "Renewal Date"],
        config,
    )

    assert resolved["Last Name"] == 1
    assert resolved["Service Line"] == 2
    assert resolved["Mobile Phone"] == 3


def test_build_row_dict_preserves_optional_values() -> None:
    config = build_config()
    resolved = resolve_headers(
        ["Client", "Service Line", "Mobile Phone", "Email", "Policy Number", "Renewal Date"],
        config,
    )

    row = build_row_dict(
        ["Test Person", "Vida", "5551234", "test.person@example.com", "POL-123", "2026-09-25"],
        resolved,
    )

    assert row["Service Line"] == "Vida"
    assert row["Mobile Phone"] == "5551234"
