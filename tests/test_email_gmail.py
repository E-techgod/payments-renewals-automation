from __future__ import annotations

import base64
from email import policy
from email.parser import BytesParser
from pathlib import Path
from types import SimpleNamespace

import pytest
from googleapiclient.errors import HttpError  # type: ignore[import-untyped]
from httplib2 import HttpLib2Error  # type: ignore[import-untyped]

from app.email.base import AmbiguousSendError, EmailMessage, EmailProvider, EmailSendError
from app.email.gmail import GmailProvider
from tests.support import build_config


def test_gmail_send_builds_expected_related_mime_message() -> None:
    service = _FakeGmailService()
    provider = GmailProvider(config=build_config(), service_factory=lambda: service)
    message = _build_message()

    provider.send(message)

    parsed_message = _parse_sent_message(service)

    assert parsed_message["From"] == "Example Sender <sender@example.com>"
    assert parsed_message["To"] == "Ana Perez <ana@example.com>"
    assert parsed_message["Subject"] == message.subject
    assert parsed_message.get_content_subtype() == "related"

    related_parts = parsed_message.get_payload()
    assert len(related_parts) == 1

    alternative_part = related_parts[0]
    assert alternative_part.get_content_subtype() == "alternative"
    assert [part.get_content_subtype() for part in alternative_part.get_payload()] == [
        "plain",
        "html",
    ]


def test_gmail_send_includes_cc_header_when_configured() -> None:
    service = _FakeGmailService()
    provider = GmailProvider(config=build_config(), service_factory=lambda: service)
    message = _build_message(cc_emails=("ops@example.com", "sales@example.com"))

    provider.send(message)

    parsed_message = _parse_sent_message(service)
    assert parsed_message["Cc"] == "ops@example.com, sales@example.com"


@pytest.mark.parametrize(
    "transport_error",
    [
        pytest.param((503, "Service Unavailable"), id="http-error"),
        pytest.param(HttpLib2Error("synthetic transport failure"), id="httplib2"),
        pytest.param(TimeoutError("synthetic timeout"), id="timeout"),
        pytest.param(ConnectionError("synthetic connection failure"), id="connection"),
    ],
)
def test_gmail_send_treats_transport_errors_as_ambiguous(
    transport_error: Exception | tuple[int, str],
) -> None:
    if isinstance(transport_error, tuple):
        transport_error = _http_error(*transport_error)
    service = _FakeGmailService(execute_side_effects=[transport_error])
    provider = GmailProvider(config=build_config(), service_factory=lambda: service)

    with pytest.raises(AmbiguousSendError):
        provider.send(_build_message())


def test_gmail_send_wraps_build_failure_as_plain_email_send_error() -> None:
    class BrokenProvider(EmailProvider):
        def send(self, message: EmailMessage) -> None:
            raise NotImplementedError

    service = _FakeGmailService()
    provider = GmailProvider(config=build_config(), service_factory=lambda: service)

    with pytest.raises(EmailSendError):
        provider.send(
            EmailMessage(
                to_email="ana@example.com",
                to_name="Ana Perez",
                from_name="Example Sender",
                from_address="sender@example.com",
                subject="x",
                html_body="<p>x</p>",
                text_body="x",
                inline_image=SimpleNamespace(
                    content_id="x",
                    data=b"bad",
                    mime_type="application/pdf",
                ),
            )
        )

    assert BrokenProvider


class _FakeGmailService:
    def __init__(self, execute_side_effects: list[Exception] | None = None) -> None:
        self.execute_side_effects = list(execute_side_effects or [])
        self.sent_bodies: list[dict[str, str]] = []

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
        if self._service.execute_side_effects:
            raise self._service.execute_side_effects.pop(0)
        return {"id": "synthetic-message-id"}


def _build_message(*, cc_emails: tuple[str, ...] = ()) -> EmailMessage:
    return EmailMessage(
        to_email="ana@example.com",
        to_name="Ana Perez",
        from_name="Example Sender",
        from_address="sender@example.com",
        subject="Renewal reminder: policy POL-123 due 2026-09-25",
        html_body="<p>Renewal reminder</p>",
        text_body="Renewal reminder",
        inline_image=None,
        cc_emails=cc_emails,
    )


def _parse_sent_message(service: _FakeGmailService):
    raw_bytes = base64.urlsafe_b64decode(service.sent_bodies[0]["raw"].encode("ascii"))
    return BytesParser(policy=policy.default).parsebytes(raw_bytes)


def _http_error(status: int, reason: str) -> HttpError:
    return HttpError(
        resp=SimpleNamespace(status=status, reason=reason),
        content=b'{"error":"synthetic"}',
    )
