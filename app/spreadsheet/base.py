from __future__ import annotations

from abc import ABC, abstractmethod
import unicodedata

from app.config import Config


class SpreadsheetError(ValueError):
    """Raised when spreadsheet data cannot be loaded or mapped safely."""


class SpreadsheetProvider(ABC):
    @abstractmethod
    def load_rows(self) -> list[dict[str, object]]:
        """Load spreadsheet rows keyed by the configured column names."""


def resolve_headers(raw_header_row: list[str], config: Config) -> dict[str, int]:
    _validate_distinct_configured_headers(config)

    normalized_headers: dict[str, int] = {}
    raw_headers_by_normalized: dict[str, list[str]] = {}
    for index, cell in enumerate(raw_header_row):
        normalized = _normalize_header(cell)
        if not normalized:
            continue
        raw_headers = raw_headers_by_normalized.setdefault(normalized, [])
        raw_headers.append(cell)
        if len(raw_headers) > 1:
            duplicates = ", ".join(repr(value) for value in raw_headers)
            raise SpreadsheetError(
                f"Duplicate spreadsheet headers after normalization: {duplicates}"
            )
        normalized_headers[normalized] = index

    resolved = {
        config.client_name_column: _require_header(
            config.client_name_column, normalized_headers
        ),
        config.email_column: _require_header(config.email_column, normalized_headers),
        config.policy_number_column: _require_header(
            config.policy_number_column, normalized_headers
        ),
        config.renewal_date_column: _require_header(
            config.renewal_date_column, normalized_headers
        ),
    }

    optional_last_name_index = normalized_headers.get(
        _normalize_header(config.last_name_column)
    )
    if optional_last_name_index is not None:
        resolved[config.last_name_column] = optional_last_name_index

    optional_service_line_index = normalized_headers.get(
        _normalize_header(config.service_line_column)
    )
    if optional_service_line_index is not None:
        resolved[config.service_line_column] = optional_service_line_index

    optional_mobile_phone_index = normalized_headers.get(
        _normalize_header(config.mobile_phone_column)
    )
    if optional_mobile_phone_index is not None:
        resolved[config.mobile_phone_column] = optional_mobile_phone_index

    return resolved


def build_row_dict(
    raw_row: list[object], resolved_headers: dict[str, int]
) -> dict[str, object]:
    row: dict[str, object] = {}
    for logical_name, column_index in resolved_headers.items():
        if column_index >= len(raw_row):
            continue
        value = raw_row[column_index]
        if value is None:
            continue
        row[logical_name] = value.strip() if isinstance(value, str) else value
    return row


def row_has_client_identity(
    raw_row: list[object], resolved_headers: dict[str, int], config: Config
) -> bool:
    return any(
        _cell_has_value(raw_row, resolved_headers.get(column_name))
        for column_name in (config.client_name_column, config.last_name_column)
    )


def _require_header(logical_name: str, normalized_headers: dict[str, int]) -> int:
    header_index = normalized_headers.get(_normalize_header(logical_name))
    if header_index is None:
        raise SpreadsheetError(f"Missing required spreadsheet header: {logical_name}")
    return header_index


def _validate_distinct_configured_headers(config: Config) -> None:
    configured_headers = [
        config.client_name_column,
        config.email_column,
        config.policy_number_column,
        config.renewal_date_column,
    ]
    if _normalize_header(config.last_name_column):
        configured_headers.append(config.last_name_column)
    if _normalize_header(config.service_line_column):
        configured_headers.append(config.service_line_column)
    if _normalize_header(config.mobile_phone_column):
        configured_headers.append(config.mobile_phone_column)

    seen_by_normalized: dict[str, str] = {}
    for configured_header in configured_headers:
        normalized = _normalize_header(configured_header)
        existing = seen_by_normalized.get(normalized)
        if existing is not None:
            raise SpreadsheetError(
                "Configured spreadsheet columns collide after normalization: "
                f"{existing!r} and {configured_header!r}"
            )
        seen_by_normalized[normalized] = configured_header


def _normalize_header(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.strip())
    without_diacritics = "".join(
        character for character in normalized if not unicodedata.combining(character)
    )
    return without_diacritics.casefold()


def _cell_has_value(raw_row: list[object], column_index: int | None) -> bool:
    if column_index is None or column_index >= len(raw_row):
        return False
    value = raw_row[column_index]
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True
