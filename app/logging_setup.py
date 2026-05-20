"""
Logging configuration — JSON in production, pretty in development.
"""
from __future__ import annotations

import sys

from loguru import logger

from app.config import settings


def configure_logging() -> None:
    logger.remove()

    if settings.environment == "production":
        # Structured JSON for log aggregators (Datadog, Loki, CloudWatch)
        logger.add(
            sys.stdout,
            level="INFO",
            serialize=True,          # loguru built-in JSON serialiser
            backtrace=False,
            diagnose=False,
        )
    else:
        # Human-readable for local dev
        logger.add(
            sys.stderr,
            level="DEBUG",
            format=(
                "<green>{time:HH:mm:ss}</green> | "
                "<level>{level: <8}</level> | "
                "<cyan>{name}</cyan>:<cyan>{line}</cyan> — <level>{message}</level>"
            ),
            colorize=True,
            backtrace=True,
            diagnose=True,
        )
