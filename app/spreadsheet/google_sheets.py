from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from googleapiclient.discovery import build  # type: ignore[import-untyped]
from googleapiclient.errors import HttpError  # type: ignore[import-untyped]
from httplib2 import HttpLib2Error  # type: ignore[import-untyped]

from app.config import Config
from app.retry import retry_with_backoff
from app.spreadsheet.base import (
    SpreadsheetError,
    SpreadsheetProvider,
    build_row_dict,
    row_has_client_identity,
    resolve_headers,
)

SheetsServiceFactory = Callable[[], Any]
_TRANSIENT_HTTP_STATUSES = frozenset({429, 500, 502, 503, 504})
_UNQUOTED_SHEET_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")


class _RetryableSpreadsheetError(Exception):
    """Wrap transient API failures so the retry decorator can target them."""


class GoogleSheetsProvider(SpreadsheetProvider):
    def __init__(self, config: Config, service_factory: SheetsServiceFactory) -> None:
        self._config = config
        self._service_factory = service_factory

    @classmethod
    def from_credentials(cls, config: Config, credentials: Any) -> GoogleSheetsProvider:
        def service_factory() -> Any:
            return build("sheets", "v4", credentials=credentials)

        return cls(config=config, service_factory=service_factory)

    def load_rows(self) -> list[dict[str, object]]:
        try:
            values = self._fetch_values_with_retry()
        except (
            _RetryableSpreadsheetError,
            TimeoutError,
            ConnectionError,
        ) as exc:
            raise SpreadsheetError(
                "Sheets API request failed after retries were exhausted"
            ) from exc
        if not values:
            return []

        header_row = [str(cell) for cell in values[0]]
        resolved_headers = resolve_headers(header_row, self._config)
        rows: list[dict[str, object]] = []
        for raw_row in values[1:]:
            materialized_row = list(raw_row)
            if not row_has_client_identity(
                materialized_row, resolved_headers, self._config
            ):
                break
            rows.append(build_row_dict(materialized_row, resolved_headers))
        return rows

    def _fetch_values_with_retry(self) -> list[list[object]]:
        @retry_with_backoff(
            max_attempts=self._config.retry_max_attempts,
            base_delay_seconds=self._config.retry_base_delay_seconds,
            retryable_exceptions=(
                _RetryableSpreadsheetError,
                TimeoutError,
                ConnectionError,
            ),
        )
        def fetch_values() -> list[list[object]]:
            return self._fetch_values()

        return fetch_values()

    def _fetch_values(self) -> list[list[object]]:
        service = self._service_factory()
        try:
            response = (
                service.spreadsheets()
                .values()
                .get(
                    spreadsheetId=self._config.google_sheet_id,
                    range=_build_sheet_range(self._config),
                    valueRenderOption="UNFORMATTED_VALUE",
                    dateTimeRenderOption="SERIAL_NUMBER",
                )
                .execute()
            )
        except HttpError as exc:
            if exc.resp.status in _TRANSIENT_HTTP_STATUSES:
                raise _RetryableSpreadsheetError(
                    "Transient Sheets API failure"
                ) from exc
            raise SpreadsheetError("Sheets API request failed") from exc
        except HttpLib2Error as exc:
            raise _RetryableSpreadsheetError(
                "Transient Sheets transport failure"
            ) from exc
        values = response.get("values", [])
        if not isinstance(values, list):
            raise SpreadsheetError("Sheets API returned an invalid values payload")
        return values


def _build_sheet_range(config: Config) -> str:
    tab_name = config.google_sheet_tab.strip()
    if not tab_name:
        return "A:ZZ"
    if _UNQUOTED_SHEET_NAME_RE.fullmatch(tab_name):
        return f"{tab_name}!A:ZZ"
    escaped_tab_name = tab_name.replace("'", "''")
    return f"'{escaped_tab_name}'!A:ZZ"
