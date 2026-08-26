from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path

import pytest

from app.config import Config, ConfigError
from app.email.base import AmbiguousSendError, EmailMessage, EmailProvider, EmailSendError
from app.main import main
from app.reminder_config import ReminderStage
from app.reminder_rules import FixedClock
from app.reminder_service import Summary, build_email_provider, build_spreadsheet_provider, run_reminder_job
from app.spreadsheet.base import SpreadsheetProvider
from app.state.base import ClaimOutcome, ClaimResult, LeaseLostError
from tests.support import build_config, build_row


def test_reminder_before_due_date_claims_and_sends(caplog: pytest.LogCaptureFixture) -> None:
    provider = FakeSpreadsheetProvider(
        rows=[
            build_row(
                client="Ana",
                email="ana@example.com",
                policy_number="POL-123",
                renewal_date="2026-09-25",
            )
        ]
    )
    store = FakeStateStore()
    email_provider = FakeEmailProvider()

    with caplog.at_level(logging.INFO):
        summary = run_reminder_job(
            build_config(),
            spreadsheet_provider=provider,
            state_store=store,
            email_provider=email_provider,
            clock=FixedClock(date(2026, 8, 26)),
        )

    assert summary == Summary(inspected=1, matched=1, sent=1)
    assert store.claim_calls == [("ana@example.com", "POL-123", "2026-09-25", "30_days")]
    assert store.mark_sent_calls == [(101, "lease-101")]
    assert email_provider.sent_messages[0].to_email == "ana@example.com"
    assert "renewal reminder sent to ana@example.com for policy POL-123 stage 30_days" in caplog.text


def test_reminder_on_due_date_sends_due_today_stage() -> None:
    config = build_config(
        reminder_stages=(
            ReminderStage("30_days", "30 days before", -30),
            ReminderStage("due_today", "Due today", 0),
        )
    )
    email_provider = FakeEmailProvider()

    summary = run_reminder_job(
        config,
        spreadsheet_provider=FakeSpreadsheetProvider(
            rows=[
                build_row(
                    client="Ana",
                    email="ana@example.com",
                    policy_number="POL-123",
                    renewal_date="2026-08-26",
                )
            ]
        ),
        state_store=FakeStateStore(),
        email_provider=email_provider,
        clock=FixedClock(date(2026, 8, 26)),
    )

    assert summary == Summary(inspected=1, matched=1, sent=1)
    assert "Due today" in email_provider.sent_messages[0].text_body


def test_non_reminder_day_has_clean_zero_send_summary() -> None:
    summary = run_reminder_job(
        build_config(),
        spreadsheet_provider=FakeSpreadsheetProvider(
            rows=[
                build_row(
                    client="Ana",
                    email="ana@example.com",
                    policy_number="POL-123",
                    renewal_date="2026-09-26",
                )
            ]
        ),
        state_store=FakeStateStore(),
        email_provider=FakeEmailProvider(),
        clock=FixedClock(date(2026, 8, 26)),
    )

    assert summary == Summary(inspected=1)


def test_multiple_configured_stages_are_supported() -> None:
    config = build_config(
        reminder_stages=(
            ReminderStage("20_days", "20 days before", -20),
            ReminderStage("10_days", "10 days before", -10),
            ReminderStage("3_days", "3 days before", -3),
        )
    )

    summary = run_reminder_job(
        config,
        spreadsheet_provider=FakeSpreadsheetProvider(
            rows=[
                build_row(
                    client="Ana",
                    email="ana@example.com",
                    policy_number="POL-123",
                    renewal_date="2026-09-15",
                )
            ]
        ),
        state_store=FakeStateStore(),
        email_provider=FakeEmailProvider(),
        clock=FixedClock(date(2026, 8, 26)),
    )

    assert summary == Summary(inspected=1, matched=1, sent=1)


def test_same_reminder_stage_cannot_send_twice() -> None:
    store = FakeStateStore(claim_results=[ClaimResult(ClaimOutcome.ALREADY_SENT)])

    summary = run_reminder_job(
        build_config(),
        spreadsheet_provider=FakeSpreadsheetProvider(
            rows=[
                build_row(
                    client="Ana",
                    email="ana@example.com",
                    policy_number="POL-123",
                    renewal_date="2026-09-25",
                )
            ]
        ),
        state_store=store,
        email_provider=FakeEmailProvider(),
        clock=FixedClock(date(2026, 8, 26)),
    )

    assert summary == Summary(inspected=1, matched=1, duplicates=1)


def test_different_stages_for_same_renewal_can_send() -> None:
    config = build_config(
        reminder_stages=(
            ReminderStage("30_days", "30 days before", -30),
            ReminderStage("15_days", "15 days before", -15),
        )
    )
    store = FakeStateStore()
    email_provider = FakeEmailProvider()

    first = run_reminder_job(
        config,
        spreadsheet_provider=FakeSpreadsheetProvider(
            rows=[
                build_row(
                    client="Ana",
                    email="ana@example.com",
                    policy_number="POL-123",
                    renewal_date="2026-09-25",
                )
            ]
        ),
        state_store=store,
        email_provider=email_provider,
        clock=FixedClock(date(2026, 8, 26)),
    )
    second = run_reminder_job(
        config,
        spreadsheet_provider=FakeSpreadsheetProvider(
            rows=[
                build_row(
                    client="Ana",
                    email="ana@example.com",
                    policy_number="POL-123",
                    renewal_date="2026-09-25",
                )
            ]
        ),
        state_store=store,
        email_provider=email_provider,
        clock=FixedClock(date(2026, 9, 10)),
    )

    assert first == Summary(inspected=1, matched=1, sent=1)
    assert second == Summary(inspected=1, matched=1, sent=1)
    assert [call[3] for call in store.claim_calls] == ["30_days", "15_days"]


def test_next_years_renewal_can_send_again() -> None:
    store = FakeStateStore()
    email_provider = FakeEmailProvider()

    run_reminder_job(
        build_config(),
        spreadsheet_provider=FakeSpreadsheetProvider(
            rows=[
                build_row(
                    client="Ana",
                    email="ana@example.com",
                    policy_number="POL-123",
                    renewal_date="2026-09-25",
                )
            ]
        ),
        state_store=store,
        email_provider=email_provider,
        clock=FixedClock(date(2026, 8, 26)),
    )
    summary = run_reminder_job(
        build_config(),
        spreadsheet_provider=FakeSpreadsheetProvider(
            rows=[
                build_row(
                    client="Ana",
                    email="ana@example.com",
                    policy_number="POL-123",
                    renewal_date="2027-09-25",
                )
            ]
        ),
        state_store=store,
        email_provider=email_provider,
        clock=FixedClock(date(2027, 8, 26)),
    )

    assert summary == Summary(inspected=1, matched=1, sent=1)
    assert store.claim_calls[0][2] == "2026-09-25"
    assert store.claim_calls[1][2] == "2027-09-25"


def test_invalid_rows_are_skipped_and_do_not_block_valid_rows(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        summary = run_reminder_job(
            build_config(),
            spreadsheet_provider=FakeSpreadsheetProvider(
                rows=[
                    build_row(
                        client="Ana",
                        email="ana@example.com",
                        policy_number="POL-123",
                        renewal_date="2026-09-25",
                    ),
                    build_row(
                        client="Bad Date",
                        email="bad.date@example.com",
                        policy_number="POL-456",
                        renewal_date="not-a-date",
                    ),
                    build_row(
                        client="No Policy",
                        email="missing.policy@example.com",
                        policy_number="",
                        renewal_date="2026-09-25",
                    ),
                ]
            ),
            state_store=FakeStateStore(),
            email_provider=FakeEmailProvider(),
            clock=FixedClock(date(2026, 8, 26)),
        )

    assert summary == Summary(inspected=3, matched=1, sent=1, invalid=2)
    assert "row 3 skipped: missing or invalid renewal date" in caplog.text
    assert "row 4 skipped: missing policy number" in caplog.text


def test_disabled_reminder_stage_is_skipped() -> None:
    config = build_config(
        reminder_stages=(
            ReminderStage("30_days", "30 days before", -30, enabled=False),
        )
    )

    summary = run_reminder_job(
        config,
        spreadsheet_provider=FakeSpreadsheetProvider(
            rows=[
                build_row(
                    client="Ana",
                    email="ana@example.com",
                    policy_number="POL-123",
                    renewal_date="2026-09-25",
                )
            ]
        ),
        state_store=FakeStateStore(),
        email_provider=FakeEmailProvider(),
        clock=FixedClock(date(2026, 8, 26)),
    )

    assert summary == Summary(inspected=1)


def test_dry_run_does_not_claim_or_send(caplog: pytest.LogCaptureFixture) -> None:
    config = build_config(dry_run=True)
    store = FakeStateStore()

    with caplog.at_level(logging.INFO):
        summary = run_reminder_job(
            config,
            spreadsheet_provider=FakeSpreadsheetProvider(
                rows=[
                    build_row(
                        client="Ana",
                        email="ana@example.com",
                        policy_number="POL-123",
                        renewal_date="2026-09-25",
                    )
                ]
            ),
            state_store=store,
            email_provider=FakeEmailProvider(),
            clock=FixedClock(date(2026, 8, 26)),
        )

    assert summary == Summary(inspected=1, matched=1)
    assert store.claim_calls == []
    assert "[DRY RUN] would send renewal reminder to ana@example.com for policy POL-123 stage 30_days" in caplog.text


def test_email_subject_and_template_context_are_centralized() -> None:
    config = replace(
        build_config(),
        email_subject_template="Custom stage {{ reminder_stage }} for {{ policy_number }}",
    )
    email_provider = FakeEmailProvider()

    run_reminder_job(
        config,
        spreadsheet_provider=FakeSpreadsheetProvider(
            rows=[
                build_row(
                    client="Ana Perez",
                    email="ana@example.com",
                    policy_number="POL-123",
                    renewal_date="2026-09-25",
                )
            ]
        ),
        state_store=FakeStateStore(),
        email_provider=email_provider,
        clock=FixedClock(date(2026, 8, 26)),
    )

    message = email_provider.sent_messages[0]
    assert message.subject == "Custom stage 30_days for POL-123"
    assert "Ana Perez" in message.html_body
    assert "POL-123" in message.html_body
    assert "30 days before due date" in message.text_body


def test_spreadsheet_mode_selection_google_sheet(monkeypatch: pytest.MonkeyPatch) -> None:
    google_provider = FakeSpreadsheetProvider(rows=[])
    monkeypatch.setattr(
        "app.reminder_service.GoogleSheetsProvider.from_credentials",
        classmethod(lambda cls, config, credentials: google_provider),
    )

    provider = build_spreadsheet_provider(
        build_config(spreadsheet_mode="google_sheet"), credentials=object()
    )

    assert provider is google_provider


def test_spreadsheet_mode_selection_xlsx_drive(monkeypatch: pytest.MonkeyPatch) -> None:
    drive_provider = FakeSpreadsheetProvider(rows=[])
    monkeypatch.setattr(
        "app.reminder_service.XlsxDriveProvider.from_credentials",
        classmethod(lambda cls, config, credentials: drive_provider),
    )

    provider = build_spreadsheet_provider(
        build_config(spreadsheet_mode="xlsx_drive"), credentials=object()
    )

    assert provider is drive_provider


def test_build_email_provider_requests_only_gmail_send_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_scopes: list[str] = []
    provider_credentials: list[FakeCredentials] = []

    class FakeCredentials:
        def __init__(self) -> None:
            self.with_subject_calls: list[str] = []

        def with_subject(self, subject: str) -> FakeCredentials:
            self.with_subject_calls.append(subject)
            return self

    def fake_from_service_account_file(
        filename: str, *, scopes: list[str]
    ) -> FakeCredentials:
        captured_scopes.extend(scopes)
        return FakeCredentials()

    monkeypatch.setattr(
        "app.reminder_service.service_account.Credentials.from_service_account_file",
        fake_from_service_account_file,
    )
    monkeypatch.setattr(
        "app.reminder_service.GmailProvider.from_credentials",
        classmethod(
            lambda cls, config, credentials: (
                provider_credentials.append(credentials),
                FakeEmailProvider(),
            )[1]
        ),
    )

    config = build_config()
    provider = build_email_provider(config)

    assert isinstance(provider, FakeEmailProvider)
    assert captured_scopes == ["https://www.googleapis.com/auth/gmail.send"]
    assert provider_credentials[0].with_subject_calls == [config.google_impersonate_subject]


def test_email_provider_failure_marks_failed_and_continues() -> None:
    store = FakeStateStore()
    email_provider = FakeEmailProvider(
        effects=[EmailSendError("synthetic failure"), None]
    )

    summary = run_reminder_job(
        build_config(),
        spreadsheet_provider=FakeSpreadsheetProvider(
            rows=[
                build_row(
                    client="Failure",
                    email="failure@example.com",
                    policy_number="POL-1",
                    renewal_date="2026-09-25",
                ),
                build_row(
                    client="Success",
                    email="success@example.com",
                    policy_number="POL-2",
                    renewal_date="2026-09-25",
                ),
            ]
        ),
        state_store=store,
        email_provider=email_provider,
        clock=FixedClock(date(2026, 8, 26)),
    )

    assert summary == Summary(inspected=2, matched=2, sent=1, failed=1)
    assert store.mark_failed_calls == [(101, "lease-101")]
    assert store.mark_sent_calls == [(102, "lease-102")]


def test_ambiguous_send_marks_summary_and_continues(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = FakeStateStore()
    email_provider = FakeEmailProvider(
        effects=[AmbiguousSendError("synthetic ambiguous failure"), None]
    )

    with caplog.at_level(logging.INFO):
        summary = run_reminder_job(
            build_config(),
            spreadsheet_provider=FakeSpreadsheetProvider(
                rows=[
                    build_row(
                        client="Ambiguous",
                        email="ambiguous@example.com",
                        policy_number="POL-1",
                        renewal_date="2026-09-25",
                    ),
                    build_row(
                        client="Success",
                        email="success@example.com",
                        policy_number="POL-2",
                        renewal_date="2026-09-25",
                    ),
                ]
            ),
            state_store=store,
            email_provider=email_provider,
            clock=FixedClock(date(2026, 8, 26)),
        )

    assert summary == Summary(inspected=2, matched=2, sent=1, ambiguous=1)
    assert "duplicate email" in caplog.text


def test_main_exits_one_on_config_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("app.main.load_config", _raise_config_error)

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 1
    assert "synthetic config error" in capsys.readouterr().err


def test_main_exits_zero_when_summary_has_no_failed_or_ambiguous(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("app.main.load_config", build_config)
    monkeypatch.setattr(
        "app.main.run_reminder_job",
        lambda config, *, clock=None: Summary(inspected=7, matched=4, sent=2, duplicates=1, invalid=3),
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 0
    assert "INFO app.main job completed" in capsys.readouterr().err


class FakeSpreadsheetProvider(SpreadsheetProvider):
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows

    def load_rows(self) -> list[dict[str, object]]:
        return list(self.rows)


@dataclass
class FakeStateStore:
    claim_results: list[ClaimResult] = field(default_factory=list)
    claim_calls: list[tuple[str, str, str, str]] = field(default_factory=list)
    mark_sent_calls: list[tuple[int, str]] = field(default_factory=list)
    mark_failed_calls: list[tuple[int, str]] = field(default_factory=list)
    mark_sent_effects: list[Exception] = field(default_factory=list)
    mark_failed_effects: list[Exception] = field(default_factory=list)
    _claim_counter: int = 100

    def claim(
        self, client_key: str, policy_key: str, renewal_date_iso: str, stage_name: str
    ) -> ClaimResult:
        self.claim_calls.append((client_key, policy_key, renewal_date_iso, stage_name))
        if self.claim_results:
            return self.claim_results.pop(0)
        self._claim_counter += 1
        return ClaimResult(
            ClaimOutcome.CLAIMED, self._claim_counter, f"lease-{self._claim_counter}"
        )

    def mark_sent(self, claim_id: int, lease_token: str) -> None:
        self.mark_sent_calls.append((claim_id, lease_token))
        if self.mark_sent_effects:
            raise self.mark_sent_effects.pop(0)

    def mark_failed(self, claim_id: int, lease_token: str) -> None:
        self.mark_failed_calls.append((claim_id, lease_token))
        if self.mark_failed_effects:
            raise self.mark_failed_effects.pop(0)

    def close(self) -> None:
        return None


class FakeEmailProvider(EmailProvider):
    def __init__(self, effects: list[Exception | None] | None = None) -> None:
        self.effects = list(effects or [])
        self.sent_messages: list[EmailMessage] = []

    def send(self, message: EmailMessage) -> None:
        self.sent_messages.append(message)
        if self.effects:
            effect = self.effects.pop(0)
            if effect is not None:
                raise effect


def _raise_config_error() -> Config:
    raise ConfigError("synthetic config error")
