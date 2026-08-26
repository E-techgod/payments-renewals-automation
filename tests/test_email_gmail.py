from __future__ import annotations

import base64
from email import policy
from email.parser import BytesParser
from pathlib import Path
from types import SimpleNamespace

import pytest
from googleapiclient.errors import HttpError  # type: ignore[import-untyped]
from httplib2 import HttpLib2Error  # type: ignore[import-untyped]

from app.config import Config
from app.email.base import AmbiguousSendError, EmailMessage, EmailSendError, InlineImage
from app.email_content import (
    BPReminderRecipients,
    DEFAULT_BIRTHDAY_IMAGE_ALT,
    DEFAULT_SALUTATION,
    EMAIL_SUBJECT_TEMPLATE_DEFAULT,
    INLINE_IMAGE_CONTENT_ID,
    SIGNATURE_CLOSING,
    SIGNATURE_INTRO,
    build_email_template_environment,
)
from app.email.gmail import GmailProvider


def test_gmail_send_builds_expected_related_mime_message() -> None:
    service = _FakeGmailService()
    provider = GmailProvider(config=_build_config(), service_factory=lambda: service)
    message = _build_message(image_mode="local")

    provider.send(message)

    parsed_message = _parse_sent_message(service)

    assert parsed_message["From"] == "Example Sender <sender@example.com>"
    assert parsed_message["To"] == "Test Person <test.person@example.com>"
    assert parsed_message["Subject"] == message.subject
    assert parsed_message.get_content_subtype() == "related"

    related_parts = parsed_message.get_payload()
    assert len(related_parts) == 2

    alternative_part = related_parts[0]
    assert alternative_part.get_content_subtype() == "alternative"

    alternative_payload = alternative_part.get_payload()
    assert [part.get_content_subtype() for part in alternative_payload] == [
        "plain",
        "html",
    ]
    assert alternative_payload[0].get_content() == message.text_body
    assert alternative_payload[1].get_content() == message.html_body

    image_part = related_parts[1]
    assert image_part.get_content_maintype() == "image"
    assert image_part.get_content_subtype() == "jpeg"
    assert image_part["Content-ID"] == f"<{INLINE_IMAGE_CONTENT_ID}>"
    assert "inline" in image_part["Content-Disposition"]


def test_gmail_send_local_mode_attaches_inline_image() -> None:
    service = _FakeGmailService()
    provider = GmailProvider(config=_build_config(), service_factory=lambda: service)

    provider.send(_build_message(image_mode="local"))

    parsed_message = _parse_sent_message(service)

    assert len(parsed_message.get_payload()) == 2
    assert parsed_message.get_payload()[1]["Content-ID"] == (
        f"<{INLINE_IMAGE_CONTENT_ID}>"
    )


def test_gmail_send_url_mode_uses_remote_image_without_attachment() -> None:
    service = _FakeGmailService()
    provider = GmailProvider(config=_build_config(), service_factory=lambda: service)

    provider.send(_build_message(image_mode="url"))

    parsed_message = _parse_sent_message(service)
    html_part = parsed_message.get_payload()[0].get_payload()[1]

    assert len(parsed_message.get_payload()) == 1
    assert "https://assets.example.com/birthday-banner.jpg" in html_part.get_content()
    assert f"cid:{INLINE_IMAGE_CONTENT_ID}" not in html_part.get_content()


def test_gmail_send_none_mode_has_no_image_reference_or_attachment() -> None:
    service = _FakeGmailService()
    provider = GmailProvider(config=_build_config(), service_factory=lambda: service)

    provider.send(_build_message(image_mode="none"))

    parsed_message = _parse_sent_message(service)
    text_part, html_part = parsed_message.get_payload()[0].get_payload()

    assert len(parsed_message.get_payload()) == 1
    assert f"cid:{INLINE_IMAGE_CONTENT_ID}" not in text_part.get_content()
    assert f"cid:{INLINE_IMAGE_CONTENT_ID}" not in html_part.get_content()
    assert (
        "https://assets.example.com/birthday-banner.jpg" not in text_part.get_content()
    )
    assert (
        "https://assets.example.com/birthday-banner.jpg" not in html_part.get_content()
    )


def test_gmail_send_includes_cc_header_when_configured() -> None:
    service = _FakeGmailService()
    provider = GmailProvider(config=_build_config(), service_factory=lambda: service)

    provider.send(
        _build_message(
            image_mode="none",
            cc_emails=("manager1@quirongroup.com", "manager2@quirongroup.com"),
        )
    )

    parsed_message = _parse_sent_message(service)

    assert parsed_message["Cc"] == (
        "manager1@quirongroup.com, manager2@quirongroup.com"
    )


def test_gmail_send_fails_fast_on_transient_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleep_calls: list[float] = []
    monkeypatch.setattr("app.retry.time.sleep", sleep_calls.append)
    service = _FakeGmailService(
        execute_side_effects=[_http_error(503, "Service Unavailable")]
    )
    provider = GmailProvider(config=_build_config(), service_factory=lambda: service)

    with pytest.raises(
        AmbiguousSendError,
        match="Gmail send outcome is unknown: the request may have succeeded before this failure occurred",
    ) as exc_info:
        provider.send(_build_message(image_mode="local"))

    assert service.execute_calls == 1
    assert sleep_calls == []
    assert isinstance(exc_info.value, EmailSendError)


def test_gmail_send_does_not_retry_non_transient_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleep_calls: list[float] = []
    monkeypatch.setattr("app.retry.time.sleep", sleep_calls.append)
    service = _FakeGmailService(execute_side_effects=[_http_error(400, "Bad Request")])
    provider = GmailProvider(config=_build_config(), service_factory=lambda: service)

    with pytest.raises(
        AmbiguousSendError,
        match="Gmail send outcome is unknown: the request may have succeeded before this failure occurred",
    ) as exc_info:
        provider.send(_build_message(image_mode="local"))

    assert service.execute_calls == 1
    assert sleep_calls == []
    assert isinstance(exc_info.value, EmailSendError)


def test_gmail_send_fails_fast_on_transport_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleep_calls: list[float] = []
    monkeypatch.setattr("app.retry.time.sleep", sleep_calls.append)
    service = _FakeGmailService(
        execute_side_effects=[HttpLib2Error("synthetic transport failure")]
    )
    provider = GmailProvider(config=_build_config(), service_factory=lambda: service)

    with pytest.raises(
        AmbiguousSendError,
        match="Gmail send outcome is unknown: the request may have succeeded before this failure occurred",
    ) as exc_info:
        provider.send(_build_message(image_mode="local"))

    assert service.execute_calls == 1
    assert sleep_calls == []
    assert isinstance(exc_info.value, EmailSendError)


@pytest.mark.parametrize(
    "transport_error",
    [
        TimeoutError("synthetic timeout"),
        ConnectionError("synthetic connection failure"),
    ],
)
def test_gmail_send_treats_builtin_transport_errors_as_ambiguous(
    monkeypatch: pytest.MonkeyPatch,
    transport_error: Exception,
) -> None:
    sleep_calls: list[float] = []
    monkeypatch.setattr("app.retry.time.sleep", sleep_calls.append)
    service = _FakeGmailService(execute_side_effects=[transport_error])
    provider = GmailProvider(config=_build_config(), service_factory=lambda: service)

    with pytest.raises(
        AmbiguousSendError,
        match="Gmail send outcome is unknown: the request may have succeeded before this failure occurred",
    ) as exc_info:
        provider.send(_build_message(image_mode="local"))

    assert service.execute_calls == 1
    assert sleep_calls == []
    assert isinstance(exc_info.value, EmailSendError)


def test_gmail_send_wraps_build_failure_as_plain_email_send_error() -> None:
    service = _FakeGmailService()
    provider = GmailProvider(config=_build_config(), service_factory=lambda: service)

    with pytest.raises(
        EmailSendError, match="Failed to build or send Gmail message"
    ) as exc_info:
        provider.send(
            _build_message(image_mode="local", inline_image_mime_type="application/pdf")
        )

    assert not isinstance(exc_info.value, AmbiguousSendError)
    assert service.execute_calls == 0


class _FakeGmailService:
    def __init__(self, execute_side_effects: list[Exception] | None = None) -> None:
        self.execute_side_effects = list(execute_side_effects or [])
        self.sent_bodies: list[dict[str, str]] = []
        self.execute_calls = 0

    def users(self) -> _FakeGmailService:
        return self

    def messages(self) -> _FakeGmailService:
        return self

    def send(self, *, userId: str, body: dict[str, str]) -> _FakeGmailRequest:
        assert userId == "me"
        self.sent_bodies.append(body)
        return _FakeGmailRequest(self)


class _FakeGmailRequest:
    def __init__(self, service: _FakeGmailService) -> None:
        self._service = service

    def execute(self) -> dict[str, str]:
        self._service.execute_calls += 1
        if self._service.execute_side_effects:
            raise self._service.execute_side_effects.pop(0)
        return {"id": "synthetic-message-id"}


def _build_config() -> Config:
    return Config(
        app_timezone="America/Chicago",
        dry_run=True,
        test_date=None,
        spreadsheet_mode="google_sheet",
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
        google_auth_mode="service_account",
        google_credentials_file=Path("synthetic-credentials.json"),
        google_impersonate_subject="sender@example.com",
        google_oauth_client_secrets_file=None,
        google_oauth_token_file=Path("synthetic-oauth-token.json"),
        google_oauth_token_persist=True,
        birthday_image_mode="local",
        birthday_image_path=Path("app/assets/birthday_banner.jpg"),
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


def _build_message(
    *,
    image_mode: str,
    inline_image_mime_type: str = "image/jpeg",
    cc_emails: tuple[str, ...] = (),
) -> EmailMessage:
    template_env = build_email_template_environment()
    template_context = {
        "name": "Test Person",
        "salutation": DEFAULT_SALUTATION,
        "image_mode": image_mode,
        "image_alt": DEFAULT_BIRTHDAY_IMAGE_ALT,
        "image_width": 600,
        "image_url": "https://assets.example.com/birthday-banner.jpg",
        "inline_image_content_id": INLINE_IMAGE_CONTENT_ID,
        "signature_closing": SIGNATURE_CLOSING,
        "signature_intro": SIGNATURE_INTRO,
        "from_name": "Example Sender",
    }
    html_body = template_env.get_template("birthday_email.html").render(
        template_context
    )
    text_body = template_env.get_template("birthday_email.txt").render(template_context)
    inline_image = None
    if image_mode == "local":
        inline_image = InlineImage(
            content_id=INLINE_IMAGE_CONTENT_ID,
            data=b"synthetic-jpeg-image-bytes",
            mime_type=inline_image_mime_type,
        )

    return EmailMessage(
        to_email="test.person@example.com",
        to_name="Test Person",
        from_name="Example Sender",
        from_address="sender@example.com",
        subject=_render_subject("Test Person"),
        html_body=html_body,
        text_body=text_body,
        inline_image=inline_image,
        cc_emails=cc_emails,
    )


def _parse_sent_message(service: _FakeGmailService):
    assert service.sent_bodies
    raw_bytes = base64.urlsafe_b64decode(service.sent_bodies[0]["raw"].encode("ascii"))
    return BytesParser(policy=policy.default).parsebytes(raw_bytes)


def _http_error(status: int, reason: str) -> HttpError:
    return HttpError(
        resp=SimpleNamespace(status=status, reason=reason),
        content=b'{"error":"synthetic"}',
    )


def _render_subject(name: str) -> str:
    return EMAIL_SUBJECT_TEMPLATE_DEFAULT.replace("{{ display_name }}", name)
