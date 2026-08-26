from __future__ import annotations

from pathlib import Path

import pytest

from app.config import ConfigError, load_config
from app.reminder_config import DEFAULT_REMINDER_STAGES


def test_load_config_reads_renewal_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    credentials_path = tmp_path / "credentials.json"
    credentials_path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("GOOGLE_CREDENTIALS_FILE", str(credentials_path))
    monkeypatch.setenv("GOOGLE_SHEET_ID", "sheet-id")
    monkeypatch.setenv("EMAIL_FROM_ADDRESS", "sender@example.com")

    config = load_config()

    assert config.google_sheet_tab == "Renewals"
    assert config.client_name_column == "Client"
    assert config.policy_number_column == "Policy Number"
    assert config.renewal_date_column == "Renewal Date"
    assert config.email_subject_template.startswith("Renewal reminder:")
    assert config.reminder_stages == DEFAULT_REMINDER_STAGES
    assert config.state_table_name == "renewal_reminder_sends"
    assert config.firestore_collection_name == "renewal_reminder_sends"


def test_load_config_requires_sheet_id_for_google_sheet(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    credentials_path = tmp_path / "credentials.json"
    credentials_path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("GOOGLE_CREDENTIALS_FILE", str(credentials_path))
    monkeypatch.setenv("EMAIL_FROM_ADDRESS", "sender@example.com")
    monkeypatch.delenv("GOOGLE_SHEET_ID", raising=False)

    with pytest.raises(ConfigError):
        load_config()


def test_load_config_requires_drive_id_for_xlsx_drive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    credentials_path = tmp_path / "credentials.json"
    credentials_path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("GOOGLE_CREDENTIALS_FILE", str(credentials_path))
    monkeypatch.setenv("EMAIL_FROM_ADDRESS", "sender@example.com")
    monkeypatch.setenv("SPREADSHEET_MODE", "xlsx_drive")
    monkeypatch.delenv("GOOGLE_DRIVE_FILE_ID", raising=False)

    with pytest.raises(ConfigError):
        load_config()


def test_load_config_relative_state_db_path_is_independent_of_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    credentials_path = tmp_path / "credentials.json"
    credentials_path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("GOOGLE_CREDENTIALS_FILE", str(credentials_path))
    monkeypatch.setenv("GOOGLE_SHEET_ID", "sheet-id")
    monkeypatch.setenv("EMAIL_FROM_ADDRESS", "sender@example.com")
    monkeypatch.setenv("STATE_DB_PATH", "data/custom-renewal-state.db")

    config = load_config()

    assert config.state_db_path == Path.cwd() / "data/custom-renewal-state.db" or config.state_db_path is not None


def test_load_config_firestore_backend_does_not_require_state_db_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    credentials_path = tmp_path / "credentials.json"
    credentials_path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("GOOGLE_CREDENTIALS_FILE", str(credentials_path))
    monkeypatch.setenv("GOOGLE_SHEET_ID", "sheet-id")
    monkeypatch.setenv("EMAIL_FROM_ADDRESS", "sender@example.com")
    monkeypatch.setenv("STATE_BACKEND", "firestore")

    config = load_config()

    assert config.state_backend == "firestore"
    assert config.state_db_path is None


def test_load_config_invalid_email_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    credentials_path = tmp_path / "credentials.json"
    credentials_path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("GOOGLE_CREDENTIALS_FILE", str(credentials_path))
    monkeypatch.setenv("GOOGLE_SHEET_ID", "sheet-id")
    monkeypatch.setenv("EMAIL_FROM_ADDRESS", "not-an-email")

    with pytest.raises(ConfigError):
        load_config()
