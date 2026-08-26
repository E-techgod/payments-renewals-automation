from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class InlineImage:
    content_id: str
    data: bytes
    mime_type: str


@dataclass(frozen=True)
class EmailMessage:
    to_email: str
    to_name: str
    from_name: str
    from_address: str
    subject: str
    html_body: str
    text_body: str
    inline_image: InlineImage | None
    cc_emails: tuple[str, ...] = ()


class EmailSendError(RuntimeError):
    """Raised when an email provider cannot send a message."""


class AmbiguousSendError(EmailSendError):
    """Raised when a send may have succeeded before the failure surfaced."""


class EmailProvider(ABC):
    """Sends email messages and raises EmailSendError on failure."""

    @abstractmethod
    def send(self, message: EmailMessage) -> None:
        """Send the message or raise EmailSendError."""
