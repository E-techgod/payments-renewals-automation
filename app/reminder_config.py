from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Final


@dataclass(frozen=True)
class ReminderStage:
    name: str
    label: str
    offset_days: int
    enabled: bool = True

    @property
    def days_before_due(self) -> int | None:
        if self.offset_days > 0:
            return None
        return abs(self.offset_days)


DEFAULT_CLIENT_NAME_COLUMN: Final = "Client"
DEFAULT_LAST_NAME_COLUMN: Final = "Last Name"
DEFAULT_EMAIL_COLUMN: Final = "Email"
DEFAULT_POLICY_NUMBER_COLUMN: Final = "Policy Number"
DEFAULT_RENEWAL_DATE_COLUMN: Final = "Renewal Date"
DEFAULT_SERVICE_LINE_COLUMN: Final = "Service Line"
DEFAULT_MOBILE_PHONE_COLUMN: Final = "Mobile Phone"
DEFAULT_GOOGLE_SHEET_TAB: Final = "Renewals"
DEFAULT_EMAIL_SUBJECT_TEMPLATE: Final = (
    "Renewal reminder: policy {{ policy_number }} due {{ renewal_date }}"
)
DEFAULT_HTML_TEMPLATE_NAME: Final = "renewal_reminder.html"
DEFAULT_TEXT_TEMPLATE_NAME: Final = "renewal_reminder.txt"
DEFAULT_STATE_TABLE_NAME: Final = "renewal_reminder_sends"
DEFAULT_FIRESTORE_COLLECTION_NAME: Final = "renewal_reminder_sends"

# The team can usually satisfy schedule-change requests by editing only this tuple.
DEFAULT_REMINDER_STAGES: Final[tuple[ReminderStage, ...]] = (
    ReminderStage(name="30_days", label="30 days before due date", offset_days=-30),
    ReminderStage(name="15_days", label="15 days before due date", offset_days=-15),
    ReminderStage(name="7_days", label="7 days before due date", offset_days=-7),
    ReminderStage(name="due_today", label="Due today", offset_days=0),
)


def is_policy_eligible(
    *,
    client_name: str,
    policy_number: str,
    renewal_date: date,
    service_lines: tuple[str, ...],
) -> bool:
    del client_name, policy_number, renewal_date, service_lines
    return True


def build_template_context(
    *,
    client_name: str,
    policy_number: str,
    renewal_date: date,
    reminder_stage: ReminderStage,
    today: date,
) -> dict[str, object]:
    days_remaining = (renewal_date - today).days
    return {
        "client_name": client_name,
        "policy_number": policy_number,
        "renewal_date": renewal_date.isoformat(),
        "reminder_stage": reminder_stage.name,
        "reminder_label": reminder_stage.label,
        "days_remaining": days_remaining,
        "reminder_due_date": (renewal_date + timedelta(days=reminder_stage.offset_days)).isoformat(),
    }
