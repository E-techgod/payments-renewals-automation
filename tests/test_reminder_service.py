from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path

import pytest

from app.reminder_rules import FixedClock
from app.reminder_service import (
    Summary,
    build_email_provider,
    build_spreadsheet_provider,
    run_reminder_job,
)
from app.config import Config, ConfigError
from app.email_content import (
    BPReminderRecipients,
    BP_REMINDER_TO_ADDRESS_DEFAULT,
    BP_STATUS_LABEL,
    DEFAULT_BIRTHDAY_IMAGE_ALT,
    DEFAULT_SALUTATION,
    EMAIL_SUBJECT_TEMPLATE_DEFAULT,
    FEMALE_SALUTATION,
    MALE_SALUTATION,
)
from app.email.base import (
    AmbiguousSendError,
    EmailMessage,
    EmailProvider,
    EmailSendError,
)
from app.main import main
from app.spreadsheet.base import SpreadsheetProvider
from app.state.sqlite import ClaimOutcome, ClaimResult, LeaseLostError


def test_reminder_today_claims_and_sends(caplog: pytest.LogCaptureFixture) -> None:
    provider = FakeSpreadsheetProvider(
        rows=[
            _row(
                name="Test Person", email="test.person@example.com", reminder_date="1/1/2000"
            )
        ]
    )
    store = FakeStateStore()
    email_provider = FakeEmailProvider()

    with caplog.at_level(logging.INFO):
        summary = run_reminder_job(
            _build_config(),
            spreadsheet_provider=provider,
            state_store=store,
            email_provider=email_provider,
            clock=FixedClock(date(2026, 1, 1)),
        )

    assert summary == Summary(inspected=1, matched=1, sent=1)
    assert store.claim_calls == [("test.person@example.com", 1, 1, 2026)]
    assert store.mark_sent_calls == [(101, "lease-101")]
    assert email_provider.sent_messages[0].to_email == "test.person@example.com"
    assert "standard reminder sent to test.person@example.com" in caplog.text


def test_reminder_email_uses_display_name_in_subject_when_last_name_present() -> None:
    provider = FakeSpreadsheetProvider(
        rows=[
            _row(
                name="Test",
                last_name="Person",
                email="test.person@example.com",
                reminder_date="1/1/2000",
            )
        ]
    )
    email_provider = FakeEmailProvider()

    run_reminder_job(
        _build_config(),
        spreadsheet_provider=provider,
        state_store=FakeStateStore(),
        email_provider=email_provider,
        clock=FixedClock(date(2026, 1, 1)),
    )

    sent_message = email_provider.sent_messages[0]
    assert sent_message.to_name == "Test"
    assert "Test Person" in sent_message.subject
    assert "Test Person" not in sent_message.html_body
    assert "Test Person" not in sent_message.text_body
    assert sent_message.subject == "Feliz cumpleaños, Test Person! 🎉"
    assert "Test" in sent_message.html_body
    assert "Test" in sent_message.text_body


def test_reminder_email_uses_first_name_only_when_last_name_missing() -> None:
    provider = FakeSpreadsheetProvider(
        rows=[
            _row(
                name="Test",
                email="test.person@example.com",
                reminder_date="1/1/2000",
            )
        ]
    )
    email_provider = FakeEmailProvider()

    run_reminder_job(
        _build_config(),
        spreadsheet_provider=provider,
        state_store=FakeStateStore(),
        email_provider=email_provider,
        clock=FixedClock(date(2026, 1, 1)),
    )

    sent_message = email_provider.sent_messages[0]
    assert sent_message.to_name == "Test"
    assert "Test Person" not in sent_message.subject


def test_reminder_email_subject_template_can_use_display_name() -> None:
    provider = FakeSpreadsheetProvider(
        rows=[
            _row(
                name="Test",
                last_name="Person",
                email="test.person@example.com",
                reminder_date="1/1/2000",
            )
        ]
    )
    email_provider = FakeEmailProvider()
    config = _build_config()
    config = replace(
        config,
        email_subject_template="¡Feliz cumpleaños, {{ display_name }}! 🎉",
    )

    run_reminder_job(
        config,
        spreadsheet_provider=provider,
        state_store=FakeStateStore(),
        email_provider=email_provider,
        clock=FixedClock(date(2026, 1, 1)),
    )

    sent_message = email_provider.sent_messages[0]
    assert sent_message.subject == "¡Feliz cumpleaños, Test Person! 🎉"


def test_reminder_email_normalizes_whitespace_in_name() -> None:
    provider = FakeSpreadsheetProvider(
        rows=[
            _row(
                name="  Test  ",
                last_name="  Person  ",
                email="test.person@example.com",
                reminder_date="1/1/2000",
            )
        ]
    )
    email_provider = FakeEmailProvider()

    run_reminder_job(
        _build_config(),
        spreadsheet_provider=provider,
        state_store=FakeStateStore(),
        email_provider=email_provider,
        clock=FixedClock(date(2026, 1, 1)),
    )

    assert email_provider.sent_messages[0].to_name == "Test"


def test_reminder_email_uses_estimada_salutation_for_female_gender() -> None:
    provider = FakeSpreadsheetProvider(
        rows=[
            _row(
                name="Test Person",
                email="test.person@example.com",
                reminder_date="1/1/2000",
                gender="Femenino",
            )
        ]
    )
    email_provider = FakeEmailProvider()

    run_reminder_job(
        _build_config(),
        spreadsheet_provider=provider,
        state_store=FakeStateStore(),
        email_provider=email_provider,
        clock=FixedClock(date(2026, 1, 1)),
    )

    assert FEMALE_SALUTATION in email_provider.sent_messages[0].html_body
    assert DEFAULT_SALUTATION not in email_provider.sent_messages[0].html_body


def test_reminder_email_uses_estimado_salutation_for_male_gender() -> None:
    provider = FakeSpreadsheetProvider(
        rows=[
            _row(
                name="Test Person",
                email="test.person@example.com",
                reminder_date="1/1/2000",
                gender="Masculino",
            )
        ]
    )
    email_provider = FakeEmailProvider()

    run_reminder_job(
        _build_config(),
        spreadsheet_provider=provider,
        state_store=FakeStateStore(),
        email_provider=email_provider,
        clock=FixedClock(date(2026, 1, 1)),
    )

    html_body = email_provider.sent_messages[0].html_body
    assert f"{MALE_SALUTATION} <strong>" in html_body
    assert DEFAULT_SALUTATION not in html_body


def test_reminder_email_defaults_salutation_when_gender_missing() -> None:
    provider = FakeSpreadsheetProvider(
        rows=[
            _row(
                name="Test Person",
                email="test.person@example.com",
                reminder_date="1/1/2000",
            )
        ]
    )
    email_provider = FakeEmailProvider()

    run_reminder_job(
        _build_config(),
        spreadsheet_provider=provider,
        state_store=FakeStateStore(),
        email_provider=email_provider,
        clock=FixedClock(date(2026, 1, 1)),
    )

    assert DEFAULT_SALUTATION in email_provider.sent_messages[0].html_body


def test_reminder_email_defaults_salutation_when_gender_unknown() -> None:
    provider = FakeSpreadsheetProvider(
        rows=[
            _row(
                name="Test Person",
                email="test.person@example.com",
                reminder_date="1/1/2000",
                gender="Nonbinary",
            )
        ]
    )
    email_provider = FakeEmailProvider()

    run_reminder_job(
        _build_config(),
        spreadsheet_provider=provider,
        state_store=FakeStateStore(),
        email_provider=email_provider,
        clock=FixedClock(date(2026, 1, 1)),
    )

    assert DEFAULT_SALUTATION in email_provider.sent_messages[0].html_body


def test_invalid_gender_value_does_not_invalidate_row() -> None:
    summary = run_reminder_job(
        _build_config(),
        spreadsheet_provider=FakeSpreadsheetProvider(
            rows=[
                _row(
                    name="Test Person",
                    email="test.person@example.com",
                    reminder_date="1/1/2000",
                    gender=12345,
                )
            ]
        ),
        state_store=FakeStateStore(),
        email_provider=FakeEmailProvider(),
        clock=FixedClock(date(2026, 1, 1)),
    )

    assert summary == Summary(inspected=1, matched=1, sent=1)


def test_no_reminder_due_today_has_clean_zero_send_summary() -> None:
    summary = run_reminder_job(
        _build_config(),
        spreadsheet_provider=FakeSpreadsheetProvider(
            rows=[
                _row(
                    name="Test Person",
                    email="test.person@example.com",
                    reminder_date="1/2/2000",
                )
            ]
        ),
        state_store=FakeStateStore(),
        email_provider=FakeEmailProvider(),
        clock=FixedClock(date(2026, 1, 1)),
    )

    assert summary == Summary(inspected=1)


def test_multiple_reminders_are_processed_independently() -> None:
    store = FakeStateStore()
    email_provider = FakeEmailProvider()

    summary = run_reminder_job(
        _build_config(),
        spreadsheet_provider=FakeSpreadsheetProvider(
            rows=[
                _row(
                    name="Test Person One", email="one@example.com", reminder_date="1/1/2000"
                ),
                _row(
                    name="Test Person Two", email="two@example.com", reminder_date="1/1/1999"
                ),
                _row(
                    name="Test Person Three",
                    email="three@example.com",
                    reminder_date="1/2/1998",
                ),
            ]
        ),
        state_store=store,
        email_provider=email_provider,
        clock=FixedClock(date(2026, 1, 1)),
    )

    assert summary == Summary(inspected=3, matched=2, sent=2)
    assert [message.to_email for message in email_provider.sent_messages] == [
        "one@example.com",
        "two@example.com",
    ]
    assert len(store.mark_sent_calls) == 2


def test_bp_client_sends_internal_reminder_instead_of_client_email() -> None:
    store = FakeStateStore()
    email_provider = FakeEmailProvider()

    summary = run_reminder_job(
        _build_config(),
        spreadsheet_provider=FakeSpreadsheetProvider(
            rows=[
                _row(
                    name="Test",
                    last_name="Person",
                    email="test.person@example.com",
                    reminder_date="1/1/2000",
                    service_line="BP",
                    mobile_phone="+52 55 1234 5678",
                )
            ]
        ),
        state_store=store,
        email_provider=email_provider,
        clock=FixedClock(date(2026, 1, 1)),
    )

    assert summary == Summary(inspected=1, matched=1, sent=1)
    assert store.claim_calls == [("test.person@example.com", 1, 1, 2026)]
    sent_message = email_provider.sent_messages[0]
    assert sent_message.to_email == BP_REMINDER_TO_ADDRESS_DEFAULT
    assert sent_message.to_name == "Jorge Arellano"
    assert sent_message.cc_emails == ()
    assert sent_message.subject == "Recordatorio BP: llamada de cumpleaños para Test Person"
    assert "Nombre completo: Test Person" in sent_message.text_body
    assert "Fecha de cumpleaños: 2000-01-01" in sent_message.text_body
    assert "Móvil: 525512345678" in sent_message.text_body
    assert f"Estatus: {BP_STATUS_LABEL}" in sent_message.text_body
    assert sent_message.inline_image is None


@pytest.mark.parametrize(
    "service_line",
    [
        "Vida;BP",
        "GMM, bp",
        "GMM / BP",
        "  Vida;BP, GMM, VIDA  ",
    ],
)
def test_bp_service_line_detection_handles_delimiters_whitespace_and_case(
    service_line: str,
) -> None:
    email_provider = FakeEmailProvider()

    summary = run_reminder_job(
        _build_config(),
        spreadsheet_provider=FakeSpreadsheetProvider(
            rows=[
                _row(
                    name="Delimiter Test",
                    email="delimiter@example.com",
                    reminder_date="1/1/2000",
                    service_line=service_line,
                )
            ]
        ),
        state_store=FakeStateStore(),
        email_provider=email_provider,
        clock=FixedClock(date(2026, 1, 1)),
    )

    assert summary == Summary(inspected=1, matched=1, sent=1)
    assert email_provider.sent_messages[0].to_email == BP_REMINDER_TO_ADDRESS_DEFAULT


def test_non_bp_clients_keep_standard_reminder_path() -> None:
    email_provider = FakeEmailProvider()

    summary = run_reminder_job(
        _build_config(),
        spreadsheet_provider=FakeSpreadsheetProvider(
            rows=[
                _row(
                    name="Standard",
                    last_name="Client",
                    email="standard.client@example.com",
                    reminder_date="1/1/2000",
                    gender="Femenino",
                    service_line="Vida;GMM",
                )
            ]
        ),
        state_store=FakeStateStore(),
        email_provider=email_provider,
        clock=FixedClock(date(2026, 1, 1)),
    )

    assert summary == Summary(inspected=1, matched=1, sent=1)
    sent_message = email_provider.sent_messages[0]
    assert sent_message.to_email == "standard.client@example.com"
    assert sent_message.cc_emails == ()
    assert sent_message.subject == "Feliz cumpleaños, Standard Client! 🎉"
    assert FEMALE_SALUTATION in sent_message.html_body


def test_bp_client_adds_single_configured_cc_recipient() -> None:
    email_provider = FakeEmailProvider()

    run_reminder_job(
        replace(
            _build_config(),
            bp_reminder_recipients=BPReminderRecipients(
                cc_emails=("manager1@quirongroup.com",)
            ),
        ),
        spreadsheet_provider=FakeSpreadsheetProvider(
            rows=[
                _row(
                    name="Single",
                    last_name="Cc",
                    email="single.cc@example.com",
                    reminder_date="1/1/2000",
                    service_line="BP",
                )
            ]
        ),
        state_store=FakeStateStore(),
        email_provider=email_provider,
        clock=FixedClock(date(2026, 1, 1)),
    )

    assert email_provider.sent_messages[0].cc_emails == ("manager1@quirongroup.com",)


def test_bp_client_adds_multiple_configured_cc_recipients() -> None:
    email_provider = FakeEmailProvider()

    run_reminder_job(
        replace(
            _build_config(),
            bp_reminder_recipients=BPReminderRecipients(
                cc_emails=(
                    "manager1@quirongroup.com",
                    "manager2@quirongroup.com",
                )
            ),
        ),
        spreadsheet_provider=FakeSpreadsheetProvider(
            rows=[
                _row(
                    name="Multiple",
                    last_name="Cc",
                    email="multiple.cc@example.com",
                    reminder_date="1/1/2000",
                    service_line="BP",
                )
            ]
        ),
        state_store=FakeStateStore(),
        email_provider=email_provider,
        clock=FixedClock(date(2026, 1, 1)),
    )

    assert email_provider.sent_messages[0].cc_emails == (
        "manager1@quirongroup.com",
        "manager2@quirongroup.com",
    )


@pytest.mark.parametrize(
    ("mobile_phone", "expected_mobile_value"),
    [
        ("5551234567", "5551234567"),
        ("55 5123 4567", "5551234567"),
        ("+52 55 1234 5678", "525512345678"),
        ("(832) 555-0123", "8325550123"),
        ("   ", "No disponible"),
        ("abc def", "No disponible"),
        ("12-34", "No disponible"),
    ],
)
def test_bp_reminder_normalizes_mobile_phone_for_display(
    mobile_phone: object, expected_mobile_value: str
) -> None:
    email_provider = FakeEmailProvider()

    run_reminder_job(
        _build_config(),
        spreadsheet_provider=FakeSpreadsheetProvider(
            rows=[
                _row(
                    name="No Phone",
                    email="no.phone@example.com",
                    reminder_date="1/1/2000",
                    service_line="BP",
                    mobile_phone=mobile_phone,
                )
            ]
        ),
        state_store=FakeStateStore(),
        email_provider=email_provider,
        clock=FixedClock(date(2026, 1, 1)),
    )

    sent_message = email_provider.sent_messages[0]
    assert f"Móvil: {expected_mobile_value}" in sent_message.text_body
    assert "<strong>Móvil:</strong>" in sent_message.html_body
    assert expected_mobile_value in sent_message.html_body


@pytest.mark.parametrize(
    ("service_line", "expected_recipient"),
    [
        ("BP", BP_REMINDER_TO_ADDRESS_DEFAULT),
        ("Vida", "idempotent@example.com"),
    ],
)
def test_duplicate_prevention_applies_to_bp_and_standard_paths(
    service_line: str,
    expected_recipient: str,
) -> None:
    store = FakeStateStore(
        claim_results=[ClaimResult(ClaimOutcome.ALREADY_SENT)],
    )
    email_provider = FakeEmailProvider()

    summary = run_reminder_job(
        _build_config(),
        spreadsheet_provider=FakeSpreadsheetProvider(
            rows=[
                _row(
                    name="Idempotent",
                    email="idempotent@example.com",
                    reminder_date="1/1/2000",
                    service_line=service_line,
                )
            ]
        ),
        state_store=store,
        email_provider=email_provider,
        clock=FixedClock(date(2026, 1, 1)),
    )

    assert summary == Summary(inspected=1, matched=1, duplicates=1)
    assert store.mark_sent_calls == []
    assert email_provider.sent_messages == []
    assert expected_recipient


def test_invalid_rows_are_skipped_and_do_not_block_valid_rows(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = FakeStateStore()
    email_provider = FakeEmailProvider()

    with caplog.at_level(logging.WARNING):
        summary = run_reminder_job(
            _build_config(),
            spreadsheet_provider=FakeSpreadsheetProvider(
                rows=[
                    _row(
                        name="Valid Person",
                        email="valid@example.com",
                        reminder_date="1/1/2000",
                    ),
                    _row(
                        name="Bad Birthday",
                        email="bad.birthday@example.com",
                        reminder_date="not-a-date",
                    ),
                    _row(name="No Email", email="", reminder_date="1/1/2000"),
                    {"Email": "missing.name@example.com", "Birthday": "1/1/2000"},
                    _row(name="Bad Email", email="not-an-email", reminder_date="1/1/2000"),
                ]
            ),
            state_store=store,
            email_provider=email_provider,
            clock=FixedClock(date(2026, 1, 1)),
        )

    assert summary == Summary(inspected=5, matched=1, sent=1, invalid=4)
    assert len(email_provider.sent_messages) == 1
    assert "row 3 skipped: missing or invalid reminder date" in caplog.text
    assert "row 4 skipped: missing or invalid email" in caplog.text
    assert "row 5 skipped: missing name" in caplog.text
    assert "row 6 skipped: missing or invalid email" in caplog.text


def test_dry_run_does_not_claim_or_send(caplog: pytest.LogCaptureFixture) -> None:
    config = _build_config(dry_run=True)
    store = FakeStateStore()
    email_provider = FakeEmailProvider()

    with caplog.at_level(logging.INFO):
        summary = run_reminder_job(
            config,
            spreadsheet_provider=FakeSpreadsheetProvider(
                rows=[
                    _row(
                        name="Test Person",
                        email="test.person@example.com",
                        reminder_date="1/1/2000",
                    )
                ]
            ),
            state_store=store,
            email_provider=email_provider,
            clock=FixedClock(date(2026, 1, 1)),
        )

    assert summary == Summary(inspected=1, matched=1)
    assert store.claim_calls == []
    assert store.mark_sent_calls == []
    assert store.mark_failed_calls == []
    assert email_provider.sent_messages == []
    assert (
        "[DRY RUN] would send standard reminder to test.person@example.com (Test Person)"
        in caplog.text
    )


def test_dry_run_bp_route_does_not_claim_or_send(
    caplog: pytest.LogCaptureFixture,
) -> None:
    config = _build_config(dry_run=True)
    store = FakeStateStore()
    email_provider = FakeEmailProvider()

    with caplog.at_level(logging.INFO):
        summary = run_reminder_job(
            config,
            spreadsheet_provider=FakeSpreadsheetProvider(
                rows=[
                    _row(
                        name="BP Dry Run",
                        email="bp.dry.run@example.com",
                        reminder_date="1/1/2000",
                        service_line=" bp ",
                    )
                ]
            ),
            state_store=store,
            email_provider=email_provider,
            clock=FixedClock(date(2026, 1, 1)),
        )

    assert summary == Summary(inspected=1, matched=1)
    assert store.claim_calls == []
    assert email_provider.sent_messages == []
    assert (
        "[DRY RUN] would send BP call reminder for BP Dry Run to "
        f"{BP_REMINDER_TO_ADDRESS_DEFAULT}"
    ) in caplog.text


def test_dry_run_with_matching_rows_does_not_build_email_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.reminder_service.build_email_provider",
        lambda config: pytest.fail("build_email_provider should not be called"),
    )

    summary = run_reminder_job(
        _build_config(dry_run=True),
        spreadsheet_provider=FakeSpreadsheetProvider(
            rows=[
                _row(
                    name="Dry Run Person",
                    email="dry.run@example.com",
                    reminder_date="1/1/2000",
                )
            ]
        ),
        state_store=FakeStateStore(),
        clock=FixedClock(date(2026, 1, 1)),
    )

    assert summary == Summary(inspected=1, matched=1)


def test_dry_run_with_matching_rows_skips_state_store_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingStateStore:
        def __init__(self, db_path: Path, stale_claim_timeout_minutes: int) -> None:
            raise RuntimeError("synthetic state store construction failure")

    monkeypatch.setattr("app.reminder_service.StateStore", FailingStateStore)

    summary = run_reminder_job(
        _build_config(dry_run=True),
        spreadsheet_provider=FakeSpreadsheetProvider(
            rows=[
                _row(
                    name="Dry Run Person",
                    email="dry.run@example.com",
                    reminder_date="1/1/2000",
                )
            ]
        ),
        clock=FixedClock(date(2026, 1, 1)),
    )

    assert summary == Summary(inspected=1, matched=1)


def test_dry_run_with_matching_rows_skips_local_image_loading(
    caplog: pytest.LogCaptureFixture,
) -> None:
    config = _build_config(
        dry_run=True,
        birthday_image_mode="local",
        birthday_image_path=Path("synthetic-missing-image.gif"),
    )

    with caplog.at_level(logging.INFO):
        summary = run_reminder_job(
            config,
            spreadsheet_provider=FakeSpreadsheetProvider(
                rows=[
                    _row(
                        name="Dry Run Person",
                        email="dry.run@example.com",
                        reminder_date="1/1/2000",
                    )
                ]
            ),
            state_store=FakeStateStore(),
            clock=FixedClock(date(2026, 1, 1)),
        )

    assert summary == Summary(inspected=1, matched=1)
    assert (
        "[DRY RUN] would send standard reminder to dry.run@example.com (Dry Run Person)"
        in caplog.text
    )


def test_zero_matching_birthdays_does_not_build_email_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.reminder_service.build_email_provider",
        lambda config: pytest.fail("build_email_provider should not be called"),
    )

    summary = run_reminder_job(
        _build_config(),
        spreadsheet_provider=FakeSpreadsheetProvider(
            rows=[
                _row(
                    name="No Match Person",
                    email="no.match@example.com",
                    reminder_date="1/2/2000",
                )
            ]
        ),
        state_store=FakeStateStore(),
        clock=FixedClock(date(2026, 1, 1)),
    )

    assert summary == Summary(inspected=1)


def test_zero_matching_birthdays_skip_state_store_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingStateStore:
        def __init__(self, db_path: Path, stale_claim_timeout_minutes: int) -> None:
            raise RuntimeError("synthetic state store construction failure")

    monkeypatch.setattr("app.reminder_service.StateStore", FailingStateStore)

    summary = run_reminder_job(
        _build_config(),
        spreadsheet_provider=FakeSpreadsheetProvider(
            rows=[
                _row(
                    name="No Match Person",
                    email="no.match@example.com",
                    reminder_date="1/2/2000",
                )
            ]
        ),
        clock=FixedClock(date(2026, 1, 1)),
    )

    assert summary == Summary(inspected=1)


def test_zero_matching_birthdays_skip_local_image_loading() -> None:
    summary = run_reminder_job(
        _build_config(
            birthday_image_mode="local",
            birthday_image_path=Path("synthetic-missing-image.gif"),
        ),
        spreadsheet_provider=FakeSpreadsheetProvider(
            rows=[
                _row(
                    name="No Match Person",
                    email="no.match@example.com",
                    reminder_date="1/2/2000",
                )
            ]
        ),
        state_store=FakeStateStore(),
        clock=FixedClock(date(2026, 1, 1)),
    )

    assert summary == Summary(inspected=1)


def test_real_matches_build_and_close_state_store_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created_stores: list[TrackingStateStore] = []

    class TrackingStateStore(FakeStateStore):
        def __init__(self, db_path: Path, stale_claim_timeout_minutes: int) -> None:
            super().__init__()
            self.db_path = db_path
            self.stale_claim_timeout_minutes = stale_claim_timeout_minutes
            self.close_calls = 0
            created_stores.append(self)

        def close(self) -> None:
            self.close_calls += 1

    monkeypatch.setattr("app.reminder_service.StateStore", TrackingStateStore)

    email_provider = FakeEmailProvider()
    summary = run_reminder_job(
        _build_config(),
        spreadsheet_provider=FakeSpreadsheetProvider(
            rows=[
                _row(
                    name="Match One",
                    email="match.one@example.com",
                    reminder_date="1/1/2000",
                )
            ]
        ),
        email_provider=email_provider,
        clock=FixedClock(date(2026, 1, 1)),
    )

    assert summary == Summary(inspected=1, matched=1, sent=1)
    assert len(created_stores) == 1
    assert created_stores[0].claim_calls == [("match.one@example.com", 1, 1, 2026)]
    assert created_stores[0].mark_sent_calls == [(101, "lease-101")]
    assert created_stores[0].close_calls == 1
    assert email_provider.sent_messages[0].to_email == "match.one@example.com"


def test_real_matches_build_email_provider_once_and_reuse_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    build_calls = 0
    fake_mailer = FakeEmailProvider()

    def fake_build_email_provider(config: Config) -> EmailProvider:
        nonlocal build_calls
        build_calls += 1
        return fake_mailer

    monkeypatch.setattr(
        "app.reminder_service.build_email_provider", fake_build_email_provider
    )

    summary = run_reminder_job(
        _build_config(),
        spreadsheet_provider=FakeSpreadsheetProvider(
            rows=[
                _row(
                    name="Match One",
                    email="match.one@example.com",
                    reminder_date="1/1/2000",
                ),
                _row(
                    name="Match Two",
                    email="match.two@example.com",
                    reminder_date="1/1/1999",
                ),
            ]
        ),
        state_store=FakeStateStore(),
        clock=FixedClock(date(2026, 1, 1)),
    )

    assert summary == Summary(inspected=2, matched=2, sent=2)
    assert build_calls == 1
    assert [message.to_email for message in fake_mailer.sent_messages] == [
        "match.one@example.com",
        "match.two@example.com",
    ]


def test_real_matches_load_local_image_once_and_reuse_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    image_path = tmp_path / "synthetic-banner.jpg"
    image_bytes = b"synthetic-jpeg-bytes"
    image_path.write_bytes(image_bytes)

    read_calls = 0
    original_read_bytes = Path.read_bytes

    def counting_read_bytes(self: Path) -> bytes:
        nonlocal read_calls
        if self == image_path:
            read_calls += 1
        return original_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", counting_read_bytes)

    email_provider = FakeEmailProvider()
    summary = run_reminder_job(
        _build_config(
            birthday_image_mode="local",
            birthday_image_path=image_path,
        ),
        spreadsheet_provider=FakeSpreadsheetProvider(
            rows=[
                _row(
                    name="Match One",
                    email="match.one@example.com",
                    reminder_date="1/1/2000",
                ),
                _row(
                    name="Match Two",
                    email="match.two@example.com",
                    reminder_date="1/1/1999",
                ),
            ]
        ),
        state_store=FakeStateStore(),
        email_provider=email_provider,
        clock=FixedClock(date(2026, 1, 1)),
    )

    assert summary == Summary(inspected=2, matched=2, sent=2)
    assert read_calls == 1
    assert len(email_provider.sent_messages) == 2
    assert email_provider.sent_messages[0].inline_image is not None
    assert email_provider.sent_messages[0].inline_image.data == image_bytes
    assert email_provider.sent_messages[0].inline_image.mime_type == "image/jpeg"
    assert email_provider.sent_messages[1].inline_image is not None
    assert email_provider.sent_messages[1].inline_image.data == image_bytes


def test_test_date_clock_is_used_instead_of_real_date() -> None:
    config = _build_config(test_date=date(2026, 2, 14))
    summary = run_reminder_job(
        config,
        spreadsheet_provider=FakeSpreadsheetProvider(
            rows=[
                _row(
                    name="Valentine Test",
                    email="valentine@example.com",
                    reminder_date="2/14/2000",
                )
            ]
        ),
        state_store=FakeStateStore(),
        email_provider=FakeEmailProvider(),
    )

    assert summary == Summary(inspected=1, matched=1, sent=1)


def test_spreadsheet_mode_selection_google_sheet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    google_provider = FakeSpreadsheetProvider(rows=[])
    monkeypatch.setattr(
        "app.reminder_service.GoogleSheetsProvider.from_credentials",
        classmethod(lambda cls, config, credentials: google_provider),
    )

    provider = build_spreadsheet_provider(
        _build_config(spreadsheet_mode="google_sheet"), credentials=object()
    )

    assert provider is google_provider


def test_spreadsheet_mode_selection_xlsx_drive(monkeypatch: pytest.MonkeyPatch) -> None:
    drive_provider = FakeSpreadsheetProvider(rows=[])
    monkeypatch.setattr(
        "app.reminder_service.XlsxDriveProvider.from_credentials",
        classmethod(lambda cls, config, credentials: drive_provider),
    )

    provider = build_spreadsheet_provider(
        _build_config(spreadsheet_mode="xlsx_drive"), credentials=object()
    )

    assert provider is drive_provider


@pytest.mark.parametrize(
    ("spreadsheet_mode", "expected_scope"),
    [
        ("google_sheet", "https://www.googleapis.com/auth/spreadsheets.readonly"),
        ("xlsx_drive", "https://www.googleapis.com/auth/drive.readonly"),
    ],
)
def test_build_spreadsheet_provider_requests_only_read_scope(
    monkeypatch: pytest.MonkeyPatch,
    spreadsheet_mode: str,
    expected_scope: str,
) -> None:
    captured_scopes: list[str] = []
    created_credentials: list[FakeCredentials] = []
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
        credentials = FakeCredentials()
        created_credentials.append(credentials)
        return credentials

    monkeypatch.setattr(
        "app.reminder_service.service_account.Credentials.from_service_account_file",
        fake_from_service_account_file,
    )
    monkeypatch.setattr(
        "app.reminder_service.GoogleSheetsProvider.from_credentials",
        classmethod(
            lambda cls, config, credentials: (
                provider_credentials.append(credentials),
                FakeSpreadsheetProvider(rows=[]),
            )[1]
        ),
    )
    monkeypatch.setattr(
        "app.reminder_service.XlsxDriveProvider.from_credentials",
        classmethod(
            lambda cls, config, credentials: (
                provider_credentials.append(credentials),
                FakeSpreadsheetProvider(rows=[]),
            )[1]
        ),
    )

    build_spreadsheet_provider(_build_config(spreadsheet_mode=spreadsheet_mode))

    assert captured_scopes == [expected_scope]
    assert "https://www.googleapis.com/auth/gmail.send" not in captured_scopes
    assert len(created_credentials) == 1
    assert provider_credentials == [created_credentials[0]]
    assert created_credentials[0].with_subject_calls == []


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

    config = _build_config()
    provider = build_email_provider(config)

    assert isinstance(provider, FakeEmailProvider)
    assert captured_scopes == ["https://www.googleapis.com/auth/gmail.send"]
    assert len(provider_credentials) == 1
    assert provider_credentials[0].with_subject_calls == [
        config.google_impersonate_subject
    ]


class _FakeOAuthCredentials:
    def __init__(
        self, *, expired: bool = False, valid: bool = True, refresh_token: str | None = None
    ) -> None:
        self.expired = expired
        self.valid = valid
        self.refresh_token = refresh_token
        self.refresh_calls = 0
        self.to_json_calls = 0

    def refresh(self, request: object) -> None:
        self.refresh_calls += 1
        self.expired = False
        self.valid = True

    def to_json(self) -> str:
        self.to_json_calls += 1
        return "{}"


def test_build_spreadsheet_provider_oauth_mode_reuses_valid_cached_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    token_path = tmp_path / "token.json"
    token_path.write_text("{}", encoding="utf-8")
    cached_credentials = _FakeOAuthCredentials()
    captured_scopes: list[list[str]] = []
    provider_credentials: list[object] = []

    monkeypatch.setattr(
        "app.reminder_service.GoogleOAuthCredentials.from_authorized_user_file",
        lambda filename, scopes: (captured_scopes.append(scopes), cached_credentials)[
            1
        ],
    )
    monkeypatch.setattr(
        "google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file",
        classmethod(
            lambda cls, filename, scopes: (_ for _ in ()).throw(
                AssertionError("interactive flow should not run for a valid token")
            )
        ),
    )
    monkeypatch.setattr(
        "app.reminder_service.GoogleSheetsProvider.from_credentials",
        classmethod(
            lambda cls, config, credentials: (
                provider_credentials.append(credentials),
                FakeSpreadsheetProvider(rows=[]),
            )[1]
        ),
    )

    config = _build_config(
        spreadsheet_mode="google_sheet",
        google_auth_mode="oauth",
        google_credentials_file=None,
        google_oauth_client_secrets_file=Path("synthetic-client-secrets.json"),
        google_oauth_token_file=token_path,
    )

    build_spreadsheet_provider(config)

    assert captured_scopes == [
        [
            "https://www.googleapis.com/auth/gmail.send",
            "https://www.googleapis.com/auth/spreadsheets.readonly",
            "https://www.googleapis.com/auth/drive.readonly",
        ]
    ]
    assert provider_credentials == [cached_credentials]
    assert token_path.read_text(encoding="utf-8") == "{}"


def test_build_email_provider_oauth_mode_does_not_call_with_subject(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    token_path = tmp_path / "token.json"
    token_path.write_text("{}", encoding="utf-8")
    cached_credentials = _FakeOAuthCredentials()
    provider_credentials: list[object] = []

    monkeypatch.setattr(
        "app.reminder_service.GoogleOAuthCredentials.from_authorized_user_file",
        lambda filename, scopes: cached_credentials,
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

    config = _build_config(
        google_auth_mode="oauth",
        google_credentials_file=None,
        google_oauth_client_secrets_file=Path("synthetic-client-secrets.json"),
        google_oauth_token_file=token_path,
    )

    provider = build_email_provider(config)

    assert isinstance(provider, FakeEmailProvider)
    # _FakeOAuthCredentials has no with_subject method; a call to it would
    # raise AttributeError, so reaching here proves it was never attempted.
    assert provider_credentials == [cached_credentials]


def test_oauth_credentials_refresh_expired_token_without_running_flow(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    token_path = tmp_path / "token.json"
    token_path.write_text("{}", encoding="utf-8")
    expired_credentials = _FakeOAuthCredentials(
        expired=True, valid=False, refresh_token="synthetic-refresh-token"
    )

    monkeypatch.setattr(
        "app.reminder_service.GoogleOAuthCredentials.from_authorized_user_file",
        lambda filename, scopes: expired_credentials,
    )
    monkeypatch.setattr(
        "google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file",
        classmethod(
            lambda cls, filename, scopes: (_ for _ in ()).throw(
                AssertionError("interactive flow should not run when a refresh token exists")
            )
        ),
    )
    monkeypatch.setattr(
        "app.reminder_service.GmailProvider.from_credentials",
        classmethod(lambda cls, config, credentials: FakeEmailProvider()),
    )

    config = _build_config(
        google_auth_mode="oauth",
        google_credentials_file=None,
        google_oauth_client_secrets_file=Path("synthetic-client-secrets.json"),
        google_oauth_token_file=token_path,
    )

    build_email_provider(config)

    assert expired_credentials.refresh_calls == 1
    assert expired_credentials.to_json_calls == 1
    assert token_path.read_text(encoding="utf-8") == "{}"


def test_oauth_credentials_refresh_expired_token_without_persisting_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    token_path = tmp_path / "token.json"
    original_token = '{"access_token":"stale"}'
    token_path.write_text(original_token, encoding="utf-8")
    expired_credentials = _FakeOAuthCredentials(
        expired=True, valid=False, refresh_token="synthetic-refresh-token"
    )
    provider_credentials: list[object] = []

    monkeypatch.setattr(
        "app.reminder_service.GoogleOAuthCredentials.from_authorized_user_file",
        lambda filename, scopes: expired_credentials,
    )
    monkeypatch.setattr(
        "google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file",
        classmethod(
            lambda cls, filename, scopes: (_ for _ in ()).throw(
                AssertionError("interactive flow should not run when a refresh token exists")
            )
        ),
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

    config = _build_config(
        google_auth_mode="oauth",
        google_credentials_file=None,
        google_oauth_client_secrets_file=Path("synthetic-client-secrets.json"),
        google_oauth_token_file=token_path,
        google_oauth_token_persist=False,
    )

    build_email_provider(config)

    assert expired_credentials.refresh_calls == 1
    assert expired_credentials.to_json_calls == 0
    assert provider_credentials == [expired_credentials]
    assert token_path.read_text(encoding="utf-8") == original_token


def test_oauth_credentials_valid_cached_token_without_persisting_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    token_path = tmp_path / "token.json"
    original_token = '{"access_token":"current"}'
    token_path.write_text(original_token, encoding="utf-8")
    cached_credentials = _FakeOAuthCredentials()
    provider_credentials: list[object] = []

    monkeypatch.setattr(
        "app.reminder_service.GoogleOAuthCredentials.from_authorized_user_file",
        lambda filename, scopes: cached_credentials,
    )
    monkeypatch.setattr(
        "google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file",
        classmethod(
            lambda cls, filename, scopes: (_ for _ in ()).throw(
                AssertionError("interactive flow should not run for a valid token")
            )
        ),
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

    config = _build_config(
        google_auth_mode="oauth",
        google_credentials_file=None,
        google_oauth_client_secrets_file=Path("synthetic-client-secrets.json"),
        google_oauth_token_file=token_path,
        google_oauth_token_persist=False,
    )

    build_email_provider(config)

    assert cached_credentials.refresh_calls == 0
    assert cached_credentials.to_json_calls == 0
    assert provider_credentials == [cached_credentials]
    assert token_path.read_text(encoding="utf-8") == original_token


def test_oauth_credentials_runs_installed_app_flow_when_no_cached_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    token_path = tmp_path / "nested" / "token.json"
    fresh_credentials = _FakeOAuthCredentials()
    captured_secrets_files: list[str] = []

    class FakeFlow:
        def run_local_server(self, *, port: int) -> _FakeOAuthCredentials:
            return fresh_credentials

    monkeypatch.setattr(
        "google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file",
        classmethod(
            lambda cls, filename, scopes: (
                captured_secrets_files.append(filename),
                FakeFlow(),
            )[1]
        ),
    )
    monkeypatch.setattr(
        "app.reminder_service.GmailProvider.from_credentials",
        classmethod(lambda cls, config, credentials: FakeEmailProvider()),
    )

    config = _build_config(
        google_auth_mode="oauth",
        google_credentials_file=None,
        google_oauth_client_secrets_file=Path("synthetic-client-secrets.json"),
        google_oauth_token_file=token_path,
    )

    build_email_provider(config)

    assert captured_secrets_files == ["synthetic-client-secrets.json"]
    assert token_path.is_file()
    assert token_path.read_text(encoding="utf-8") == "{}"


def test_email_provider_failure_marks_failed_and_continues() -> None:
    store = FakeStateStore()
    email_provider = FakeEmailProvider(
        effects=[
            EmailSendError("synthetic failure"),
            None,
        ]
    )

    summary = run_reminder_job(
        _build_config(),
        spreadsheet_provider=FakeSpreadsheetProvider(
            rows=[
                _row(
                    name="Failure Case",
                    email="failure@example.com",
                    reminder_date="1/1/2000",
                ),
                _row(
                    name="Success Case",
                    email="success@example.com",
                    reminder_date="1/1/2000",
                ),
            ]
        ),
        state_store=store,
        email_provider=email_provider,
        clock=FixedClock(date(2026, 1, 1)),
    )

    assert summary == Summary(inspected=2, matched=2, sent=1, failed=1)
    assert store.mark_failed_calls == [(101, "lease-101")]
    assert store.mark_sent_calls == [(102, "lease-102")]


def test_duplicate_claim_skips_send() -> None:
    summary = run_reminder_job(
        _build_config(),
        spreadsheet_provider=FakeSpreadsheetProvider(
            rows=[
                _row(
                    name="Duplicate Person",
                    email="dup@example.com",
                    reminder_date="1/1/2000",
                )
            ]
        ),
        state_store=FakeStateStore(
            claim_results=[ClaimResult(ClaimOutcome.ALREADY_SENT)]
        ),
        email_provider=FakeEmailProvider(),
        clock=FixedClock(date(2026, 1, 1)),
    )

    assert summary == Summary(inspected=1, matched=1, duplicates=1)


def test_duplicate_claim_skips_render_and_local_image_loading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    read_calls = 0
    original_read_bytes = Path.read_bytes

    def counting_read_bytes(self: Path) -> bytes:
        nonlocal read_calls
        if self == Path("synthetic-missing-image.gif"):
            read_calls += 1
        return original_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", counting_read_bytes)

    summary = run_reminder_job(
        _build_config(
            birthday_image_mode="local",
            birthday_image_path=Path("synthetic-missing-image.gif"),
        ),
        spreadsheet_provider=FakeSpreadsheetProvider(
            rows=[
                _row(
                    name="Duplicate Person",
                    email="dup@example.com",
                    reminder_date="1/1/2000",
                )
            ]
        ),
        state_store=FakeStateStore(
            claim_results=[ClaimResult(ClaimOutcome.ALREADY_SENT)]
        ),
        email_provider=FakeEmailProvider(),
        clock=FixedClock(date(2026, 1, 1)),
    )

    assert summary == Summary(inspected=1, matched=1, duplicates=1)
    assert read_calls == 0


def test_in_progress_claim_skips_send() -> None:
    summary = run_reminder_job(
        _build_config(),
        spreadsheet_provider=FakeSpreadsheetProvider(
            rows=[
                _row(
                    name="In Progress Person",
                    email="progress@example.com",
                    reminder_date="1/1/2000",
                )
            ]
        ),
        state_store=FakeStateStore(
            claim_results=[ClaimResult(ClaimOutcome.IN_PROGRESS)]
        ),
        email_provider=FakeEmailProvider(),
        clock=FixedClock(date(2026, 1, 1)),
    )

    assert summary == Summary(inspected=1, matched=1, in_progress=1)


def test_ambiguous_send_marks_failed_and_logs_critical(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = FakeStateStore()
    email_provider = FakeEmailProvider(
        effects=[AmbiguousSendError("synthetic ambiguous failure")]
    )

    with caplog.at_level(logging.INFO):
        summary = run_reminder_job(
            _build_config(),
            spreadsheet_provider=FakeSpreadsheetProvider(
                rows=[
                    _row(
                        name="Ambiguous Person",
                        email="ambiguous@example.com",
                        reminder_date="1/1/2000",
                    )
                ]
            ),
            state_store=store,
            email_provider=email_provider,
            clock=FixedClock(date(2026, 1, 1)),
        )

    assert summary == Summary(inspected=1, matched=1, ambiguous=1)
    assert store.mark_failed_calls == []
    critical_records = [
        record for record in caplog.records if record.levelno == logging.CRITICAL
    ]
    assert critical_records
    assert "outcome is unknown" in critical_records[0].getMessage()
    assert "duplicate email" in critical_records[0].getMessage()
    assert "ambiguous@example.com" in critical_records[0].getMessage()


def test_ambiguous_send_does_not_abort_batch_or_mark_failed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = FakeStateStore()
    email_provider = FakeEmailProvider(
        effects=[AmbiguousSendError("synthetic ambiguous failure"), None]
    )

    with caplog.at_level(logging.INFO):
        summary = run_reminder_job(
            _build_config(),
            spreadsheet_provider=FakeSpreadsheetProvider(
                rows=[
                    _row(
                        name="Ambiguous Person",
                        email="ambiguous@example.com",
                        reminder_date="1/1/2000",
                    ),
                    _row(
                        name="Success Person",
                        email="success@example.com",
                        reminder_date="1/1/2000",
                    ),
                ]
            ),
            state_store=store,
            email_provider=email_provider,
            clock=FixedClock(date(2026, 1, 1)),
        )

    assert summary == Summary(inspected=2, matched=2, sent=1, ambiguous=1)
    assert store.mark_failed_calls == []
    assert store.mark_sent_calls == [(102, "lease-102")]
    assert "standard reminder sent to success@example.com" in caplog.text


def test_email_send_failure_mark_failed_lease_loss_does_not_abort_batch(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = FakeStateStore(mark_failed_effects=[LeaseLostError("synthetic lease loss")])
    email_provider = FakeEmailProvider(
        effects=[EmailSendError("synthetic failure"), None]
    )

    with caplog.at_level(logging.INFO):
        summary = run_reminder_job(
            _build_config(),
            spreadsheet_provider=FakeSpreadsheetProvider(
                rows=[
                    _row(
                        name="Failure Person",
                        email="failure@example.com",
                        reminder_date="1/1/2000",
                    ),
                    _row(
                        name="Success Person",
                        email="success@example.com",
                        reminder_date="1/1/2000",
                    ),
                ]
            ),
            state_store=store,
            email_provider=email_provider,
            clock=FixedClock(date(2026, 1, 1)),
        )

    assert summary == Summary(inspected=2, matched=2, sent=1, failed=1)
    assert store.mark_failed_calls == [(101, "lease-101")]
    assert store.mark_sent_calls == [(102, "lease-102")]
    assert "failed to mark claim as failed for failure@example.com" in caplog.text
    assert "standard reminder sent to success@example.com" in caplog.text


def test_mark_sent_lease_loss_after_send_is_ambiguous_and_critical(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = FakeStateStore(mark_sent_effects=[LeaseLostError("synthetic lease loss")])
    email_provider = FakeEmailProvider()

    with caplog.at_level(logging.INFO):
        summary = run_reminder_job(
            _build_config(),
            spreadsheet_provider=FakeSpreadsheetProvider(
                rows=[
                    _row(
                        name="Lease Loss Person",
                        email="lease-loss@example.com",
                        reminder_date="1/1/2000",
                    )
                ]
            ),
            state_store=store,
            email_provider=email_provider,
            clock=FixedClock(date(2026, 1, 1)),
        )

    assert summary == Summary(inspected=1, matched=1, ambiguous=1)
    assert store.mark_sent_calls == [(101, "lease-101")]
    assert store.mark_failed_calls == []
    critical_records = [
        record for record in caplog.records if record.levelno == logging.CRITICAL
    ]
    assert critical_records
    critical_message = critical_records[0].getMessage()
    assert "standard reminder was sent to lease-loss@example.com" in critical_message
    assert "could not durably record it" in critical_message
    assert "duplicate email" in critical_message


def test_mark_sent_generic_error_after_send_is_ambiguous_and_critical(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = FakeStateStore(mark_sent_effects=[RuntimeError("synthetic store failure")])
    email_provider = FakeEmailProvider()

    with caplog.at_level(logging.INFO):
        summary = run_reminder_job(
            _build_config(),
            spreadsheet_provider=FakeSpreadsheetProvider(
                rows=[
                    _row(
                        name="Store Failure Person",
                        email="store-failure@example.com",
                        reminder_date="1/1/2000",
                    )
                ]
            ),
            state_store=store,
            email_provider=email_provider,
            clock=FixedClock(date(2026, 1, 1)),
        )

    assert summary == Summary(inspected=1, matched=1, ambiguous=1)
    assert summary.sent == 0
    assert summary.failed == 0
    assert store.mark_sent_calls == [(101, "lease-101")]
    assert store.mark_failed_calls == []
    critical_records = [
        record for record in caplog.records if record.levelno == logging.CRITICAL
    ]
    assert critical_records
    critical_message = critical_records[0].getMessage()
    assert "standard reminder was sent to store-failure@example.com" in critical_message
    assert "could not durably record it" in critical_message
    assert "duplicate email" in critical_message


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
    monkeypatch.setattr("app.main.load_config", _build_config)
    monkeypatch.setattr(
        "app.main.run_reminder_job",
        lambda config, *, clock=None: Summary(
            inspected=7,
            matched=4,
            sent=2,
            duplicates=1,
            invalid=3,
        ),
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 0
    stderr = capsys.readouterr().err
    assert (
        "INFO app.main summary: inspected=7 matched=4 sent=2 duplicates=1 "
        "in_progress=0 invalid=3 failed=0 ambiguous=0" in stderr
    )
    assert "INFO app.main job completed" in stderr
    assert "ERROR app.main" not in stderr


def test_main_exits_one_when_summary_has_failed_sends(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("app.main.load_config", _build_config)
    monkeypatch.setattr(
        "app.main.run_reminder_job",
        lambda config, *, clock=None: Summary(
            inspected=2,
            matched=2,
            sent=1,
            failed=1,
        ),
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 1
    stderr = capsys.readouterr().err
    assert (
        "ERROR app.main job completed with delivery issues: failed=1 ambiguous=0 "
        "in_progress=0" in stderr
    )
    assert "INFO app.main job completed\n" not in stderr


def test_main_exits_one_when_summary_has_ambiguous_sends(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("app.main.load_config", _build_config)
    monkeypatch.setattr(
        "app.main.run_reminder_job",
        lambda config, *, clock=None: Summary(
            inspected=1,
            matched=1,
            ambiguous=1,
        ),
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 1
    stderr = capsys.readouterr().err
    assert (
        "ERROR app.main job completed with delivery issues: failed=0 ambiguous=1 "
        "in_progress=0" in stderr
    )
    assert "INFO app.main job completed\n" not in stderr


def test_main_exits_one_when_summary_has_in_progress_sends(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("app.main.load_config", _build_config)
    monkeypatch.setattr(
        "app.main.run_reminder_job",
        lambda config, *, clock=None: Summary(
            inspected=1,
            matched=1,
            in_progress=1,
        ),
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 1
    stderr = capsys.readouterr().err
    assert (
        "ERROR app.main job completed with delivery issues: failed=0 ambiguous=0 "
        "in_progress=1" in stderr
    )
    assert "INFO app.main job completed\n" not in stderr


def test_main_uses_single_clock_for_logging_and_job_execution(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class AdvancingClock:
        def __init__(self) -> None:
            self._dates = [date(2026, 1, 1), date(2026, 1, 2)]
            self.calls = 0

        def today(self) -> date:
            value = self._dates[min(self.calls, len(self._dates) - 1)]
            self.calls += 1
            return value

    clock = AdvancingClock()
    observed_dates: list[date] = []

    monkeypatch.setattr("app.main.load_config", _build_config)
    monkeypatch.setattr("app.main.build_clock", lambda config: clock)

    def fake_run_reminder_job(config: Config, *, clock: object = None) -> Summary:
        assert clock is not None
        observed_dates.append(clock.today())  # type: ignore[union-attr]
        return Summary()

    monkeypatch.setattr("app.main.run_reminder_job", fake_run_reminder_job)

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 0
    assert observed_dates == [date(2026, 1, 1)]
    assert clock.calls == 1
    stderr = capsys.readouterr().err
    assert "INFO app.main job starting: date=2026-01-01 " in stderr


class FakeSpreadsheetProvider(SpreadsheetProvider):
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows

    def load_rows(self) -> list[dict[str, object]]:
        return list(self.rows)


@dataclass
class FakeStateStore:
    claim_results: list[ClaimResult] = field(default_factory=list)
    claim_calls: list[tuple[str, int, int, int]] = field(default_factory=list)
    mark_sent_calls: list[tuple[int, str]] = field(default_factory=list)
    mark_failed_calls: list[tuple[int, str]] = field(default_factory=list)
    mark_sent_effects: list[Exception] = field(default_factory=list)
    mark_failed_effects: list[Exception] = field(default_factory=list)
    _claim_counter: int = 100

    def claim(self, email: str, month: int, day: int, year: int) -> ClaimResult:
        self.claim_calls.append((email, month, day, year))
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


def _build_config(
    *,
    dry_run: bool = False,
    spreadsheet_mode: str = "google_sheet",
    test_date: date | None = None,
    birthday_image_mode: str = "url",
    birthday_image_path: Path = Path("app/assets/birthday_banner.jpg"),
    google_auth_mode: str = "service_account",
    google_credentials_file: Path | None = Path("synthetic-credentials.json"),
    google_oauth_client_secrets_file: Path | None = None,
    google_oauth_token_file: Path = Path("synthetic-oauth-token.json"),
    google_oauth_token_persist: bool = True,
) -> Config:
    return Config(
        app_timezone="America/Chicago",
        dry_run=dry_run,
        test_date=test_date,
        spreadsheet_mode=spreadsheet_mode,
        google_sheet_id="synthetic-sheet-id",
        google_sheet_tab="Birthdays",
        google_drive_file_id="synthetic-drive-id",
        name_column="Name",
        last_name_column="Last Name",
        gender_column="Gender",
        service_line_column="Línea de servicio",
        mobile_phone_column="Móvil",
        email_column="Email",
        birthday_column="Birthday",
        last_sent_year_column="Last Birthday Email Year",
        email_provider="gmail",
        email_from_name="Example Sender",
        email_from_address="sender@example.com",
        email_subject_template=EMAIL_SUBJECT_TEMPLATE_DEFAULT,
        google_auth_mode=google_auth_mode,
        google_credentials_file=google_credentials_file,
        google_impersonate_subject="sender@example.com",
        google_oauth_client_secrets_file=google_oauth_client_secrets_file,
        google_oauth_token_file=google_oauth_token_file,
        google_oauth_token_persist=google_oauth_token_persist,
        birthday_image_mode=birthday_image_mode,
        birthday_image_path=birthday_image_path,
        birthday_image_url="https://assets.example.com/birthday-banner.jpg",
        birthday_image_alt=DEFAULT_BIRTHDAY_IMAGE_ALT,
        birthday_image_width=600,
        state_backend="sqlite",
        state_db_path=Path("synthetic-state.db"),
        firestore_database="birthday-automation",
        stale_claim_timeout_minutes=30,
        retry_max_attempts=3,
        retry_base_delay_seconds=0.25,
        log_level="INFO",
        bp_reminder_recipients=BPReminderRecipients(),
    )


def _row(
    *,
    name: str,
    email: str,
    reminder_date: object,
    last_name: object = None,
    gender: object = None,
    service_line: object = None,
    mobile_phone: object = None,
) -> dict[str, object]:
    row: dict[str, object] = {
        "Name": name,
        "Email": email,
        "Birthday": reminder_date,
    }
    if last_name is not None:
        row["Last Name"] = last_name
    if gender is not None:
        row["Gender"] = gender
    if service_line is not None:
        row["Línea de servicio"] = service_line
    if mobile_phone is not None:
        row["Móvil"] = mobile_phone
    return row


def _raise_config_error() -> Config:
    raise ConfigError("synthetic config error")
