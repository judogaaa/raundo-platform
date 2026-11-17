"""Structured logging without dumping raw Telegram updates."""

import logging


def configure_logging(level_name: str) -> None:
    level = getattr(logging, level_name.upper(), logging.INFO)
    if level_name.upper() not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        level = logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        force=True,
    )
    # Keep third-party noise down in development.
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
