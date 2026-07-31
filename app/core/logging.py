"""Logging configuration for the VeriSure application."""

import logging


def configure_logging() -> None:
    """Configure application-wide logging defaults."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    )


logger = logging.getLogger("verisure")
