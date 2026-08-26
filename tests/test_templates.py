from __future__ import annotations

from app.email_content import build_email_template_environment


def test_renewal_templates_render_expected_fields() -> None:
    template_env = build_email_template_environment()
    context = {
        "client_name": "Ana Perez",
        "policy_number": "POL-123",
        "renewal_date": "2026-09-25",
        "reminder_label": "30 days before due date",
        "days_remaining": 30,
        "from_name": "Example Sender",
    }

    html_output = template_env.get_template("renewal_reminder.html").render(context)
    text_output = template_env.get_template("renewal_reminder.txt").render(context)

    assert 'lang="es"' in html_output
    assert "Ana Perez" in html_output
    assert "POL-123" in html_output
    assert "2026-09-25" in html_output
    assert "30 days before due date" in html_output
    assert "Días restantes" in html_output
    assert "Ana Perez" in text_output
    assert "POL-123" in text_output
    assert "Saludos" in text_output
