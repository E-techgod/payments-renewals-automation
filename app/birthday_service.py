from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from google.auth.transport.requests import (  # type: ignore[import-untyped]
    Request as GoogleAuthRequest,
)
from google.oauth2 import service_account  # type: ignore[import-untyped]
from google.oauth2.credentials import (  # type: ignore[import-untyped]
    Credentials as GoogleOAuthCredentials,
)
from jinja2 import Environment

from app.birthday_rules import Clock, build_clock, is_birthday_today, parse_birthday
from app.config import Config
from app.email.base import (
    AmbiguousSendError,
    EmailMessage,
    EmailProvider,
    EmailSendError,
    InlineImage,
)
from app.email_content import (
    BP_FOLLOW_UP_TEXT,
    BP_REMINDER_SUBJECT_TEMPLATE,
    BP_STATUS_LABEL,
    INLINE_IMAGE_CONTENT_ID,
    SIGNATURE_CLOSING,
    SIGNATURE_INTRO,
    build_email_template_environment,
)
from app.email.gmail import GmailProvider
from app.models import Client, DeliveryRoute
from app.spreadsheet.base import SpreadsheetProvider
from app.spreadsheet.google_sheets import GoogleSheetsProvider
from app.spreadsheet.xlsx_drive import XlsxDriveProvider
from app.state.base import ClaimOutcome, ClaimResult, StateStore
from app.state.firestore import FirestoreStateStore
from app.state.sqlite import StateStore as SqliteStateStore

StateStore = SqliteStateStore

LOGGER = logging.getLogger(__name__)
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.send"
_SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"
_DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
# OAuth mode requests the union of scopes up front (one interactive consent,
# one cached token) rather than the narrower per-call scope used for service
# accounts, since an installed-app flow can't silently re-consent mid-run.
_OAUTH_SCOPES = [_GMAIL_SCOPE, _SHEETS_SCOPE, _DRIVE_SCOPE]
_UNSET = object()
_SERVICE_LINE_SPLIT_RE = re.compile(r"[,;/]+")
_NON_DIGIT_RE = re.compile(r"\D+")
_MIN_USABLE_PHONE_DIGITS = 7


@dataclass(frozen=True)
class Summary:
    inspected: int = 0
    matched: int = 0
    sent: int = 0
    duplicates: int = 0
    in_progress: int = 0
    invalid: int = 0
    failed: int = 0
    ambiguous: int = 0


def run_birthday_job(
    config: Config,
    *,
    spreadsheet_provider: SpreadsheetProvider | None = None,
    state_store: StateStore | None = None,
    email_provider: EmailProvider | None = None,
    clock: Clock | None = None,
) -> Summary:
    provider = spreadsheet_provider or build_spreadsheet_provider(config)
    effective_clock = clock or build_clock(config)
    get_state_store, close_state_store = _build_state_store_accessor(
        config, state_store
    )
    get_mailer = _build_email_provider_accessor(config, email_provider)
    get_inline_image = _build_inline_image_accessor(config)

    summary = Summary()
    try:
        today = effective_clock.today()
        rows = provider.load_rows()
        LOGGER.info("spreadsheet load succeeded: rows=%d", len(rows))

        clients: list[Client] = []
        invalid_count = 0
        for row_index, row in enumerate(rows, start=2):
            client = _parse_client_row(config, row, row_index)
            if client is None:
                invalid_count += 1
                continue
            clients.append(client)

        matched_clients = [
            client for client in clients if is_birthday_today(client.birthday, today)
        ]
        LOGGER.info("birthdays detected: count=%d", len(matched_clients))

        summary = Summary(
            inspected=len(rows),
            matched=len(matched_clients),
            invalid=invalid_count,
        )

        html_env, subject_env = _build_template_environments()

        for client in matched_clients:
            summary = _process_match(
                config=config,
                client=client,
                today_year=today.year,
                get_state_store=get_state_store,
                get_email_provider=get_mailer,
                html_env=html_env,
                subject_env=subject_env,
                get_inline_image=get_inline_image,
                summary=summary,
            )
        return summary
    finally:
        close_state_store()


def build_spreadsheet_provider(
    config: Config, credentials: Any | None = None
) -> SpreadsheetProvider:
    scope = _SHEETS_SCOPE if config.spreadsheet_mode == "google_sheet" else _DRIVE_SCOPE
    effective_credentials = credentials or _load_google_credentials(config, [scope])
    if config.spreadsheet_mode == "google_sheet":
        return GoogleSheetsProvider.from_credentials(config, effective_credentials)
    return XlsxDriveProvider.from_credentials(config, effective_credentials)


def build_email_provider(
    config: Config, credentials: Any | None = None
) -> EmailProvider:
    effective_credentials = credentials or _build_gmail_credentials(config)
    if config.email_provider != "gmail":
        raise ValueError(f"Unsupported email provider: {config.email_provider}")
    return GmailProvider.from_credentials(config, effective_credentials)


def _build_gmail_credentials(config: Config) -> Any:
    credentials = _load_google_credentials(config, [_GMAIL_SCOPE])
    # Domain-wide delegation (with_subject) is a service-account-only feature;
    # an OAuth user credential already represents the consenting user.
    if config.google_auth_mode == "service_account":
        return credentials.with_subject(config.google_impersonate_subject)
    return credentials


def _load_google_credentials(config: Config, scopes: list[str]) -> Any:
    if config.google_auth_mode == "oauth":
        return _load_oauth_user_credentials(config)
    return service_account.Credentials.from_service_account_file(
        str(config.google_credentials_file),
        scopes=scopes,
    )


def _load_oauth_user_credentials(config: Config) -> Any:
    try:
        from google_auth_oauthlib.flow import (  # type: ignore[import-untyped]
            InstalledAppFlow,
        )
    except ImportError as exc:
        raise RuntimeError(
            "GOOGLE_AUTH_MODE=oauth requires the 'google-auth-oauthlib' package; "
            "install the project's dev dependencies to use it locally"
        ) from exc

    token_path = config.google_oauth_token_file
    credentials: GoogleOAuthCredentials | None = None
    if token_path.is_file():
        credentials = GoogleOAuthCredentials.from_authorized_user_file(
            str(token_path), _OAUTH_SCOPES
        )

    if credentials and credentials.expired and credentials.refresh_token:
        credentials.refresh(GoogleAuthRequest())
    elif not credentials or not credentials.valid:
        flow = InstalledAppFlow.from_client_secrets_file(
            str(config.google_oauth_client_secrets_file), _OAUTH_SCOPES
        )
        credentials = flow.run_local_server(port=0)

    if config.google_oauth_token_persist:
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(credentials.to_json(), encoding="utf-8")
    return credentials


def _build_email_provider_accessor(
    config: Config, email_provider: EmailProvider | None
) -> Callable[[], EmailProvider]:
    if email_provider is not None:
        return lambda: email_provider

    mailer: EmailProvider | None = None

    def get_email_provider() -> EmailProvider:
        nonlocal mailer
        if mailer is None:
            mailer = build_email_provider(config)
        return mailer

    return get_email_provider


def _build_state_store_accessor(
    config: Config, state_store: StateStore | None
) -> tuple[Callable[[], StateStore], Callable[[], None]]:
    if state_store is not None:
        return (lambda: state_store), (lambda: None)

    store: StateStore | None = None

    def get_state_store() -> StateStore:
        nonlocal store
        if store is None:
            if config.state_backend == "firestore":
                store = FirestoreStateStore(
                    config.stale_claim_timeout_minutes,
                    firestore_database=config.firestore_database,
                )
            else:
                if config.state_db_path is None:
                    raise RuntimeError(
                        "STATE_DB_PATH is required when STATE_BACKEND=sqlite"
                    )
                store = StateStore(
                    config.state_db_path,
                    config.stale_claim_timeout_minutes,
                )
        return store

    def close_state_store() -> None:
        if store is not None:
            store.close()

    return get_state_store, close_state_store


def _build_inline_image_accessor(config: Config) -> Callable[[], InlineImage | None]:
    inline_image: InlineImage | None | object = _UNSET

    def get_inline_image() -> InlineImage | None:
        nonlocal inline_image
        if inline_image is _UNSET:
            inline_image = _build_inline_image(config)
        if isinstance(inline_image, InlineImage):
            return inline_image
        return None

    return get_inline_image


def _parse_client_row(
    config: Config, row: dict[str, object], row_index: int
) -> Client | None:
    name = _parse_name(row.get(config.name_column))
    if name is None:
        LOGGER.warning("row %d skipped: missing name", row_index)
        return None

    normalized_email = _parse_email(row.get(config.email_column))
    if normalized_email is None:
        LOGGER.warning("row %d skipped: missing or invalid email", row_index)
        return None

    birthday = parse_birthday(row.get(config.birthday_column))
    if birthday is None:
        LOGGER.warning("row %d skipped: missing or invalid birthday", row_index)
        return None

    return Client(
        name=name,
        email=normalized_email,
        birthday=birthday,
        row_index=row_index,
        last_sent_year=_parse_last_sent_year(row.get(config.last_sent_year_column)),
        last_name=_parse_name(row.get(config.last_name_column)),
        gender=_parse_gender(row.get(config.gender_column)),
        mobile_phone=_parse_mobile_phone(row.get(config.mobile_phone_column)),
        service_lines=_parse_service_lines(row.get(config.service_line_column)),
        delivery_route=_resolve_delivery_route(
            row.get(config.service_line_column),
        ),
    )


def _parse_name(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    name = raw.strip()
    if not name:
        return None
    return name


def _parse_gender(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    gender = raw.strip()
    if not gender:
        return None
    return gender


def _parse_email(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    email = raw.strip().lower()
    if not email or _EMAIL_RE.fullmatch(email) is None:
        return None
    return email


def _parse_mobile_phone(raw: object) -> str | None:
    if isinstance(raw, str):
        text = raw.strip()
    elif isinstance(raw, int) and not isinstance(raw, bool):
        text = str(raw)
    elif isinstance(raw, float) and raw.is_integer():
        text = str(int(raw))
    else:
        return None

    normalized = _NON_DIGIT_RE.sub("", text)
    if len(normalized) < _MIN_USABLE_PHONE_DIGITS:
        return None
    return normalized


def _parse_service_lines(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, str):
        return ()
    values = []
    for value in _SERVICE_LINE_SPLIT_RE.split(raw):
        normalized = value.strip()
        if normalized:
            values.append(normalized)
    return tuple(values)


def _contains_bp_service_line(raw: object) -> bool:
    return any(value.casefold() == "bp" for value in _parse_service_lines(raw))


def _resolve_delivery_route(raw_service_line: object) -> DeliveryRoute:
    if _contains_bp_service_line(raw_service_line):
        return "bp_call_reminder"
    return "birthday_email"


def _parse_last_sent_year(raw: object) -> int | None:
    if raw is None:
        return None
    if isinstance(raw, int) and not isinstance(raw, bool):
        return raw
    if isinstance(raw, float) and raw.is_integer():
        return int(raw)
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        try:
            return int(text)
        except ValueError:
            return None
    return None


def _build_template_environments() -> tuple[Environment, Environment]:
    html_env = build_email_template_environment()
    subject_env = Environment(autoescape=False)
    return html_env, subject_env


def _build_inline_image(config: Config) -> InlineImage | None:
    if config.birthday_image_mode != "local":
        return None
    return InlineImage(
        content_id=INLINE_IMAGE_CONTENT_ID,
        data=config.birthday_image_path.read_bytes(),
        mime_type=_sniff_image_mime_type(config.birthday_image_path),
    )


def _sniff_image_mime_type(path: Path) -> str:
    suffix = path.suffix.casefold()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".png":
        return "image/png"
    raise ValueError(f"Unsupported birthday image file extension: {path.suffix}")


def _process_match(
    *,
    config: Config,
    client: Client,
    today_year: int,
    get_state_store: Callable[[], StateStore],
    get_email_provider: Callable[[], EmailProvider],
    html_env: Environment,
    subject_env: Environment,
    get_inline_image: Callable[[], InlineImage | None],
    summary: Summary,
) -> Summary:
    if config.dry_run:
        _log_dry_run(config, client)
        return summary

    claim_result: ClaimResult | None = None
    state_store = get_state_store()
    try:
        claim_result = state_store.claim(
            client.email,
            client.birthday.month,
            client.birthday.day,
            today_year,
        )
        if claim_result.outcome == ClaimOutcome.ALREADY_SENT:
            LOGGER.info("duplicate skipped for %s", client.email)
            return _with_increment(summary, duplicates=1)
        if claim_result.outcome == ClaimOutcome.IN_PROGRESS:
            LOGGER.info("in-progress skipped for %s", client.email)
            return _with_increment(summary, in_progress=1)

        if claim_result.claim_id is None or claim_result.lease_token is None:
            raise RuntimeError(
                "CLAIMED result did not include claim_id and lease_token"
            )

        message = _render_message(
            config=config,
            client=client,
            html_env=html_env,
            subject_env=subject_env,
            get_inline_image=get_inline_image,
        )

        get_email_provider().send(message)
        try:
            state_store.mark_sent(claim_result.claim_id, claim_result.lease_token)
        except Exception:
            LOGGER.critical(
                "birthday email was sent to %s, but the state store could not durably record it; a future reclaim could send a duplicate email, so verify the mailbox manually",
                client.email,
                exc_info=True,
            )
            return _with_increment(summary, ambiguous=1)
        _log_sent_message(client, message)
        return _with_increment(summary, sent=1)
    except AmbiguousSendError:
        LOGGER.critical(
            "send outcome is unknown for %s; a future reclaim could send a duplicate email, so verify the mailbox manually",
            client.email,
            exc_info=True,
        )
        return _with_increment(summary, ambiguous=1)
    except EmailSendError:
        if claim_result is not None and claim_result.claim_id is not None:
            try:
                state_store.mark_failed(
                    claim_result.claim_id, claim_result.lease_token or ""
                )
            except Exception:
                LOGGER.exception(
                    "failed to mark claim as failed for %s after an email send failure",
                    client.email,
                )
        LOGGER.exception("birthday email failed for %s", client.email)
        return _with_increment(summary, failed=1)
    except Exception:
        if (
            claim_result is not None
            and claim_result.claim_id is not None
            and claim_result.lease_token is not None
        ):
            try:
                state_store.mark_failed(claim_result.claim_id, claim_result.lease_token)
            except Exception:
                LOGGER.exception(
                    "failed to mark claim as failed for %s after an unexpected error",
                    client.email,
                )
        LOGGER.exception("unexpected error while processing %s", client.email)
        return _with_increment(summary, failed=1)


def _render_message(
    *,
    config: Config,
    client: Client,
    html_env: Environment,
    subject_env: Environment,
    get_inline_image: Callable[[], InlineImage | None],
) -> EmailMessage:
    if client.delivery_route == "bp_call_reminder":
        return _render_bp_call_reminder(
            config=config,
            client=client,
            html_env=html_env,
            subject_env=subject_env,
        )

    return _render_birthday_email(
        config=config,
        client=client,
        html_env=html_env,
        subject_env=subject_env,
        get_inline_image=get_inline_image,
    )


def _render_birthday_email(
    *,
    config: Config,
    client: Client,
    html_env: Environment,
    subject_env: Environment,
    get_inline_image: Callable[[], InlineImage | None],
) -> EmailMessage:
    template_context = {
        "name": client.name,
        "salutation": client.salutation,
        "image_mode": config.birthday_image_mode,
        "image_alt": config.birthday_image_alt,
        "image_width": config.birthday_image_width,
        "image_url": config.birthday_image_url,
        "inline_image_content_id": INLINE_IMAGE_CONTENT_ID,
        "signature_closing": SIGNATURE_CLOSING,
        "signature_intro": SIGNATURE_INTRO,
        "from_name": config.email_from_name,
    }
    html_body = html_env.get_template("birthday_email.html").render(template_context)
    text_body = html_env.get_template("birthday_email.txt").render(template_context)
    subject = subject_env.from_string(config.email_subject_template).render(
        name=client.name,
        last_name=client.last_name,
        display_name=client.display_name,
    )
    return EmailMessage(
        to_email=client.email,
        to_name=client.name,
        from_name=config.email_from_name,
        from_address=config.email_from_address,
        subject=subject,
        html_body=html_body,
        text_body=text_body,
        inline_image=get_inline_image(),
    )


def _render_bp_call_reminder(
    *,
    config: Config,
    client: Client,
    html_env: Environment,
    subject_env: Environment,
) -> EmailMessage:
    template_context = {
        "display_name": client.display_name,
        "birthday_date": _format_birthday_date(client.birthday),
        "mobile_phone": client.mobile_phone or "No disponible",
        "bp_status": BP_STATUS_LABEL,
        "bp_follow_up_text": BP_FOLLOW_UP_TEXT,
    }
    html_body = html_env.get_template("bp_call_reminder.html").render(template_context)
    text_body = html_env.get_template("bp_call_reminder.txt").render(template_context)
    subject = subject_env.from_string(BP_REMINDER_SUBJECT_TEMPLATE).render(
        name=client.name,
        last_name=client.last_name,
        display_name=client.display_name,
    )
    return EmailMessage(
        to_email=config.bp_reminder_recipients.to_email,
        to_name=config.bp_reminder_recipients.to_name,
        from_name=config.email_from_name,
        from_address=config.email_from_address,
        subject=subject,
        html_body=html_body,
        text_body=text_body,
        inline_image=None,
        cc_emails=config.bp_reminder_recipients.cc_emails,
    )


def _format_birthday_date(value: date) -> str:
    return value.isoformat()


def _log_dry_run(config: Config, client: Client) -> None:
    if client.delivery_route == "bp_call_reminder":
        cc_suffix = ""
        if config.bp_reminder_recipients.cc_emails:
            cc_suffix = f" (cc: {', '.join(config.bp_reminder_recipients.cc_emails)})"
        LOGGER.info(
            "[DRY RUN] would send BP birthday call reminder for %s to %s%s",
            client.display_name,
            config.bp_reminder_recipients.to_email,
            cc_suffix,
        )
        return
    LOGGER.info(
        "[DRY RUN] would send birthday email to %s (%s)",
        client.email,
        client.name,
    )


def _log_sent_message(client: Client, message: EmailMessage) -> None:
    if client.delivery_route == "bp_call_reminder":
        cc_suffix = ""
        if message.cc_emails:
            cc_suffix = f" (cc: {', '.join(message.cc_emails)})"
        LOGGER.info(
            "BP birthday call reminder sent for %s to %s%s",
            client.email,
            message.to_email,
            cc_suffix,
        )
        return
    LOGGER.info("birthday email sent to %s", client.email)


def _with_increment(
    summary: Summary,
    *,
    sent: int = 0,
    duplicates: int = 0,
    in_progress: int = 0,
    failed: int = 0,
    ambiguous: int = 0,
) -> Summary:
    return Summary(
        inspected=summary.inspected,
        matched=summary.matched,
        sent=summary.sent + sent,
        duplicates=summary.duplicates + duplicates,
        in_progress=summary.in_progress + in_progress,
        invalid=summary.invalid,
        failed=summary.failed + failed,
        ambiguous=summary.ambiguous + ambiguous,
    )
