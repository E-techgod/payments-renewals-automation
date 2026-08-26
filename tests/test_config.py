from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

import app.config as config_module
from app.config import (
    _DEFAULT_BIRTHDAY_IMAGE_PATH,
    _DEFAULT_FIRESTORE_DATABASE,
    _DEFAULT_GOOGLE_OAUTH_TOKEN_PATH,
    _DEFAULT_STATE_DB_PATH,
    ConfigError,
    load_config,
)
from app.email_content import (
    BPReminderRecipients,
    DEFAULT_BIRTHDAY_IMAGE_ALT,
    EMAIL_SUBJECT_TEMPLATE_DEFAULT,
)


def test_load_config_valid_google_sheet_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    image_path, credentials_path = _create_files(tmp_path)
    _set_base_env(monkeypatch, image_path, credentials_path)
    monkeypatch.setenv("SPREADSHEET_MODE", "google_sheet")
    monkeypatch.setenv("GOOGLE_SHEET_ID", "sheet-123")
    monkeypatch.setenv("GOOGLE_SHEET_TAB", "Birthdays")

    config = load_config()

    assert config.spreadsheet_mode == "google_sheet"
    assert config.google_sheet_id == "sheet-123"
    assert config.google_sheet_tab == "Birthdays"
    assert config.google_impersonate_subject == "sender@example.com"


def test_load_config_valid_xlsx_drive_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    image_path, credentials_path = _create_files(tmp_path)
    _set_base_env(monkeypatch, image_path, credentials_path)
    monkeypatch.setenv("SPREADSHEET_MODE", "xlsx_drive")
    monkeypatch.setenv("GOOGLE_DRIVE_FILE_ID", "drive-123")

    config = load_config()

    assert config.spreadsheet_mode == "xlsx_drive"
    assert config.google_drive_file_id == "drive-123"


def test_load_config_gender_column_defaults_to_gender(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    image_path, credentials_path = _create_files(tmp_path)
    _set_base_env(monkeypatch, image_path, credentials_path)
    monkeypatch.delenv("GENDER_COLUMN", raising=False)

    config = load_config()

    assert config.gender_column == "Gender"


def test_load_config_gender_column_uses_configured_value(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    image_path, credentials_path = _create_files(tmp_path)
    _set_base_env(monkeypatch, image_path, credentials_path)
    monkeypatch.setenv("GENDER_COLUMN", "Género")

    config = load_config()

    assert config.gender_column == "Género"


def test_load_config_service_line_column_defaults_to_linea_de_servicio(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    image_path, credentials_path = _create_files(tmp_path)
    _set_base_env(monkeypatch, image_path, credentials_path)
    monkeypatch.delenv("SERVICE_LINE_COLUMN", raising=False)

    config = load_config()

    assert config.service_line_column == "Línea de servicio"


def test_load_config_mobile_phone_column_defaults_to_movil(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    image_path, credentials_path = _create_files(tmp_path)
    _set_base_env(monkeypatch, image_path, credentials_path)
    monkeypatch.delenv("MOBILE_PHONE_COLUMN", raising=False)

    config = load_config()

    assert config.mobile_phone_column == "Móvil"


def test_load_config_service_and_mobile_columns_use_configured_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    image_path, credentials_path = _create_files(tmp_path)
    _set_base_env(monkeypatch, image_path, credentials_path)
    monkeypatch.setenv("SERVICE_LINE_COLUMN", "Service Line")
    monkeypatch.setenv("MOBILE_PHONE_COLUMN", "Mobile")

    config = load_config()

    assert config.service_line_column == "Service Line"
    assert config.mobile_phone_column == "Mobile"


@pytest.mark.parametrize(
    ("mode", "missing_var"),
    [
        ("google_sheet", "GOOGLE_SHEET_ID"),
        ("xlsx_drive", "GOOGLE_DRIVE_FILE_ID"),
    ],
)
def test_load_config_missing_required_spreadsheet_id_raises(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    mode: str,
    missing_var: str,
) -> None:
    image_path, credentials_path = _create_files(tmp_path)
    _set_base_env(monkeypatch, image_path, credentials_path)
    monkeypatch.setenv("SPREADSHEET_MODE", mode)
    monkeypatch.delenv(missing_var, raising=False)

    with pytest.raises(ConfigError):
        load_config()


def test_load_config_invalid_email_from_address_raises(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    image_path, credentials_path = _create_files(tmp_path)
    _set_base_env(monkeypatch, image_path, credentials_path)
    monkeypatch.setenv("EMAIL_FROM_ADDRESS", "not-an-email")

    with pytest.raises(ConfigError):
        load_config()


def test_load_config_invalid_google_impersonate_subject_raises(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    image_path, credentials_path = _create_files(tmp_path)
    _set_base_env(monkeypatch, image_path, credentials_path)
    monkeypatch.setenv("GOOGLE_IMPERSONATE_SUBJECT", "not-an-email")

    with pytest.raises(
        ConfigError,
        match="GOOGLE_IMPERSONATE_SUBJECT must be a valid email address",
    ):
        load_config()


def test_load_config_bp_reminder_cc_defaults_to_empty_tuple(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    image_path, credentials_path = _create_files(tmp_path)
    _set_base_env(monkeypatch, image_path, credentials_path)
    monkeypatch.delenv("BP_REMINDER_CC", raising=False)

    config = load_config()

    assert config.bp_reminder_recipients == BPReminderRecipients()


def test_load_config_bp_reminder_to_address_uses_configured_value(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    image_path, credentials_path = _create_files(tmp_path)
    _set_base_env(monkeypatch, image_path, credentials_path)
    monkeypatch.setenv(
        "BP_REMINDER_TO_ADDRESS_DEFAULT", "lead.bp@quirongroup.com"
    )

    config = load_config()

    assert config.bp_reminder_recipients == BPReminderRecipients(
        to_email="lead.bp@quirongroup.com"
    )


def test_load_config_bp_reminder_to_address_rejects_invalid_email(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    image_path, credentials_path = _create_files(tmp_path)
    _set_base_env(monkeypatch, image_path, credentials_path)
    monkeypatch.setenv("BP_REMINDER_TO_ADDRESS_DEFAULT", "not-an-email")

    with pytest.raises(
        ConfigError,
        match="BP_REMINDER_TO_ADDRESS_DEFAULT must be a valid email address",
    ):
        load_config()


def test_load_config_bp_reminder_cc_accepts_single_address(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    image_path, credentials_path = _create_files(tmp_path)
    _set_base_env(monkeypatch, image_path, credentials_path)
    monkeypatch.setenv("BP_REMINDER_CC", "manager1@quirongroup.com")

    config = load_config()

    assert config.bp_reminder_recipients == BPReminderRecipients(
        cc_emails=("manager1@quirongroup.com",)
    )


def test_load_config_bp_reminder_cc_accepts_multiple_addresses(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    image_path, credentials_path = _create_files(tmp_path)
    _set_base_env(monkeypatch, image_path, credentials_path)
    monkeypatch.setenv(
        "BP_REMINDER_CC",
        "manager1@quirongroup.com,manager2@quirongroup.com",
    )

    config = load_config()

    assert config.bp_reminder_recipients == BPReminderRecipients(
        cc_emails=("manager1@quirongroup.com", "manager2@quirongroup.com")
    )


def test_load_config_bp_reminder_cc_trims_whitespace_and_drops_blank_entries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    image_path, credentials_path = _create_files(tmp_path)
    _set_base_env(monkeypatch, image_path, credentials_path)
    monkeypatch.setenv(
        "BP_REMINDER_CC",
        " manager1@quirongroup.com , , manager2@quirongroup.com  , ",
    )

    config = load_config()

    assert config.bp_reminder_recipients == BPReminderRecipients(
        cc_emails=("manager1@quirongroup.com", "manager2@quirongroup.com")
    )


def test_load_config_bp_reminder_cc_deduplicates_addresses_case_insensitively(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    image_path, credentials_path = _create_files(tmp_path)
    _set_base_env(monkeypatch, image_path, credentials_path)
    monkeypatch.setenv(
        "BP_REMINDER_CC",
        "manager1@quirongroup.com, MANAGER1@QUIRONGROUP.COM, manager2@quirongroup.com",
    )

    config = load_config()

    assert config.bp_reminder_recipients == BPReminderRecipients(
        cc_emails=("manager1@quirongroup.com", "manager2@quirongroup.com")
    )


def test_load_config_bp_reminder_cc_rejects_invalid_address(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    image_path, credentials_path = _create_files(tmp_path)
    _set_base_env(monkeypatch, image_path, credentials_path)
    monkeypatch.setenv("BP_REMINDER_CC", "manager1@quirongroup.com, not-an-email")

    with pytest.raises(
        ConfigError,
        match="BP_REMINDER_CC must be a valid email address",
    ):
        load_config()


def test_load_config_invalid_app_timezone_raises(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    image_path, credentials_path = _create_files(tmp_path)
    _set_base_env(monkeypatch, image_path, credentials_path)
    monkeypatch.setenv("APP_TIMEZONE", "Not/ARealZone")

    with pytest.raises(
        ConfigError,
        match=r"APP_TIMEZONE is not a valid IANA timezone: 'Not/ARealZone'",
    ):
        load_config()


def test_load_config_valid_app_timezone_passes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    image_path, credentials_path = _create_files(tmp_path)
    _set_base_env(monkeypatch, image_path, credentials_path)
    monkeypatch.setenv("APP_TIMEZONE", "America/Chicago")

    config = load_config()

    assert config.app_timezone == "America/Chicago"


def test_load_config_missing_local_birthday_image_raises(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _, credentials_path = _create_files(tmp_path)
    missing_image_path = tmp_path / "missing.png"
    _set_base_env(monkeypatch, missing_image_path, credentials_path)

    with pytest.raises(ConfigError):
        load_config()


def test_load_config_without_birthday_image_mode_env_uses_local_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    image_path, credentials_path = _create_files(tmp_path)
    _set_base_env(monkeypatch, image_path, credentials_path)
    monkeypatch.delenv("BIRTHDAY_IMAGE_MODE", raising=False)

    config = load_config()

    assert config.birthday_image_mode == "local"
    assert config.birthday_image_path == image_path


def test_load_config_invalid_email_provider_raises(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    image_path, credentials_path = _create_files(tmp_path)
    _set_base_env(monkeypatch, image_path, credentials_path)
    monkeypatch.setenv("EMAIL_PROVIDER", "smtp")

    with pytest.raises(ConfigError):
        load_config()


def test_load_config_non_https_birthday_image_url_raises(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    image_path, credentials_path = _create_files(tmp_path)
    _set_base_env(monkeypatch, image_path, credentials_path)
    monkeypatch.setenv("BIRTHDAY_IMAGE_MODE", "url")
    monkeypatch.setenv("BIRTHDAY_IMAGE_URL", "http://example.com/banner.png")

    with pytest.raises(ConfigError):
        load_config()


def test_load_config_non_positive_birthday_image_width_raises(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    image_path, credentials_path = _create_files(tmp_path)
    _set_base_env(monkeypatch, image_path, credentials_path)
    monkeypatch.setenv("BIRTHDAY_IMAGE_WIDTH", "0")

    with pytest.raises(ConfigError):
        load_config()


def test_load_config_malformed_test_date_raises(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    image_path, credentials_path = _create_files(tmp_path)
    _set_base_env(monkeypatch, image_path, credentials_path)
    monkeypatch.setenv("TEST_DATE", "2026-2-30")

    with pytest.raises(ConfigError):
        load_config()


def test_load_config_valid_test_date_parses_correctly(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    image_path, credentials_path = _create_files(tmp_path)
    _set_base_env(monkeypatch, image_path, credentials_path)
    monkeypatch.setenv("TEST_DATE", "2026-08-19")

    config = load_config()

    assert config.test_date == date(2026, 8, 19)


def test_load_config_invalid_dry_run_value_raises(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    image_path, credentials_path = _create_files(tmp_path)
    _set_base_env(monkeypatch, image_path, credentials_path)
    monkeypatch.setenv("DRY_RUN", "yes")

    with pytest.raises(ConfigError):
        load_config()


def test_load_config_missing_google_credentials_file_raises(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    image_path, _ = _create_files(tmp_path)
    missing_credentials = tmp_path / "missing-credentials.json"
    _set_base_env(monkeypatch, image_path, missing_credentials)

    with pytest.raises(ConfigError):
        load_config()


def test_load_config_defaults_to_service_account_auth_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    image_path, credentials_path = _create_files(tmp_path)
    _set_base_env(monkeypatch, image_path, credentials_path)
    monkeypatch.delenv("GOOGLE_AUTH_MODE", raising=False)

    config = load_config()

    assert config.google_auth_mode == "service_account"
    assert config.google_credentials_file == credentials_path
    assert config.google_oauth_client_secrets_file is None


def test_load_config_invalid_google_auth_mode_raises(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    image_path, credentials_path = _create_files(tmp_path)
    _set_base_env(monkeypatch, image_path, credentials_path)
    monkeypatch.setenv("GOOGLE_AUTH_MODE", "api_key")

    with pytest.raises(
        ConfigError,
        match="GOOGLE_AUTH_MODE must be 'service_account' or 'oauth'",
    ):
        load_config()


def test_load_config_oauth_mode_requires_client_secrets_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    image_path, credentials_path = _create_files(tmp_path)
    _set_base_env(monkeypatch, image_path, credentials_path)
    monkeypatch.setenv("GOOGLE_AUTH_MODE", "oauth")
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRETS_FILE", raising=False)

    with pytest.raises(ConfigError):
        load_config()


def test_load_config_oauth_mode_does_not_require_credentials_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    image_path, credentials_path = _create_files(tmp_path)
    _set_base_env(monkeypatch, image_path, credentials_path)
    monkeypatch.setenv("GOOGLE_AUTH_MODE", "oauth")
    monkeypatch.delenv("GOOGLE_CREDENTIALS_FILE", raising=False)
    client_secrets_path = tmp_path / "client-secrets.json"
    client_secrets_path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv(
        "GOOGLE_OAUTH_CLIENT_SECRETS_FILE", str(client_secrets_path)
    )

    config = load_config()

    assert config.google_auth_mode == "oauth"
    assert config.google_credentials_file is None
    assert config.google_oauth_client_secrets_file == client_secrets_path


def test_load_config_oauth_mode_missing_client_secrets_file_raises(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    image_path, credentials_path = _create_files(tmp_path)
    _set_base_env(monkeypatch, image_path, credentials_path)
    monkeypatch.setenv("GOOGLE_AUTH_MODE", "oauth")
    missing_client_secrets = tmp_path / "missing-client-secrets.json"
    monkeypatch.setenv(
        "GOOGLE_OAUTH_CLIENT_SECRETS_FILE", str(missing_client_secrets)
    )

    with pytest.raises(ConfigError):
        load_config()


def test_load_config_default_google_oauth_token_path_is_independent_of_cwd(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    image_path, credentials_path = _create_files(tmp_path)
    _set_base_env(monkeypatch, image_path, credentials_path)
    monkeypatch.delenv("GOOGLE_OAUTH_TOKEN_FILE", raising=False)
    monkeypatch.chdir(tmp_path)

    config = load_config()

    assert config.google_oauth_token_file == _DEFAULT_GOOGLE_OAUTH_TOKEN_PATH


def test_load_config_relative_google_oauth_token_path_is_independent_of_cwd(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    image_path, credentials_path = _create_files(tmp_path)
    _set_base_env(monkeypatch, image_path, credentials_path)
    monkeypatch.setenv("GOOGLE_OAUTH_TOKEN_FILE", "data/google_oauth_token.json")
    monkeypatch.chdir(tmp_path)

    config = load_config()

    assert config.google_oauth_token_file == _DEFAULT_GOOGLE_OAUTH_TOKEN_PATH


def test_load_config_oauth_token_persist_defaults_true(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    image_path, credentials_path = _create_files(tmp_path)
    _set_base_env(monkeypatch, image_path, credentials_path)
    monkeypatch.delenv("GOOGLE_OAUTH_TOKEN_PERSIST", raising=False)

    config = load_config()

    assert config.google_oauth_token_persist is True


def test_load_config_oauth_token_persist_accepts_false(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    image_path, credentials_path = _create_files(tmp_path)
    _set_base_env(monkeypatch, image_path, credentials_path)
    monkeypatch.setenv("GOOGLE_OAUTH_TOKEN_PERSIST", "false")

    config = load_config()

    assert config.google_oauth_token_persist is False


def test_load_config_invalid_oauth_token_persist_raises(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    image_path, credentials_path = _create_files(tmp_path)
    _set_base_env(monkeypatch, image_path, credentials_path)
    monkeypatch.setenv("GOOGLE_OAUTH_TOKEN_PERSIST", "sometimes")

    with pytest.raises(
        ConfigError, match="GOOGLE_OAUTH_TOKEN_PERSIST must be 'true' or 'false'"
    ):
        load_config()


def test_load_config_succeeds_with_real_env_example_defaults(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    credentials_path = tmp_path / "credentials.json"
    credentials_path.write_text("{}", encoding="utf-8")

    for key in _parse_env_example():
        monkeypatch.delenv(key, raising=False)

    env_vars = _parse_env_example()
    env_vars.update(
        {
            "GOOGLE_SHEET_ID": "sheet-123",
            "GOOGLE_DRIVE_FILE_ID": "drive-123",
            "EMAIL_FROM_ADDRESS": "sender@example.com",
            "GOOGLE_CREDENTIALS_FILE": str(credentials_path),
        }
    )

    for key, value in env_vars.items():
        monkeypatch.setenv(key, value)

    config = load_config()

    assert config.birthday_image_mode == "local"
    assert config.birthday_image_path == _DEFAULT_BIRTHDAY_IMAGE_PATH
    assert config.birthday_image_path.is_file()
    assert config.email_provider == "gmail"
    assert config.google_oauth_token_persist is True


def test_load_config_default_birthday_image_path_is_independent_of_cwd(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    credentials_path = tmp_path / "credentials.json"
    credentials_path.write_text("{}", encoding="utf-8")

    for key in _parse_env_example():
        monkeypatch.delenv(key, raising=False)

    env_vars = _parse_env_example()
    env_vars.update(
        {
            "GOOGLE_SHEET_ID": "sheet-123",
            "GOOGLE_DRIVE_FILE_ID": "drive-123",
            "EMAIL_FROM_ADDRESS": "sender@example.com",
            "GOOGLE_CREDENTIALS_FILE": str(credentials_path),
        }
    )
    env_vars.pop("BIRTHDAY_IMAGE_PATH", None)

    for key, value in env_vars.items():
        monkeypatch.setenv(key, value)

    monkeypatch.chdir(tmp_path)

    config = load_config()

    assert config.birthday_image_mode == "local"
    assert config.birthday_image_path == _DEFAULT_BIRTHDAY_IMAGE_PATH
    assert config.birthday_image_path.is_file()


def test_load_config_relative_birthday_image_path_is_independent_of_cwd(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    credentials_path = tmp_path / "credentials.json"
    credentials_path.write_text("{}", encoding="utf-8")

    for key in _parse_env_example():
        monkeypatch.delenv(key, raising=False)

    env_vars = _parse_env_example()
    env_vars.update(
        {
            "GOOGLE_SHEET_ID": "sheet-123",
            "GOOGLE_DRIVE_FILE_ID": "drive-123",
            "EMAIL_FROM_ADDRESS": "sender@example.com",
            "GOOGLE_CREDENTIALS_FILE": str(credentials_path),
            "BIRTHDAY_IMAGE_PATH": "app/assets/birthday_banner.jpg",
        }
    )

    for key, value in env_vars.items():
        monkeypatch.setenv(key, value)

    monkeypatch.chdir(tmp_path)

    config = load_config()

    assert config.birthday_image_mode == "local"
    assert config.birthday_image_path == _DEFAULT_BIRTHDAY_IMAGE_PATH
    assert config.birthday_image_path.is_file()


def test_load_config_default_state_db_path_is_independent_of_cwd(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    credentials_path = tmp_path / "credentials.json"
    credentials_path.write_text("{}", encoding="utf-8")

    for key in _parse_env_example():
        monkeypatch.delenv(key, raising=False)

    env_vars = _parse_env_example()
    env_vars.update(
        {
            "GOOGLE_SHEET_ID": "sheet-123",
            "GOOGLE_DRIVE_FILE_ID": "drive-123",
            "EMAIL_FROM_ADDRESS": "sender@example.com",
            "GOOGLE_CREDENTIALS_FILE": str(credentials_path),
        }
    )
    env_vars.pop("STATE_DB_PATH", None)

    for key, value in env_vars.items():
        monkeypatch.setenv(key, value)

    monkeypatch.chdir(tmp_path)

    config = load_config()

    assert config.state_backend == "sqlite"
    assert config.state_db_path == _DEFAULT_STATE_DB_PATH
    assert config.firestore_database == _DEFAULT_FIRESTORE_DATABASE
    assert config.state_db_path.is_absolute()


def test_load_config_relative_state_db_path_is_independent_of_cwd(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    credentials_path = tmp_path / "credentials.json"
    credentials_path.write_text("{}", encoding="utf-8")

    for key in _parse_env_example():
        monkeypatch.delenv(key, raising=False)

    env_vars = _parse_env_example()
    env_vars.update(
        {
            "GOOGLE_SHEET_ID": "sheet-123",
            "GOOGLE_DRIVE_FILE_ID": "drive-123",
            "EMAIL_FROM_ADDRESS": "sender@example.com",
            "GOOGLE_CREDENTIALS_FILE": str(credentials_path),
            "STATE_DB_PATH": "data/birthday_state.db",
        }
    )

    for key, value in env_vars.items():
        monkeypatch.setenv(key, value)

    monkeypatch.chdir(tmp_path)

    config = load_config()

    assert config.state_backend == "sqlite"
    assert config.state_db_path == _DEFAULT_STATE_DB_PATH
    assert config.firestore_database == _DEFAULT_FIRESTORE_DATABASE
    assert config.state_db_path.is_absolute()


def test_load_config_firestore_backend_does_not_require_state_db_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    image_path, credentials_path = _create_files(tmp_path)
    _set_base_env(monkeypatch, image_path, credentials_path)
    monkeypatch.setenv("STATE_BACKEND", "firestore")
    monkeypatch.delenv("STATE_DB_PATH", raising=False)

    config = load_config()

    assert config.state_backend == "firestore"
    assert config.state_db_path is None
    assert config.firestore_database == _DEFAULT_FIRESTORE_DATABASE


def test_load_config_uses_configured_firestore_database(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    image_path, credentials_path = _create_files(tmp_path)
    _set_base_env(monkeypatch, image_path, credentials_path)
    monkeypatch.setenv("STATE_BACKEND", "firestore")
    monkeypatch.setenv("FIRESTORE_DATABASE", "custom-db")

    config = load_config()

    assert config.state_backend == "firestore"
    assert config.firestore_database == "custom-db"


def test_load_config_invalid_state_backend_raises(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    image_path, credentials_path = _create_files(tmp_path)
    _set_base_env(monkeypatch, image_path, credentials_path)
    monkeypatch.setenv("STATE_BACKEND", "redis")

    with pytest.raises(
        ConfigError,
        match="STATE_BACKEND must be 'sqlite' or 'firestore'",
    ):
        load_config()


def test_load_config_relative_google_credentials_file_is_independent_of_cwd(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    project_root = tmp_path / "project"
    credentials_path = project_root / "config/google-credentials.json"
    credentials_path.parent.mkdir(parents=True, exist_ok=True)
    credentials_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(config_module, "_PROJECT_ROOT", project_root)

    for key in _parse_env_example():
        monkeypatch.delenv(key, raising=False)

    env_vars = _parse_env_example()
    env_vars.update(
        {
            "GOOGLE_SHEET_ID": "sheet-123",
            "GOOGLE_DRIVE_FILE_ID": "drive-123",
            "EMAIL_FROM_ADDRESS": "sender@example.com",
            "GOOGLE_CREDENTIALS_FILE": "config/google-credentials.json",
            "BIRTHDAY_IMAGE_MODE": "none",
        }
    )

    for key, value in env_vars.items():
        monkeypatch.setenv(key, value)

    monkeypatch.chdir(tmp_path)

    config = load_config()

    assert config.google_credentials_file == credentials_path
    assert config.google_credentials_file.is_file()


def test_load_config_absolute_google_credentials_file_is_independent_of_cwd(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    credentials_path = tmp_path / "credentials.json"
    credentials_path.write_text("{}", encoding="utf-8")

    for key in _parse_env_example():
        monkeypatch.delenv(key, raising=False)

    env_vars = _parse_env_example()
    env_vars.update(
        {
            "GOOGLE_SHEET_ID": "sheet-123",
            "GOOGLE_DRIVE_FILE_ID": "drive-123",
            "EMAIL_FROM_ADDRESS": "sender@example.com",
            "GOOGLE_CREDENTIALS_FILE": str(credentials_path),
        }
    )

    for key, value in env_vars.items():
        monkeypatch.setenv(key, value)

    other_cwd = tmp_path / "other-cwd"
    other_cwd.mkdir()
    monkeypatch.chdir(other_cwd)

    config = load_config()

    assert config.google_credentials_file == credentials_path
    assert config.google_credentials_file.is_file()


def test_load_config_passes_project_root_env_path_to_load_dotenv(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    image_path, credentials_path = _create_files(tmp_path)
    _set_base_env(monkeypatch, image_path, credentials_path)
    monkeypatch.chdir(tmp_path)

    captured: dict[str, Path | None] = {"dotenv_path": None}

    def fake_load_dotenv(*, dotenv_path: Path | None = None, **_kwargs: object) -> bool:
        captured["dotenv_path"] = dotenv_path
        return False

    monkeypatch.setattr(config_module, "load_dotenv", fake_load_dotenv)

    load_config()

    assert captured["dotenv_path"] == config_module._PROJECT_ROOT / ".env"


def _set_base_env(
    monkeypatch: pytest.MonkeyPatch,
    image_path,
    credentials_path,
) -> None:
    env_vars = {
        "APP_TIMEZONE": "America/Chicago",
        "DRY_RUN": "true",
        "TEST_DATE": "",
        "SPREADSHEET_MODE": "google_sheet",
        "GOOGLE_SHEET_ID": "sheet-123",
        "GOOGLE_SHEET_TAB": "",
        "GOOGLE_DRIVE_FILE_ID": "drive-123",
        "NAME_COLUMN": "Name",
        "LAST_NAME_COLUMN": "Last Name",
        "GENDER_COLUMN": "Gender",
        "SERVICE_LINE_COLUMN": "Línea de servicio",
        "MOBILE_PHONE_COLUMN": "Móvil",
        "EMAIL_COLUMN": "Email",
        "BIRTHDAY_COLUMN": "Birthday",
        "LAST_SENT_YEAR_COLUMN": "Last Birthday Email Year",
        "EMAIL_PROVIDER": "gmail",
        "EMAIL_FROM_NAME": "Sender Name",
        "EMAIL_FROM_ADDRESS": "sender@example.com",
        "EMAIL_SUBJECT_TEMPLATE": EMAIL_SUBJECT_TEMPLATE_DEFAULT,
        "BP_REMINDER_TO_ADDRESS_DEFAULT": "",
        "BP_REMINDER_CC": "",
        "GOOGLE_CREDENTIALS_FILE": str(credentials_path),
        "GOOGLE_IMPERSONATE_SUBJECT": "",
        "BIRTHDAY_IMAGE_MODE": "local",
        "BIRTHDAY_IMAGE_PATH": str(image_path),
        "BIRTHDAY_IMAGE_URL": "",
        "BIRTHDAY_IMAGE_ALT": DEFAULT_BIRTHDAY_IMAGE_ALT,
        "BIRTHDAY_IMAGE_WIDTH": "600",
        "STATE_BACKEND": "sqlite",
        "STATE_DB_PATH": "data/birthday_state.db",
        "FIRESTORE_DATABASE": "birthday-automation",
        "STALE_CLAIM_TIMEOUT_MINUTES": "30",
        "RETRY_MAX_ATTEMPTS": "3",
        "RETRY_BASE_DELAY_SECONDS": "1.0",
        "LOG_LEVEL": "INFO",
    }
    for key, value in env_vars.items():
        monkeypatch.setenv(key, value)


def _create_files(tmp_path):
    image_path = tmp_path / "birthday_banner.jpg"
    image_path.write_bytes(b"jpg")
    credentials_path = tmp_path / "credentials.json"
    credentials_path.write_text("{}", encoding="utf-8")
    return image_path, credentials_path


def _parse_env_example() -> dict[str, str]:
    env_vars: dict[str, str] = {}
    for raw_line in Path(".env.example").read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env_vars[key.strip()] = value.strip()
    return env_vars
