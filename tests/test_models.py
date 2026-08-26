from __future__ import annotations

from datetime import date

from app.models import Client, PolicyRenewal, build_display_name
from app.reminder_config import ReminderStage


def test_build_display_name_normalizes_whitespace() -> None:
    assert build_display_name("  Ana  ", "  Perez  ") == "Ana Perez"


def test_client_display_name_uses_last_name_when_present() -> None:
    client = Client(name="Ana", last_name="Perez", email="ana@example.com")

    assert client.display_name == "Ana Perez"


def test_policy_renewal_keeps_client_policy_and_due_date() -> None:
    client = Client(name="Ana", email="ana@example.com")
    renewal = PolicyRenewal(
        client=client,
        policy_number="POL-123",
        renewal_date=date(2026, 9, 25),
        row_index=2,
    )

    assert renewal.client.email == "ana@example.com"
    assert renewal.policy_number == "POL-123"
    assert renewal.renewal_date == date(2026, 9, 25)


def test_reminder_stage_days_before_due_is_derived_from_offset() -> None:
    assert ReminderStage("30_days", "30 days before", -30).days_before_due == 30
    assert ReminderStage("due_today", "Due today", 0).days_before_due == 0
    assert ReminderStage("grace", "Grace", 3).days_before_due is None
