from __future__ import annotations

import base64
from collections.abc import Callable
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from typing import Any

from googleapiclient.discovery import build  # type: ignore[import-untyped]
from googleapiclient.errors import HttpError  # type: ignore[import-untyped]
from httplib2 import HttpLib2Error  # type: ignore[import-untyped]

from app.config import Config
from app.email.base import (
    AmbiguousSendError,
    EmailMessage,
    EmailProvider,
    EmailSendError,
)

GmailServiceFactory = Callable[[], Any]


class GmailProvider(EmailProvider):
    def __init__(self, config: Config, service_factory: GmailServiceFactory) -> None:
        self._config = config
        self._service_factory = service_factory

    @classmethod
    def from_credentials(cls, config: Config, credentials: Any) -> GmailProvider:
        def service_factory() -> Any:
            return build("gmail", "v1", credentials=credentials)

        return cls(config=config, service_factory=service_factory)

    def send(self, message: EmailMessage) -> None:
        try:
            self._send_message(message)
        except AmbiguousSendError:
            raise
        except Exception as exc:
            raise EmailSendError("Failed to build or send Gmail message") from exc

    def _send_message(self, message: EmailMessage) -> None:
        service = self._service_factory()
        raw_message = self._build_raw_message(message)
        request = (
            service.users().messages().send(userId="me", body={"raw": raw_message})
        )
        try:
            request.execute()
        except (HttpError, HttpLib2Error, TimeoutError, ConnectionError) as exc:
            raise AmbiguousSendError(
                "Gmail send outcome is unknown: the request may have succeeded before this failure occurred"
            ) from exc

    def _build_raw_message(self, message: EmailMessage) -> str:
        mime_message = MIMEMultipart("related")
        mime_message["From"] = _format_mailbox(message.from_name, message.from_address)
        mime_message["To"] = _format_mailbox(message.to_name, message.to_email)
        if message.cc_emails:
            mime_message["Cc"] = ", ".join(message.cc_emails)
        mime_message["Subject"] = message.subject

        alternative_part = MIMEMultipart("alternative")
        alternative_part.attach(MIMEText(message.text_body, "plain", "utf-8"))
        alternative_part.attach(MIMEText(message.html_body, "html", "utf-8"))
        mime_message.attach(alternative_part)

        if message.inline_image is not None:
            image_subtype = _image_subtype_from_mime_type(
                message.inline_image.mime_type
            )
            image_part = MIMEImage(
                message.inline_image.data,
                _subtype=image_subtype,
                name="birthday_banner.jpg",
            )

            image_part.add_header(
                "Content-ID",
                f"<{message.inline_image.content_id}>",
            )

            image_part.add_header(
                "Content-Disposition",
                "inline",
                filename="birthday_banner.jpg",
            )

            mime_message.attach(image_part)

        encoded_message = base64.urlsafe_b64encode(mime_message.as_bytes())
        return encoded_message.decode("ascii")


def _format_mailbox(name: str, address: str) -> str:
    if not name.strip():
        return address
    return formataddr((name, address))


def _image_subtype_from_mime_type(mime_type: str) -> str:
    type_name, separator, subtype = mime_type.partition("/")
    if separator != "/" or type_name != "image" or not subtype:
        raise ValueError(f"Unsupported inline image MIME type: {mime_type!r}")
    return subtype
