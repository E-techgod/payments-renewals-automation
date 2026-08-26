from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import IO, Literal
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from app.email_content import (
    BPReminderRecipients,
    BP_REMINDER_CC_ENV_NAME,
    BP_REMINDER_TO_ADDRESS_ENV_NAME,
    DEFAULT_BIRTHDAY_IMAGE_ALT,
    DEFAULT_BIRTHDAY_IMAGE_MODE,
    DEFAULT_BIRTHDAY_IMAGE_PATH,
    DEFAULT_BIRTHDAY_IMAGE_URL,
    DEFAULT_BIRTHDAY_IMAGE_WIDTH,
    EMAIL_SUBJECT_TEMPLATE_DEFAULT,
    build_bp_reminder_recipients,
)

EMAIL_SUBJECT_TEMPLATE = ("EMAIL_SUBJECT_TEMPLATE", EMAIL_SUBJECT_TEMPLATE_DEFAULT)
try:
    from dotenv import load_dotenv
except ImportError:

    def load_dotenv(
        dotenv_path: str | os.PathLike[str] | None = None,
        stream: IO[str] | None = None,
        verbose: bool = False,
        override: bool = False,
        interpolate: bool = True,
        encoding: str | None = "utf-8",
    ) -> bool:
        del stream, verbose, override, interpolate
        env_path = Path(dotenv_path) if dotenv_path is not None else Path(".env")
        if not env_path.is_file():
            return False

        for raw_line in env_path.read_text(encoding=encoding or "utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())
        return True


SpreadsheetMode = Literal["google_sheet", "xlsx_drive"]
BirthdayImageMode = Literal["none", "local", "url"]
EmailProvider = Literal["gmail"]
GoogleAuthMode = Literal["service_account", "oauth"]
StateBackend = Literal["sqlite", "firestore"]

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_TEST_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_BIRTHDAY_IMAGE_PATH = _PROJECT_ROOT / DEFAULT_BIRTHDAY_IMAGE_PATH
_DEFAULT_STATE_DB_PATH = _PROJECT_ROOT / "data/birthday_state.db"
_DEFAULT_GOOGLE_OAUTH_TOKEN_PATH = _PROJECT_ROOT / "data/google_oauth_token.json"
_DEFAULT_FIRESTORE_DATABASE = "birthday-automation"


class ConfigError(ValueError):
    """Raised when environment configuration is invalid."""


@dataclass(frozen=True)
class Config:
    app_timezone: str
    dry_run: bool
    test_date: date | None
    spreadsheet_mode: SpreadsheetMode
    google_sheet_id: str
    google_sheet_tab: str
    google_drive_file_id: str
    name_column: str
    last_name_column: str
    gender_column: str
    service_line_column: str
    mobile_phone_column: str
    email_column: str
    birthday_column: str
    last_sent_year_column: str
    email_provider: str
    email_from_name: str
    email_from_address: str
    email_subject_template: str
    google_auth_mode: GoogleAuthMode
    google_credentials_file: Path | None
    google_impersonate_subject: str
    google_oauth_client_secrets_file: Path | None
    google_oauth_token_file: Path
    google_oauth_token_persist: bool
    birthday_image_mode: BirthdayImageMode
    birthday_image_path: Path
    birthday_image_url: str
    birthday_image_alt: str
    birthday_image_width: int
    state_backend: StateBackend
    state_db_path: Path | None
    firestore_database: str
    stale_claim_timeout_minutes: int
    retry_max_attempts: int
    retry_base_delay_seconds: float
    log_level: str
    bp_reminder_recipients: BPReminderRecipients = BPReminderRecipients()


def load_config() -> Config:
    load_dotenv(dotenv_path=_PROJECT_ROOT / ".env")

    spreadsheet_mode = _parse_spreadsheet_mode(
        _get_env("SPREADSHEET_MODE", "google_sheet")
    )
    app_timezone = _parse_timezone(_get_env("APP_TIMEZONE", "America/Chicago"))
    dry_run = _parse_bool("DRY_RUN", _get_env("DRY_RUN", "true"))
    test_date = _parse_test_date(_get_env("TEST_DATE", ""))
    email_provider = _parse_email_provider(_get_env("EMAIL_PROVIDER", "gmail"))
    email_from_address = _parse_email(_get_env("EMAIL_FROM_ADDRESS"))
    google_auth_mode = _parse_google_auth_mode(
        _get_env("GOOGLE_AUTH_MODE", "service_account")
    )
    google_credentials_file, google_oauth_client_secrets_file = (
        _load_google_auth_files(google_auth_mode)
    )
    google_oauth_token_file = _load_google_oauth_token_path()
    google_oauth_token_persist = _parse_bool(
        "GOOGLE_OAUTH_TOKEN_PERSIST",
        _get_env("GOOGLE_OAUTH_TOKEN_PERSIST", "true"),
    )
    birthday_image_mode = _parse_image_mode(
        _get_optional_override_env("BIRTHDAY_IMAGE_MODE")
        or DEFAULT_BIRTHDAY_IMAGE_MODE
    )
    birthday_image_path = _load_birthday_image_path()
    birthday_image_url = (
        _get_optional_override_env("BIRTHDAY_IMAGE_URL")
        or DEFAULT_BIRTHDAY_IMAGE_URL
    )
    birthday_image_width = _parse_positive_int(
        "BIRTHDAY_IMAGE_WIDTH",
        _get_optional_override_env("BIRTHDAY_IMAGE_WIDTH")
        or str(DEFAULT_BIRTHDAY_IMAGE_WIDTH),
    )
    state_backend = _parse_state_backend(_get_env("STATE_BACKEND", "sqlite"))

    google_sheet_id = _get_env("GOOGLE_SHEET_ID", "")
    google_drive_file_id = _get_env("GOOGLE_DRIVE_FILE_ID", "")
    _validate_spreadsheet_requirements(
        spreadsheet_mode, google_sheet_id, google_drive_file_id
    )
    _validate_image_settings(
        birthday_image_mode, birthday_image_path, birthday_image_url
    )

    google_impersonate_subject = _parse_google_impersonate_subject(
        _get_env("GOOGLE_IMPERSONATE_SUBJECT", ""),
        email_from_address,
    )
    bp_reminder_recipients = _load_bp_reminder_recipients()

    return Config(
        app_timezone=app_timezone,
        dry_run=dry_run,
        test_date=test_date,
        spreadsheet_mode=spreadsheet_mode,
        google_sheet_id=google_sheet_id,
        google_sheet_tab=_get_env("GOOGLE_SHEET_TAB", ""),
        google_drive_file_id=google_drive_file_id,
        name_column=_get_env("NAME_COLUMN", "Name"),
        last_name_column=_get_env("LAST_NAME_COLUMN", "Last Name"),
        gender_column=_get_env("GENDER_COLUMN", "Gender"),
        service_line_column=_get_env("SERVICE_LINE_COLUMN", "Línea de servicio"),
        mobile_phone_column=_get_env("MOBILE_PHONE_COLUMN", "Móvil"),
        email_column=_get_env("EMAIL_COLUMN", "Email"),
        birthday_column=_get_env("BIRTHDAY_COLUMN", "Birthday"),
        last_sent_year_column=_get_env(
            "LAST_SENT_YEAR_COLUMN", "Last Birthday Email Year"
        ),
        email_provider=email_provider,
        email_from_name=_get_env("EMAIL_FROM_NAME", ""),
        email_from_address=email_from_address,
        email_subject_template=(
            _get_optional_override_env(EMAIL_SUBJECT_TEMPLATE[0])
            or EMAIL_SUBJECT_TEMPLATE[1]
        ),
        google_auth_mode=google_auth_mode,
        google_credentials_file=google_credentials_file,
        google_impersonate_subject=google_impersonate_subject,
        google_oauth_client_secrets_file=google_oauth_client_secrets_file,
        google_oauth_token_file=google_oauth_token_file,
        google_oauth_token_persist=google_oauth_token_persist,
        birthday_image_mode=birthday_image_mode,
        birthday_image_path=birthday_image_path,
        birthday_image_url=birthday_image_url,
        birthday_image_alt=(
            _get_optional_override_env("BIRTHDAY_IMAGE_ALT")
            or DEFAULT_BIRTHDAY_IMAGE_ALT
        ),
        birthday_image_width=birthday_image_width,
        state_backend=state_backend,
        state_db_path=_load_state_db_path(state_backend),
        firestore_database=_get_env(
            "FIRESTORE_DATABASE", _DEFAULT_FIRESTORE_DATABASE
        ),
        stale_claim_timeout_minutes=_parse_positive_int(
            "STALE_CLAIM_TIMEOUT_MINUTES",
            _get_env("STALE_CLAIM_TIMEOUT_MINUTES", "30"),
        ),
        retry_max_attempts=_parse_positive_int(
            "RETRY_MAX_ATTEMPTS",
            _get_env("RETRY_MAX_ATTEMPTS", "3"),
        ),
        retry_base_delay_seconds=_parse_positive_float(
            "RETRY_BASE_DELAY_SECONDS",
            _get_env("RETRY_BASE_DELAY_SECONDS", "1.0"),
        ),
        log_level=_get_env("LOG_LEVEL", "INFO"),
        bp_reminder_recipients=bp_reminder_recipients,
    )


def _get_env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None:
        raise ConfigError(f"{name} is required")
    return value


def _get_optional_override_env(name: str) -> str | None:
    value = os.environ.get(name)
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    return stripped


def _load_birthday_image_path() -> Path:
    configured_path = _get_optional_override_env("BIRTHDAY_IMAGE_PATH")
    if configured_path is None:
        return _DEFAULT_BIRTHDAY_IMAGE_PATH
    return _resolve_project_relative_path(configured_path)


def _load_state_db_path(state_backend: StateBackend) -> Path | None:
    if state_backend != "sqlite":
        return None
    configured_path = _get_optional_override_env("STATE_DB_PATH")
    if configured_path is None:
        return _DEFAULT_STATE_DB_PATH
    return _resolve_project_relative_path(configured_path)


def _load_google_oauth_token_path() -> Path:
    configured_path = _get_optional_override_env("GOOGLE_OAUTH_TOKEN_FILE")
    if configured_path is None:
        return _DEFAULT_GOOGLE_OAUTH_TOKEN_PATH
    return _resolve_project_relative_path(configured_path)


def _parse_bool(name: str, value: str) -> bool:
    normalized = value.strip().casefold()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise ConfigError(f"{name} must be 'true' or 'false'")


def _parse_test_date(value: str) -> date | None:
    if not value.strip():
        return None
    if not _TEST_DATE_RE.fullmatch(value):
        raise ConfigError("TEST_DATE must be in YYYY-MM-DD format")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ConfigError("TEST_DATE must be in YYYY-MM-DD format") from exc


def _parse_spreadsheet_mode(value: str) -> SpreadsheetMode:
    if value == "google_sheet":
        return "google_sheet"
    if value == "xlsx_drive":
        return "xlsx_drive"
    raise ConfigError("SPREADSHEET_MODE must be 'google_sheet' or 'xlsx_drive'")


def _parse_image_mode(value: str) -> BirthdayImageMode:
    if value == "none":
        return "none"
    if value == "local":
        return "local"
    if value == "url":
        return "url"
    raise ConfigError("BIRTHDAY_IMAGE_MODE must be 'none', 'local', or 'url'")


def _parse_google_auth_mode(value: str) -> GoogleAuthMode:
    normalized = value.strip().casefold()
    if normalized == "service_account":
        return "service_account"
    if normalized == "oauth":
        return "oauth"
    raise ConfigError("GOOGLE_AUTH_MODE must be 'service_account' or 'oauth'")


def _parse_state_backend(value: str) -> StateBackend:
    normalized = value.strip().casefold()
    if normalized == "sqlite":
        return "sqlite"
    if normalized == "firestore":
        return "firestore"
    raise ConfigError("STATE_BACKEND must be 'sqlite' or 'firestore'")


def _load_google_auth_files(
    google_auth_mode: GoogleAuthMode,
) -> tuple[Path | None, Path | None]:
    if google_auth_mode == "oauth":
        oauth_client_secrets_file = _parse_existing_path(
            "GOOGLE_OAUTH_CLIENT_SECRETS_FILE",
            _get_env("GOOGLE_OAUTH_CLIENT_SECRETS_FILE"),
        )
        return None, oauth_client_secrets_file

    credentials_file = _parse_existing_path(
        "GOOGLE_CREDENTIALS_FILE",
        _get_env("GOOGLE_CREDENTIALS_FILE"),
    )
    return credentials_file, None


def _parse_email_provider(value: str) -> EmailProvider:
    normalized = value.strip().casefold()
    if normalized == "gmail":
        return "gmail"
    raise ConfigError("EMAIL_PROVIDER must be 'gmail'")


def _parse_email(value: str) -> str:
    return _parse_email_value("EMAIL_FROM_ADDRESS", value)


def _parse_email_value(name: str, value: str) -> str:
    if not _EMAIL_RE.match(value):
        raise ConfigError(f"{name} must be a valid email address")
    return value


def _parse_google_impersonate_subject(value: str, email_from_address: str) -> str:
    stripped_value = value.strip()
    if not stripped_value:
        return email_from_address
    return _parse_email_value("GOOGLE_IMPERSONATE_SUBJECT", stripped_value)


def _load_bp_reminder_recipients() -> BPReminderRecipients:
    try:
        return build_bp_reminder_recipients(
            _get_env(BP_REMINDER_TO_ADDRESS_ENV_NAME, ""),
            _get_env(BP_REMINDER_CC_ENV_NAME, ""),
        )
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc


def _parse_timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except Exception as exc:
        raise ConfigError(
            f"APP_TIMEZONE is not a valid IANA timezone: {value!r}"
        ) from exc
    return value


def _parse_existing_path(name: str, value: str) -> Path:
    path = _resolve_project_relative_path(value)
    if not path.is_file():
        raise ConfigError(f"{name} must point to an existing file")
    return path


def _resolve_project_relative_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return _PROJECT_ROOT / path


def _parse_positive_int(name: str, value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a positive integer") from exc
    if parsed <= 0:
        raise ConfigError(f"{name} must be a positive integer")
    return parsed


def _parse_positive_float(name: str, value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a positive number") from exc
    if parsed <= 0:
        raise ConfigError(f"{name} must be a positive number")
    return parsed


def _validate_spreadsheet_requirements(
    spreadsheet_mode: SpreadsheetMode,
    google_sheet_id: str,
    google_drive_file_id: str,
) -> None:
    if spreadsheet_mode == "google_sheet" and not google_sheet_id.strip():
        raise ConfigError(
            "GOOGLE_SHEET_ID is required when SPREADSHEET_MODE=google_sheet"
        )
    if spreadsheet_mode == "xlsx_drive" and not google_drive_file_id.strip():
        raise ConfigError(
            "GOOGLE_DRIVE_FILE_ID is required when SPREADSHEET_MODE=xlsx_drive"
        )


def _validate_image_settings(
    image_mode: BirthdayImageMode,
    image_path: Path,
    image_url: str,
) -> None:
    if image_mode == "none":
        return
    if image_mode == "local":
        if not image_path.is_file():
            raise ConfigError(
                "BIRTHDAY_IMAGE_PATH must point to an existing file when BIRTHDAY_IMAGE_MODE=local"
            )
        return
    parsed = urlparse(image_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ConfigError(
            "BIRTHDAY_IMAGE_URL must be an https:// URL when BIRTHDAY_IMAGE_MODE=url"
        )
