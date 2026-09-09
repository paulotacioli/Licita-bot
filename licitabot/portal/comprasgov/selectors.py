"""Resolução de seletores declarados em config/selectors/comprasgov.yaml (primary + fallbacks)."""

from __future__ import annotations

import logging
import re
from typing import Any

from playwright.sync_api import Locator, Page

from licitabot.config import load_selectors
from licitabot.portal.base import SeletorNaoEncontrado

log = logging.getLogger(__name__)


def _parse(spec: str) -> tuple[str, dict[str, str]]:
    kind, _, rest = spec.partition("=")
    kind = kind.strip()
    if kind == "role":
        role, _, name = rest.partition(";name=")
        return "role", {"role": role.strip(), "name": name.strip()}
    return kind, {"value": rest.strip()}


def locator_for(page: Page, spec: str) -> Locator:
    kind, args = _parse(spec)
    rx = re.compile(args.get("name") or args.get("value", ""), re.I)
    if kind == "role":
        return page.get_by_role(args["role"], name=rx) if args.get("name") else page.get_by_role(args["role"])
    if kind == "label":
        return page.get_by_label(rx)
    if kind == "text":
        return page.get_by_text(rx)
    if kind == "placeholder":
        return page.get_by_placeholder(rx)
    if kind == "css":
        return page.locator(args["value"])
    raise ValueError(f"tipo de seletor desconhecido: {spec}")


class Selectors:
    def __init__(self, portal: str = "comprasgov"):
        self.cfg: dict[str, Any] = load_selectors(portal)

    def url(self, chave: str) -> str:
        return self.cfg["urls"][chave]

    def specs(self, grupo: str, chave: str) -> list[str]:
        node = self.cfg[grupo][chave]
        return [node["primary"]] + list(node.get("fallbacks") or [])

    def find(self, page: Page, grupo: str, chave: str, timeout_ms: int = 8000, must: bool = True) -> Locator | None:
        """Primeiro seletor que resolve para um elemento visível. Registra qual funcionou."""
        for spec in self.specs(grupo, chave):
            loc = locator_for(page, spec).first
            try:
                loc.wait_for(state="visible", timeout=timeout_ms)
                log.debug("seletor %s.%s resolvido por '%s'", grupo, chave, spec)
                return loc
            except Exception:  # noqa: BLE001
                continue
        if must:
            raise SeletorNaoEncontrado(f"{grupo}.{chave}: nenhum seletor resolveu ({self.specs(grupo, chave)})")
        return None

    def exists(self, page: Page, grupo: str, chave: str, timeout_ms: int = 3000) -> bool:
        return self.find(page, grupo, chave, timeout_ms=timeout_ms, must=False) is not None
