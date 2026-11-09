"""Logging.

Every scan and every deletion is written to a plain-text log the user can
open and read, plus a machine-readable JSON report per run. Nothing leaves
the machine - there is no network code anywhere in this project.

Each app calls `configure("Name")` once at import, which decides the folder
under %LOCALAPPDATA% its logs go to.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime
from typing import Optional

_app_name = "SimpleApp"
_logger: Optional[logging.Logger] = None
_log_path: Optional[str] = None


def configure(app_name: str) -> None:
    global _app_name
    _app_name = app_name


def data_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, _app_name)


def log_dir() -> str:
    path = os.path.join(data_dir(), "logs")
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        path = os.path.join(os.path.expanduser("~"), f".{_app_name.lower()}-logs")
        os.makedirs(path, exist_ok=True)
    return path


def setup(console: bool = False) -> logging.Logger:
    global _logger, _log_path
    if _logger is not None:
        return _logger

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    _log_path = os.path.join(log_dir(), f"{_app_name.lower()}-{stamp}.log")

    logger = logging.getLogger(_app_name)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-7s  %(message)s", "%Y-%m-%d %H:%M:%S"
    )
    try:
        fh = logging.FileHandler(_log_path, encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except OSError:
        pass

    if console:
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        sh.setLevel(logging.INFO)
        logger.addHandler(sh)

    _logger = logger
    logger.info("=" * 72)
    logger.info("%s starting", _app_name)
    logger.info("Log file: %s", _log_path)
    return logger


def get() -> logging.Logger:
    return _logger or setup()


def log_path() -> str:
    if _log_path is None:
        setup()
    return _log_path or ""


def write_report(report: dict) -> str:
    """Write a JSON report next to the text log. Returns its path."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(log_dir(), f"{_app_name.lower()}-report-{stamp}.json")
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, default=str)
        get().info("Report written: %s", path)
        return path
    except OSError as exc:
        get().error("Could not write report: %s", exc)
        return ""
