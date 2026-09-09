"""Navegação instrumentada: passos com screenshot antes/depois, trace do Playwright e registro em execucao_portal."""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

from playwright.sync_api import BrowserContext, Page

from licitabot.config import get_settings
from licitabot.db.models import ExecucaoPortal
from licitabot.db.session import db_session
from licitabot.portal.comprasgov.selectors import Selectors

log = logging.getLogger(__name__)


class Navigator:
    def __init__(self, ctx: BrowserContext, page: Page, oportunidade_id: int | None, etapa: str, pasta: Path):
        self.ctx = ctx
        self.page = page
        self.sel = Selectors()
        self.etapa = etapa
        self.pasta = pasta / "screenshots"
        self.pasta.mkdir(parents=True, exist_ok=True)
        self.shots: list[str] = []
        self.resumo: dict = {}
        self._ultima_acao = 0.0
        with db_session() as session:
            ex = ExecucaoPortal(oportunidade_id=oportunidade_id, etapa=etapa, dry_run=get_settings().dry_run)
            session.add(ex)
            session.commit()
            self.exec_id = ex.id
        try:
            ctx.tracing.start(screenshots=True, snapshots=True, sources=False)
        except Exception:  # noqa: BLE001
            pass

    def shot(self, nome: str) -> str:
        ts = datetime.now().strftime("%H%M%S")
        p = self.pasta / f"{ts}_{self.etapa}_{nome}.png"
        try:
            self.page.screenshot(path=str(p), full_page=True)
            self.shots.append(str(p))
        except Exception as e:  # noqa: BLE001
            log.warning("screenshot falhou: %s", e)
        return str(p)

    @contextmanager
    def step(self, nome: str) -> Iterator[None]:
        # nunca mais de ~1 ação por segundo
        delta = time.time() - self._ultima_acao
        if delta < 1.0:
            time.sleep(1.0 - delta)
        self.shot(f"{nome}_antes")
        try:
            yield
        except Exception:
            self.shot(f"{nome}_ERRO")
            raise
        finally:
            self._ultima_acao = time.time()
        self.shot(f"{nome}_depois")

    def fechar_modais_inesperados(self) -> None:
        try:
            dlg = self.page.get_by_role("dialog").first
            if dlg.is_visible(timeout=1000):
                txt = dlg.inner_text()[:300]
                log.warning("modal inesperado: %s", txt)
                self.shot("modal")
                for nome in ("Fechar", "OK", "Ok", "Entendi", "Continuar"):
                    b = dlg.get_by_role("button", name=nome)
                    if b.count():
                        b.first.click()
                        return
        except Exception:  # noqa: BLE001
            pass

    def finalizar(self, sucesso: bool, erro: str = "") -> None:
        trace = ""
        try:
            trace_p = self.pasta / f"trace_{self.etapa}_{datetime.now():%Y%m%d_%H%M%S}.zip"
            self.ctx.tracing.stop(path=str(trace_p))
            trace = str(trace_p)
        except Exception:  # noqa: BLE001
            pass
        with db_session() as session:
            ex = session.get(ExecucaoPortal, self.exec_id)
            ex.finalizado_em = datetime.utcnow()
            ex.sucesso = sucesso
            ex.screenshots = self.shots
            ex.trace_caminho = trace
            ex.resumo_json = self.resumo
            ex.erro = erro[:2000]
            session.add(ex)
            session.commit()
