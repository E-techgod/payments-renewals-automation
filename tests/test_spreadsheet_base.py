from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Config
from app.email_content import (
    BPReminderRecipients,
    DEFAULT_BIRTHDAY_IMAGE_ALT,
    EMAIL_SUBJECT_TEMPLATE_DEFAULT,
)
from app.spreadsheet.base import SpreadsheetError, build_row_dict, resolve_headers


def test_resolve_headers_raises_for_colliding_configured_columns() -> None:
    config = _build_config(birthday_column=" email ")

    with pytest.raises(
        SpreadsheetError,
        match=r"Configured spreadsheet columns collide after normalization: 'Email' and ' email '",
    ):
        resolve_headers(["Name", "Email", "Birthday"], config)


def test_resolve_headers_includes_last_name_when_present() -> None:
    config = _build_config()

    resolved = resolve_headers(["Name", "Last Name", "Email", "Birthday"], config)

    assert resolved["Last Name"] == 1


def test_resolve_headers_omits_last_name_when_absent() -> None:
    config = _build_config()

    resolved = resolve_headers(["Name", "Email", "Birthday"], config)

    assert "Last Name" not in resolved


def test_resolve_headers_includes_gender_when_present() -> None:
    config = _build_config()

    resolved = resolve_headers(["Name", "Gender", "Email", "Birthday"], config)

    assert resolved["Gender"] == 1


def test_resolve_headers_includes_service_line_and_mobile_when_present() -> None:
    config = _build_config()

    resolved = resolve_headers(
        ["Name", "Línea de servicio", "Móvil", "Email", "Birthday"], config
    )

    assert resolved["Línea de servicio"] == 1
    assert resolved["Móvil"] == 2


def test_resolve_headers_matches_service_line_without_diacritics() -> None:
    config = _build_config()

    resolved = resolve_headers(
        ["Name", "Linea de Servicio", "Movil", "Email", "Birthday"], config
    )

    assert resolved["Línea de servicio"] == 1
    assert resolved["Móvil"] == 2


def test_resolve_headers_omits_gender_when_absent() -> None:
    config = _build_config()

    resolved = resolve_headers(["Name", "Email", "Birthday"], config)

    assert "Gender" not in resolved


def test_build_row_dict_preserves_optional_service_line_and_mobile_values() -> None:
    config = _build_config()
    resolved = resolve_headers(
        ["Name", "Linea de Servicio", "Movil", "Email", "Birthday"], config
    )

    row = build_row_dict(
        ["Test Person", "BP", "5551234", "test.person@example.com", "1/1/2000"],
        resolved,
    )

    assert row["Línea de servicio"] == "BP"
    assert row["Móvil"] == "5551234"


def _build_config(*, birthday_column: str = "Birthday") -> Config:
    return Config(
        app_timezone="America/Chicago",
        dry_run=True,
        test_date=None,
        spreadsheet_mode="google_sheet",
        google_sheet_id="test-sheet-id",
        google_sheet_tab="Birthdays",
        google_drive_file_id="test-drive-id",
        name_column="Name",
        last_name_column="Last Name",
        gender_column="Gender",
        service_line_column="Línea de servicio",
        mobile_phone_column="Móvil",
        email_column="Email",
        birthday_column=birthday_column,
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
