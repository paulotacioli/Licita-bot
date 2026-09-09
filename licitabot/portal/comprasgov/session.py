"""Sessão do navegador: perfil persistente (login humano no gov.br), lock de exclusividade, verificação de login."""

from __future__ import annotations

import json
import logging
import os
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator

from playwright.sync_api import BrowserContext, Page, sync_playwright

from licitabot.config import get_settings
from licitabot.portal.base import PortalError, SessaoExpirada
from licitabot.portal.comprasgov.selectors import Selectors

log = logging.getLogger(__name__)

ACTION_TIMEOUT_MS = 45_000
NAV_TIMEOUT_MS = 90_000
CACHE_SESSAO_HORAS = 6


def _lock_path() -> Path:
    return get_settings().data_path / "browser.lock"


def _estado_path() -> Path:
    return get_settings().data_path / "sessao_portal.json"


def _gravar_estado(ok: bool, msg: str = "") -> None:
    _estado_path().write_text(json.dumps({"ok": ok, "msg": msg, "quando": datetime.now().isoformat()}), encoding="utf-8")


def sessao_valida_cache() -> tuple[bool, str]:
    """Última verificação de login conhecida (sem abrir navegador). Usada pelo checklist de onboarding."""
    p = _estado_path()
    if not p.exists():
        return False, "nunca verificado: rode `licitabot portal login`"
    d = json.loads(p.read_text(encoding="utf-8"))
    quando = datetime.fromisoformat(d["quando"])
    if not d.get("ok"):
        return False, d.get("msg") or "sessão inválida"
    if datetime.now() - quando > timedelta(hours=CACHE_SESSAO_HORAS):
        return True, f"último login válido em {quando:%d/%m %H:%M} (será reverificado ao usar o portal)"
    return True, f"sessão válida ({quando:%d/%m %H:%M})"


@contextmanager
def browser_lock(timeout_s: int = 120) -> Iterator[None]:
    """Um único navegador automatizado por vez (perfil persistente não suporta concorrência)."""
    lp = _lock_path()
    inicio = time.time()
    while lp.exists():
        try:
            pid = int(lp.read_text().strip() or 0)
        except ValueError:
            pid = 0
        if pid and not _pid_vivo(pid):
            lp.unlink(missing_ok=True)
            break
        if time.time() - inicio > timeout_s:
            raise PortalError("outro processo está usando o navegador (data/browser.lock)")
        time.sleep(2)
    lp.write_text(str(os.getpid()))
    try:
        yield
    finally:
        lp.unlink(missing_ok=True)


def _pid_vivo(pid: int) -> bool:
    try:
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    except Exception:  # noqa: BLE001
        return True


@contextmanager
def abrir_contexto(headless: bool = False) -> Iterator[tuple[BrowserContext, Page]]:
    """Chromium com perfil persistente. Sempre `headed` por padrão (gov.br + certificado se comportam melhor)."""
    s = get_settings()
    s.ensure_dirs()
    with browser_lock(), sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(s.browser_profile_path),
            headless=headless,
            channel="chrome" if Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe").exists() else None,
            args=["--disable-blink-features=AutomationControlled", "--start-maximized"],
            viewport=None,
            locale="pt-BR",
            timezone_id="America/Sao_Paulo",
            accept_downloads=True,
        )
        ctx.set_default_timeout(ACTION_TIMEOUT_MS)
        ctx.set_default_navigation_timeout(NAV_TIMEOUT_MS)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            yield ctx, page
        finally:
            try:
                ctx.close()
            except Exception:  # noqa: BLE001
                pass


def is_logged_in(page: Page, sel: Selectors | None = None) -> bool:
    sel = sel or Selectors()
    try:
        page.goto(sel.url("area_trabalho"), wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
    except Exception as e:  # noqa: BLE001
        log.warning("navegação para área do fornecedor falhou: %s", e)
        return False
    if page.url.startswith(sel.url("sso_prefix")) or sel.exists(page, "session", "login_redirect_marker", timeout_ms=2000):
        return False
    return sel.exists(page, "session", "logged_in_marker", timeout_ms=8000)


def ensure_session(page: Page, sel: Selectors | None = None) -> None:
    if is_logged_in(page, sel):
        _gravar_estado(True)
        return
    _gravar_estado(False, "sessão gov.br expirada ou inexistente")
    _avisar_sessao_expirada()
    raise SessaoExpirada("sessão gov.br expirada; rode `licitabot portal login`")


def _avisar_sessao_expirada() -> None:
    try:
        from licitabot.pipeline.notify import enviar_alerta

        enviar_alerta(
            "Sessão do Compras.gov.br expirou",
            "Ação necessária: refazer login",
            ["A sessão gov.br no navegador automatizado expirou.", "Rode `licitabot portal login`, faça login com o certificado e feche o navegador."],
        )
    except Exception as e:  # noqa: BLE001
        log.warning("alerta de sessão não enviado: %s", e)


def login_interativo(timeout_min: int = 15) -> bool:
    """Abre o navegador para o humano logar; aguarda até detectar a área do fornecedor."""
    sel = Selectors()
    with abrir_contexto(headless=False) as (ctx, page):
        page.goto(sel.url("login_fornecedor"), wait_until="domcontentloaded")
        print("\n>>> Faça login no gov.br (certificado digital / conta) nesta janela. Aguardando até %d min...\n" % timeout_min)
        fim = time.time() + timeout_min * 60
        while time.time() < fim:
            page.wait_for_timeout(5000)
            try:
                if not page.url.startswith(sel.url("sso_prefix")) and sel.exists(page, "session", "logged_in_marker", timeout_ms=1500):
                    _gravar_estado(True)
                    print(">>> Login detectado. Sessão salva no perfil persistente.")
                    ctx.storage_state(path=str(get_settings().data_path / "storage_state.json"))
                    return True
            except Exception:  # noqa: BLE001
                pass
        _gravar_estado(False, "login não detectado no tempo limite")
        print(">>> Login não detectado. Verifique os seletores em config/selectors/comprasgov.yaml (session.logged_in_marker).")
        return False
