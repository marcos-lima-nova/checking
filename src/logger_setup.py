"""Per-folder logging setup.

Each analyzed subfolder of ``inputs/checking`` gets its own log file inside the
project ``logs/`` directory, named ``<folder>_<DDMMYYYY>.log``.

Logs are never written under ``output/`` (requirement 13).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional


def _sanitize_for_filename(name: str) -> str:
    """Make a folder name safe to use inside a log filename.

    Spaces and path separators are replaced; accents are preserved (the log file
    system on Linux handles UTF-8 fine), but characters that commonly break
    filenames are stripped.
    """
    name = name.strip()
    name = name.replace("/", "_").replace("\\", "_")
    name = re.sub(r"\s+", "_", name)
    name = re.sub(r"[\0<>:\"|?*]", "", name)
    return name or "folder"


def build_log_path(folder_name: str, logs_root: Path, when: Optional[datetime] = None) -> Path:
    """Return the log file path for ``folder_name`` (``logs/<folder>_<DDMMYYYY>.log``)."""
    when = when or datetime.now()
    date_str = when.strftime("%d%m%Y")
    safe = _sanitize_for_filename(folder_name)
    return Path(logs_root) / f"{safe}_{date_str}.log"


def get_folder_logger(
    folder_name: str,
    logs_root: Path,
    level: str = "INFO",
    to_console: bool = True,
    when: Optional[datetime] = None,
) -> logging.Logger:
    """Create (or return) an isolated logger for a single analyzed folder.

    The logger writes to ``logs/<folder>_<DDMMYYYY>.log`` and optionally to the
    console. Handlers are reset on each call so repeated runs in the same process
    do not duplicate output.
    """
    logs_root = Path(logs_root)
    logs_root.mkdir(parents=True, exist_ok=True)

    log_path = build_log_path(folder_name, logs_root, when=when)

    logger_name = f"ocr.{_sanitize_for_filename(folder_name)}"
    logger = logging.getLogger(logger_name)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.propagate = False

    # Reset handlers to avoid duplicates across multiple runs in one process.
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    if to_console:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(fmt)
        logger.addHandler(console_handler)

    # Expose the resolved path so callers can record it in the summary.
    logger.log_file_path = log_path  # type: ignore[attr-defined]
    return logger
