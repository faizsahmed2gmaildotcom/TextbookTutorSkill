"""Project-wide logging helpers."""

import logging
import sys


def setupLogging(level: int = logging.INFO) -> None:
    """Configure a single stderr handler for every logger.

    Args:
        level: Root logging level, defaults to ``logging.INFO``.

    Returns:
        None.
    """
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(handler)


def getLogger(name: str) -> logging.Logger:
    """Return the named logger.

    Args:
        name: Usually ``__name__``.

    Returns:
        The configured logger instance.
    """
    return logging.getLogger(name)
