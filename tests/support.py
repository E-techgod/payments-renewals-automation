from __future__ import annotations

from datetime import date
from pathlib import Path

from app.config import Config
from app.reminder_config import DEFAULT_REMINDER_STAGES, ReminderStage


def build_config(
    *,
    dry_run: bool = False,
    spreadsheet_mode: str = "google_sheet",
    test_date: date | None = None,
    google_auth_mode: str = "service_account",
    google_credentials_file: Path | None = Path("synthetic-credentials.json"),
    google_oauth_client_secrets_file: Path | None = None,
    google_oauth_token_file: Path = Path("synthetic-oauth-token.json"),
    google_oauth_token_persist: bool = True,
    reminder_stages: tuple[ReminderStage, ...] = DEFAULT_REMINDER_STAGES,
) -> Config:
    return Config(
        app_timezone="America/Chicago",
        dry_run=dry_run,
        test_date=test_date,
        spreadsheet_mode=spreadsheet_mode,
        google_sheet_id="synthetic-sheet-id",
        google_sheet_tab="Renewals",
        google_drive_file_id="synthetic-drive-id",
        client_name_column="Client",
        last_name_column="Last Name",
        email_column="Email",
        policy_number_column="Policy Number",
        renewal_date_column="Renewal Date",
        service_line_column="Service Line",
        mobile_phone_column="Mobile Phone",
        email_provider="gmail",
        email_from_name="Example Sender",
        email_from_address="sender@example.com",
        email_subject_template="Renewal reminder: policy {{ policy_number }} due {{ renewal_date }}",
        email_html_template="renewal_reminder.html",
        email_text_template="renewal_reminder.txt",
        reminder_stages=reminder_stages,
        google_auth_mode=google_auth_mode,
        google_credentials_file=google_credentials_file,
        google_impersonate_subject="sender@example.com",
        google_oauth_client_secrets_file=google_oauth_client_secrets_file,
        google_oauth_token_file=google_oauth_token_file,
        google_oauth_token_persist=google_oauth_token_persist,
        state_backend="sqlite",
        state_db_path=Path("synthetic-state.db"),
        firestore_database="payments-renewals-automation",
        state_table_name="renewal_reminder_sends",
        firestore_collection_name="renewal_reminder_sends",
        stale_claim_timeout_minutes=30,
        retry_max_attempts=3,
        retry_base_delay_seconds=0.25,
        log_level="INFO",
    )


def build_row(
    *,
    client: str,
    email: str,
    policy_number: object,
    renewal_date: object,
    last_name: object = None,
    service_line: object = None,
    mobile_phone: object = None,
) -> dict[str, object]:
    row: dict[str, object] = {
        "Client": client,
        "Email": email,
        "Policy Number": policy_number,
        "Renewal Date": renewal_date,
    }
    if last_name is not None:
        row["Last Name"] = last_name
    if service_line is not None:
        row["Service Line"] = service_line
    if mobile_phone is not None:
        row["Mobile Phone"] = mobile_phone
    return row
