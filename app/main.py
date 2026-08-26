from __future__ import annotations

import logging

from app.birthday_rules import build_clock
from app.birthday_service import run_birthday_job
from app.config import ConfigError, load_config


class _MemoizedClock:
    def __init__(self, inner_clock) -> None:
        self._inner_clock = inner_clock
        self._cached_today = None

    def today(self):
        if self._cached_today is None:
            self._cached_today = self._inner_clock.today()
        return self._cached_today


def main() -> None:
    try:
        config = load_config()
    except ConfigError as exc:
        _configure_logging("ERROR")
        logging.getLogger(__name__).error("%s", exc)
        raise SystemExit(1) from exc

    _configure_logging(config.log_level)
    logger = logging.getLogger(__name__)
    effective_clock = _MemoizedClock(build_clock(config))
    effective_date = effective_clock.today()
    logger.info(
        "job starting: date=%s spreadsheet_mode=%s spreadsheet_source=%s dry_run=%s",
        effective_date.isoformat(),
        config.spreadsheet_mode,
        _spreadsheet_source(config),
        config.dry_run,
    )

    try:
        summary = run_birthday_job(config, clock=effective_clock)
    except Exception as exc:
        logger.critical("job failed", exc_info=True)
        raise SystemExit(1) from exc

    logger.info(
        "summary: inspected=%d matched=%d sent=%d duplicates=%d in_progress=%d invalid=%d failed=%d ambiguous=%d",
        summary.inspected,
        summary.matched,
        summary.sent,
        summary.duplicates,
        summary.in_progress,
        summary.invalid,
        summary.failed,
        summary.ambiguous,
    )
    if summary.failed > 0 or summary.ambiguous > 0 or summary.in_progress > 0:
        logger.error(
            "job completed with delivery issues: failed=%d ambiguous=%d in_progress=%d",
            summary.failed,
            summary.ambiguous,
            summary.in_progress,
        )
        raise SystemExit(1)
    logger.info("job completed")
    raise SystemExit(0)


def _configure_logging(level_name: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level_name.upper(), logging.INFO),
        format="%(levelname)s %(name)s %(message)s",
        force=True,
    )


def _spreadsheet_source(config) -> str:
    if config.spreadsheet_mode == "google_sheet":
        return config.google_sheet_id
    return config.google_drive_file_id
