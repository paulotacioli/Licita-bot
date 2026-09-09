"""Logging para console (rich) e arquivo rotativo em data/logs/."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from rich.logging import RichHandler

from licitabot.config import get_settings


def setup_logging(level: int = logging.INFO) -> None:
    s = get_settings()
    s.ensure_dirs()
    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(level)
    root.addHandler(RichHandler(rich_tracebacks=False, show_path=False, markup=False))
    fh = RotatingFileHandler(s.logs_path / "licitabot.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(fh)
    for noisy in ("httpx", "httpcore", "apscheduler", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
