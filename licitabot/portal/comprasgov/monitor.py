"""Monitoramento pós-envio (leitura apenas) e smoke test de sessão/seletores."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from rich import print as rprint

from licitabot.config import get_settings
from licitabot.db.models import Oportunidade
from licitabot.portal.comprasgov.navigator import Navigator
from licitabot.portal.comprasgov.session import abrir_contexto, ensure_session, is_logged_in

log = logging.getLogger(__name__)

SELETORES_CRITICOS = [
    ("session", "logged_in_marker"),
    ("navegacao", "menu_propostas"),
]


def smoke_test() -> bool:
    ok_total = True
    with abrir_contexto(headless=False) as (ctx, page):
        from licitabot.portal.comprasgov.selectors import Selectors

        sel = Selectors()
        logado = is_logged_in(page, sel)
        rprint(f"Sessão gov.br: {'[green]OK[/green]' if logado else '[red]NÃO LOGADO[/red] (rode licitabot portal login)'}")
        if not logado:
            return False
        for grupo, chave in SELETORES_CRITICOS:
            achou = sel.exists(page, grupo, chave, timeout_ms=8000)
            ok_total &= achou
            rprint(f"  {grupo}.{chave}: {'[green]ok[/green]' if achou else '[red]não encontrado[/red]'}")
        shot = get_settings().data_path / "smoke_test.png"
        page.screenshot(path=str(shot), full_page=True)
        rprint(f"Screenshot: {shot}")
    return ok_total


def ler_situacao(op: Oportunidade) -> dict:
    """Abre o pregão e lê situação dos itens, mensagens do pregoeiro e convocações (sem interagir)."""
    pasta = Path(op.pasta)
    with abrir_contexto(headless=False) as (ctx, page):
        nav = Navigator(ctx, page, op.id, "monitorar", pasta)
        ok = False
        erro = ""
        dados: dict = {"itens": [], "mensagens": [], "alertas": []}
        try:
            ensure_session(page, nav.sel)
            with nav.step("abrir"):
                if op.link_sistema_origem:
                    page.goto(op.link_sistema_origem, wait_until="domcontentloaded")
                else:
                    page.goto(nav.sel.url("area_trabalho"), wait_until="domcontentloaded")
                page.wait_for_timeout(3000)
                nav.fechar_modais_inesperados()
            corpo = page.inner_text("body")
            for m in re.finditer(r"(?im)^(?:item\s*)?(\d{1,4})\b.*?(aceit\w+|habilitad\w+|desclassificad\w+|em disputa|aberto|encerrad\w+|suspens\w+|cancelad\w+|adjudicad\w+|homologad\w+)", corpo):
                dados["itens"].append({"item": int(m.group(1)), "situacao": m.group(2).lower()})
            chat = nav.sel.find(page, "monitor", "chat_mensagens", timeout_ms=4000, must=False)
            if chat:
                with nav.step("chat"):
                    chat.click()
                    page.wait_for_timeout(2000)
                    txt = page.inner_text("body")
                    dados["mensagens"] = [ln.strip() for ln in txt.splitlines() if re.search(r"pregoeir|convoc|prazo|documenta|proposta ajustada|negocia", ln, re.I)][:40]
            for ln in dados["mensagens"]:
                if re.search(r"convoc|prazo de|envie|anex", ln, re.I):
                    dados["alertas"].append(ln)
            nav.resumo = dados
            ok = True
        except Exception as e:  # noqa: BLE001
            erro = f"{type(e).__name__}: {e}"
            log.exception("monitoramento falhou")
        finally:
            nav.finalizar(ok, erro)
        if not ok:
            dados["erro"] = erro
        return dados
