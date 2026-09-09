"""Fixtures: banco SQLite temporário e settings isolados por sessão de teste."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session", autouse=True)
def _isolated_env(tmp_path_factory):
    data = tmp_path_factory.mktemp("data")
    cofre = tmp_path_factory.mktemp("cofre")
    os.environ["DATA_DIR"] = str(data)
    os.environ["COFRE_DIR"] = str(cofre)
    os.environ["APPROVAL_SECRET"] = "x" * 48
    os.environ["DRY_RUN"] = "true"
    os.environ["ANTHROPIC_API_KEY"] = ""
    from licitabot.config import get_settings

    get_settings.cache_clear()
    from licitabot.db.session import init_db

    init_db()
    yield
