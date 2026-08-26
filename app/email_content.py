from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from jinja2 import DictLoader, Environment, select_autoescape

# ¡Feliz cumpleaños, {{ name ~ ' ' ~ last_name }}! 🎉
# "Feliz cumpleaños, {{ display_name }}! 🎉" = Full name
EMAIL_SUBJECT_TEMPLATE_DEFAULT: Final = "Feliz cumpleaños, {{ display_name }}! 🎉"
BP_REMINDER_TO_ADDRESS_DEFAULT: Final = "jorge.arellano@quirongroup.com"
BP_REMINDER_TO_NAME_DEFAULT: Final = "Jorge Arellano"
BP_REMINDER_TO_ADDRESS_ENV_NAME: Final = "BP_REMINDER_TO_ADDRESS_DEFAULT"
BP_REMINDER_CC_ENV_NAME: Final = "BP_REMINDER_CC"
BP_REMINDER_SUBJECT_TEMPLATE: Final = (
    "Recordatorio BP: llamada de cumpleaños para {{ display_name }}"
)
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

####################### THE ONE CURRENLTY USING ####################
######################## TO Modify From Name and Subject template go to .env ###############
EMAIL_HTML_TEMPLATE: Final = """<!DOCTYPE html>
<html lang="es">
  <body style="font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif; line-height: 1.5; color: #1e293b; max-width: 540px; margin: 0 auto; padding: 24px 16px; background-color: #ffffff;">
    <p style="font-size: 16px; margin: 0 0 16px 0;">{{ salutation }} <strong>{{ name }}</strong>,</p>

    {% if image_mode == "local" %}
    <div style="text-align: center; margin: 20px 0;">
      <img src="cid:{{ inline_image_content_id }}" alt="{{ image_alt }}" width="{{ image_width }}" style="max-width: 100%; height: auto; border-radius: 12px; display: block; margin: 0 auto;">
    </div>
    {% elif image_mode == "url" %}
    <div style="text-align: center; margin: 20px 0;">
      <img src="{{ image_url }}" alt="{{ image_alt }}" width="{{ image_width }}" style="max-width: 100%; height: auto; border-radius: 12px; display: block; margin: 0 auto;">
    </div>
    {% endif %}

    <p style="font-size: 14px; color: #64748b; text-align: center; margin: 20px 0 0 0;">
      {{ signature_intro }}<br>
      <strong style="color: #0f172a;">{{ from_name }}</strong>
    </p>
  </body>
</html>
"""

EMAIL_TEXT_TEMPLATE: Final = """Feliz cumpleaños, {{ name }}! 🎉

Hi {{ name }},

{% if image_mode == "url" %}{{ image_url }}

{% endif %}Wishing you a wonderful birthday filled with joy, laughter, and a year ahead full of great moments.

{{ signature_closing }},
{{ from_name }}
"""

BP_REMINDER_HTML_TEMPLATE: Final = """<!DOCTYPE html>
<html lang="es">
  <body style="font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif; line-height: 1.5; color: #1e293b; max-width: 540px; margin: 0 auto; padding: 24px 16px; background-color: #ffffff;">
    <p style="font-size: 16px; margin: 0 0 16px 0;">Se detectó un cliente BP en el flujo de cumpleaños.</p>
    <ul style="padding-left: 20px; margin: 0 0 16px 0;">
      <li><strong>Nombre completo:</strong> {{ display_name }}</li>
      <li><strong>Fecha de cumpleaños:</strong> {{ birthday_date }}</li>
      <li><strong>Móvil:</strong> {{ mobile_phone }}</li>
      <li><strong>Estatus:</strong> {{ bp_status }}</li>
    </ul>
    <p style="font-size: 14px; color: #64748b; margin: 0;">{{ bp_follow_up_text }}</p>
  </body>
</html>
"""

BP_REMINDER_TEXT_TEMPLATE: Final = """Cliente BP detectado en el flujo de cumpleaños.

Nombre completo: {{ display_name }}
Fecha de cumpleaños: {{ birthday_date }}
Móvil: {{ mobile_phone }}
Estatus: {{ bp_status }}

{{ bp_follow_up_text }}
"""

DEFAULT_SALUTATION: Final = "Estimado/a"
FEMALE_SALUTATION: Final = "Estimada"
MALE_SALUTATION: Final = "Estimado"
SIGNATURE_INTRO: Final = "Un cordial saludo,"
SIGNATURE_CLOSING: Final = "Best wishes"

DEFAULT_BIRTHDAY_IMAGE_MODE: Final = "local"
DEFAULT_BIRTHDAY_IMAGE_PATH: Final = "app/assets/birthday_banner.jpg"
DEFAULT_BIRTHDAY_IMAGE_URL: Final = ""
DEFAULT_BIRTHDAY_IMAGE_ALT: Final = "Happy Birthday"
DEFAULT_BIRTHDAY_IMAGE_WIDTH: Final = 600
INLINE_IMAGE_CONTENT_ID: Final = "birthday_banner"
BP_STATUS_LABEL: Final = "BP override activo"
BP_FOLLOW_UP_TEXT: Final = "No se envió correo al cliente. Favor de realizar llamada de felicitación."


@dataclass(frozen=True)
class BPReminderRecipients:
    to_email: str = BP_REMINDER_TO_ADDRESS_DEFAULT
    to_name: str = BP_REMINDER_TO_NAME_DEFAULT
    cc_emails: tuple[str, ...] = ()


def build_bp_reminder_recipients(
    configured_to_email: str | None = None,
    configured_cc: str | None = None,
) -> BPReminderRecipients:
    return BPReminderRecipients(
        to_email=_parse_bp_reminder_to_email(configured_to_email),
        cc_emails=_parse_bp_reminder_cc_list(configured_cc or ""),
    )


def _parse_bp_reminder_to_email(value: str | None) -> str:
    if value is None or not value.strip():
        return BP_REMINDER_TO_ADDRESS_DEFAULT
    candidate = value.strip().lower()
    if _EMAIL_RE.fullmatch(candidate) is None:
        raise ValueError(
            f"{BP_REMINDER_TO_ADDRESS_ENV_NAME} must be a valid email address"
        )
    return candidate


def _parse_bp_reminder_cc_list(value: str) -> tuple[str, ...]:
    if not value.strip():
        return ()

    parsed: list[str] = []
    seen: set[str] = set()
    for raw_part in value.split(","):
        candidate = raw_part.strip().lower()
        if not candidate:
            continue
        if _EMAIL_RE.fullmatch(candidate) is None:
            raise ValueError(f"{BP_REMINDER_CC_ENV_NAME} must be a valid email address")
        if candidate in seen:
            continue
        seen.add(candidate)
        parsed.append(candidate)
    return tuple(parsed)


def build_email_template_environment() -> Environment:
    return Environment(
        loader=DictLoader(
            {
                "birthday_email.html": EMAIL_HTML_TEMPLATE,
                "birthday_email.txt": EMAIL_TEXT_TEMPLATE,
                "bp_call_reminder.html": BP_REMINDER_HTML_TEMPLATE,
                "bp_call_reminder.txt": BP_REMINDER_TEXT_TEMPLATE,
            }
        ),
        autoescape=select_autoescape(["html", "xml"]),
    )
