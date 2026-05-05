"""Structured logging for D.A.R.E.

Each run gets its own log file under ``<run_dir>/logs/run.log``. The console
output stays clean and human-readable; the file log includes timestamps and
module names for post-mortem analysis.

The module is idempotent: calling :func:`get_logger` repeatedly with the same
parameters never duplicates handlers.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

_CONSOLE_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
_FILE_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DATE_FORMAT = "%H:%M:%S"


def get_logger(
    name: str = "dare",
    run_dir: Optional[Path] = None,
    level: int = logging.INFO,
) -> logging.Logger:
    """Get (or create) a logger.

    Args:
        name: logger name. Use a dotted hierarchy for module-specific loggers
            (``"dare.recorder"``, ``"dare.normalizer"``, …).
        run_dir: if provided, a file handler is attached writing to
            ``<run_dir>/logs/run.log``. The directory is created if missing.
        level: console log level. The file handler always logs at DEBUG.

    Returns:
        A configured :class:`logging.Logger`. Safe to call repeatedly.
    """
    logger = logging.getLogger(name)
    logger.setLevel(min(level, logging.DEBUG))
    logger.propagate = False

    if not any(
        isinstance(h, logging.StreamHandler) and getattr(h, "_dare_console", False)
        for h in logger.handlers
    ):
        ch = logging.StreamHandler(sys.stderr)
        ch.setLevel(level)
        ch.setFormatter(logging.Formatter(_CONSOLE_FORMAT, datefmt=_DATE_FORMAT))
        ch._dare_console = True  # type: ignore[attr-defined]
        logger.addHandler(ch)

    if run_dir is not None:
        log_path = Path(run_dir) / "logs" / "run.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        already = any(
            isinstance(h, logging.FileHandler)
            and Path(h.baseFilename).resolve() == log_path.resolve()
            for h in logger.handlers
        )
        if not already:
            fh = logging.FileHandler(log_path, encoding="utf-8")
            fh.setLevel(logging.DEBUG)
            fh.setFormatter(logging.Formatter(_FILE_FORMAT))
            logger.addHandler(fh)

    return logger
