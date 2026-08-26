from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from google.auth.transport.requests import (  # type: ignore[import-untyped]
    Request as GoogleAuthRequest,
)
from google.oauth2 import service_account  # type: ignore[import-untyped]
from google.oauth2.credentials import (  # type: ignore[import-untyped]
    Credentials as GoogleOAuthCredentials,
)
from jinja2 import Environment

from app.config import Config
from app.email.base import AmbiguousSendError, EmailMessage, EmailProvider, EmailSendError
from app.email.gmail import GmailProvider
from app.email_content import build_email_template_environment
from app.models import Client, PolicyRenewal, ReminderMatch
from app.reminder_config import build_template_context, is_policy_eligible
from app.reminder_rules import Clock, build_clock, build_due_reminders, parse_reminder_date
from app.spreadsheet.base import SpreadsheetProvider
from app.spreadsheet.google_sheets import GoogleSheetsProvider
from app.spreadsheet.xlsx_drive import XlsxDriveProvider
from app.state.base import ClaimOutcome, ClaimResult, StateStore
from app.state.firestore import FirestoreStateStore
from app.state.sqlite import StateStore as SqliteStateStore

LOGGER = logging.getLogger(__name__)
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.send"
_SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"
_DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
_OAUTH_SCOPES = [_GMAIL_SCOPE, _SHEETS_SCOPE, _DRIVE_SCOPE]
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


def run_reminder_job(
    config: Config,
    *,
    spreadsheet_provider: SpreadsheetProvider | None = None,
    state_store: StateStore | None = None,
    email_provider: EmailProvider | None = None,
    clock: Clock | None = None,
) -> Summary:
    provider = spreadsheet_provider or build_spreadsheet_provider(config)
    effective_clock = clock or build_clock(config)
    get_state_store, close_state_store = _build_state_store_accessor(config, state_store)
    get_mailer = _build_email_provider_accessor(config, email_provider)

    summary = Summary()
    try:
        today = effective_clock.today()
        rows = provider.load_rows()
        LOGGER.info("spreadsheet load succeeded: rows=%d", len(rows))

        renewals: list[PolicyRenewal] = []
        invalid_count = 0
        for row_index, row in enumerate(rows, start=2):
            renewal = _parse_renewal_row(config, row, row_index)
            if renewal is None:
                invalid_count += 1
                continue
            if not is_policy_eligible(
                client_name=renewal.client.display_name,
                policy_number=renewal.policy_number,
                renewal_date=renewal.renewal_date,
                service_lines=renewal.client.service_lines,
            ):
                continue
            renewals.append(renewal)

        matches = [
            match
            for renewal in renewals
            for match in build_due_reminders(renewal, config.reminder_stages, today)
        ]
        LOGGER.info("reminders detected: count=%d", len(matches))

        summary = Summary(
            inspected=len(rows),
            matched=len(matches),
            invalid=invalid_count,
        )

        template_env, subject_env = _build_template_environments()

        for match in matches:
            summary = _process_match(
                config=config,
                match=match,
                today=today,
                get_state_store=get_state_store,
                get_email_provider=get_mailer,
                template_env=template_env,
                subject_env=subject_env,
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
                    collection_name=config.firestore_collection_name,
                )
            else:
                if config.state_db_path is None:
                    raise RuntimeError(
                        "STATE_DB_PATH is required when STATE_BACKEND=sqlite"
                    )
                store = SqliteStateStore(
                    config.state_db_path,
                    config.stale_claim_timeout_minutes,
                    table_name=config.state_table_name,
                )
        return store

    def close_state_store() -> None:
        if store is not None:
            store.close()

    return get_state_store, close_state_store


def _parse_renewal_row(
    config: Config, row: dict[str, object], row_index: int
) -> PolicyRenewal | None:
    name = _parse_name(row.get(config.client_name_column))
    if name is None:
        LOGGER.warning("row %d skipped: missing client name", row_index)
        return None

    normalized_email = _parse_email(row.get(config.email_column))
    if normalized_email is None:
        LOGGER.warning("row %d skipped: missing or invalid email", row_index)
        return None

    policy_number = _parse_policy_number(row.get(config.policy_number_column))
    if policy_number is None:
        LOGGER.warning("row %d skipped: missing policy number", row_index)
        return None

    renewal_date = parse_reminder_date(row.get(config.renewal_date_column))
    if renewal_date is None:
        LOGGER.warning("row %d skipped: missing or invalid renewal date", row_index)
        return None

    client = Client(
        name=name,
        email=normalized_email,
        last_name=_parse_name(row.get(config.last_name_column)),
        mobile_phone=_parse_mobile_phone(row.get(config.mobile_phone_column)),
        service_lines=_parse_service_lines(row.get(config.service_line_column)),
    )
    return PolicyRenewal(
        client=client,
        policy_number=policy_number,
        renewal_date=renewal_date,
        row_index=row_index,
    )


def _parse_name(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    name = raw.strip()
    if not name:
        return None
    return name


def _parse_policy_number(raw: object) -> str | None:
    if isinstance(raw, str):
        value = raw.strip()
        return value or None
    if isinstance(raw, int | float) and not isinstance(raw, bool):
        return str(raw).rstrip("0").rstrip(".") if isinstance(raw, float) else str(raw)
    return None


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


def _build_template_environments() -> tuple[Environment, Environment]:
    return build_email_template_environment(), Environment(autoescape=False)


def _process_match(
    *,
    config: Config,
    match: ReminderMatch,
    today,
    get_state_store: Callable[[], StateStore],
    get_email_provider: Callable[[], EmailProvider],
    template_env: Environment,
    subject_env: Environment,
    summary: Summary,
) -> Summary:
    if config.dry_run:
        _log_dry_run(match)
        return summary

    claim_result: ClaimResult | None = None
    renewal = match.renewal
    state_store = get_state_store()
    try:
        claim_result = state_store.claim(
            renewal.client.email,
            renewal.policy_number,
            renewal.renewal_date.isoformat(),
            match.stage.name,
        )
        if claim_result.outcome == ClaimOutcome.ALREADY_SENT:
            LOGGER.info(
                "duplicate skipped for %s policy=%s stage=%s",
                renewal.client.email,
                renewal.policy_number,
                match.stage.name,
            )
            return _with_increment(summary, duplicates=1)
        if claim_result.outcome == ClaimOutcome.IN_PROGRESS:
            LOGGER.info(
                "in-progress skipped for %s policy=%s stage=%s",
                renewal.client.email,
                renewal.policy_number,
                match.stage.name,
            )
            return _with_increment(summary, in_progress=1)

        if claim_result.claim_id is None or claim_result.lease_token is None:
            raise RuntimeError(
                "CLAIMED result did not include claim_id and lease_token"
            )

        message = _render_message(
            config=config,
            match=match,
            today=today,
            template_env=template_env,
            subject_env=subject_env,
        )

        get_email_provider().send(message)
        try:
            state_store.mark_sent(claim_result.claim_id, claim_result.lease_token)
        except Exception:
            LOGGER.critical(
                "renewal reminder was sent to %s for policy %s stage %s, but the state store could not durably record it; a future reclaim could send a duplicate email, so verify the mailbox manually",
                renewal.client.email,
                renewal.policy_number,
                match.stage.name,
                exc_info=True,
            )
            return _with_increment(summary, ambiguous=1)
        LOGGER.info(
            "renewal reminder sent to %s for policy %s stage %s",
            renewal.client.email,
            renewal.policy_number,
            match.stage.name,
        )
        return _with_increment(summary, sent=1)
    except AmbiguousSendError:
        LOGGER.critical(
            "send outcome is unknown for %s policy %s stage %s; a future reclaim could send a duplicate email, so verify the mailbox manually",
            renewal.client.email,
            renewal.policy_number,
            match.stage.name,
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
                    renewal.client.email,
                )
        LOGGER.exception("renewal reminder failed for %s", renewal.client.email)
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
                    renewal.client.email,
                )
        LOGGER.exception("unexpected error while processing %s", renewal.client.email)
        return _with_increment(summary, failed=1)


def _render_message(
    *,
    config: Config,
    match: ReminderMatch,
    today,
    template_env: Environment,
    subject_env: Environment,
) -> EmailMessage:
    renewal = match.renewal
    template_context = build_template_context(
        client_name=renewal.client.display_name,
        policy_number=renewal.policy_number,
        renewal_date=renewal.renewal_date,
        reminder_stage=match.stage,
        today=today,
    )
    template_context["from_name"] = config.email_from_name
    html_body = template_env.get_template(config.email_html_template).render(
        template_context
    )
    text_body = template_env.get_template(config.email_text_template).render(
        template_context
    )
    subject = subject_env.from_string(config.email_subject_template).render(
        **template_context
    )
    return EmailMessage(
        to_email=renewal.client.email,
        to_name=renewal.client.display_name,
        from_name=config.email_from_name,
        from_address=config.email_from_address,
        subject=subject,
        html_body=html_body,
        text_body=text_body,
        inline_image=None,
    )


def _log_dry_run(match: ReminderMatch) -> None:
    renewal = match.renewal
    LOGGER.info(
        "[DRY RUN] would send renewal reminder to %s for policy %s stage %s",
        renewal.client.email,
        renewal.policy_number,
        match.stage.name,
    )


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
