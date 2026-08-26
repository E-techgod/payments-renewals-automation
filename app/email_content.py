from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_TEMPLATES_DIR = _PROJECT_ROOT / "templates"


def build_email_template_environment() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
    )
