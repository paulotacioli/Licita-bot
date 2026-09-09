"""Preparação da proposta no Compras.gov.br: localizar o pregão, preencher itens, declarações e anexos.

NUNCA clica no botão de envio (isso é exclusivo de envio.py, após aprovação).
Os seletores vivem em config/selectors/comprasgov.yaml e serão calibrados na Fase 6 com o portal real.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path

from playwright.sync_api import Page
from sqlmodel import select

from licitabot.config import get_settings, load_empresa
from licitabot.db.models import DocumentoGerado, Item, Oportunidade
from licitabot.db.session import db_session, log_evento, set_status
from licitabot.pipeline.states import Status
from licitabot.portal.base import DivergenciaTela, PortalError
from licitabot.portal.comprasgov.anexos import upload_anexos
from licitabot.portal.comprasgov.navigator import Navigator
from licitabot.portal.comprasgov.session import abrir_contexto, ensure_session

log = logging.getLogger(__name__)

DESCRICAO_MAX = 5000


def fmt_valor_ptbr(v: float) -> str:
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _parse_ptbr(txt: str) -> float | None:
    m = re.search(r"\d{1,3}(?:\.\d{3})*(?:,\d{2})|\d+(?:,\d{2})?", txt or "")
    if not m:
        return None
    return float(m.group(0).replace(".", "").replace(",", "."))


def abrir_pregao(nav: Navigator, op: Oportunidade) -> None:
    """Chega na tela de cadastro de proposta do pregão (UASG + número)."""
    page = nav.page
    sel = nav.sel
    with nav.step("area_trabalho"):
        page.goto(sel.url("area_trabalho"), wait_until="domcontentloaded")
        nav.fechar_modais_inesperados()
    if op.link_sistema_origem and "compras.gov.br" in op.link_sistema_origem:
        with nav.step("link_origem"):
            page.goto(op.link_sistema_origem, wait_until="domcontentloaded")
            nav.fechar_modais_inesperados()
    else:
        with nav.step("menu_propostas"):
            sel.find(page, "navegacao", "menu_propostas").click()
        with nav.step("buscar"):
            campo = sel.find(page, "navegacao", "busca_pregao_input")
            termo = op.numero_compra or op.numero_controle_pncp.split("-")[-1].split("/")[0].lstrip("0")
            campo.fill(termo)
            campo.press("Enter")
            page.wait_for_timeout(3000)
    with nav.step("participar"):
        btn = sel.find(page, "navegacao", "abrir_compra", timeout_ms=20000, must=False)
        if btn:
            btn.click()
            page.wait_for_timeout(3000)
    nav.resumo["url_pregao"] = page.url


def preencher_item(nav: Navigator, item: Item, exige_marca: bool) -> dict:
    page = nav.page
    sel = nav.sel
    valor = fmt_valor_ptbr(item.valor_unit_proposto or 0)
    with nav.step(f"item_{item.numero_item}"):
        # Localiza a linha do item pelo número e abre a edição
        linha = page.get_by_role("row").filter(has_text=re.compile(rf"^\s*{item.numero_item}\b")).first
        if linha.count():
            linha.click()
            page.wait_for_timeout(1000)
        campo_valor = sel.find(page, "proposta", "valor_unitario")
        campo_valor.fill("")
        campo_valor.type(valor, delay=30)
        qtd = sel.find(page, "proposta", "quantidade_ofertada", timeout_ms=3000, must=False)
        if qtd and not qtd.input_value().strip():
            qtd.fill(f"{item.quantidade:g}")
        if exige_marca:
            for chave in ("marca", "fabricante", "modelo"):
                c = sel.find(page, "proposta", chave, timeout_ms=3000, must=False)
                if c:
                    c.fill(item.marca_modelo[:60] if chave != "fabricante" else load_empresa().razao_social[:60])
        desc = sel.find(page, "proposta", "descricao_detalhada", timeout_ms=5000, must=False)
        if desc:
            texto = (item.descricao_detalhada_proposta or item.descricao)[:DESCRICAO_MAX]
            desc.fill(texto)
        salvar = sel.find(page, "proposta", "salvar_item", timeout_ms=5000, must=False)
        if salvar:
            salvar.click()
            page.wait_for_timeout(2000)
        nav.fechar_modais_inesperados()
    return {"numero_item": item.numero_item, "valor_unitario": valor, "marca": item.marca_modelo}


def marcar_declaracoes(nav: Navigator) -> int:
    page = nav.page
    n = 0
    with nav.step("declaracoes"):
        boxes = page.get_by_role("checkbox")
        total = boxes.count()
        for i in range(total):
            cb = boxes.nth(i)
            try:
                rotulo = (cb.get_attribute("aria-label") or "").lower()
                # nunca marcar automaticamente algo que fale em ME/EPP se a empresa não for
                if ("microempresa" in rotulo or "pequeno porte" in rotulo) and load_empresa().porte not in ("ME", "EPP"):
                    continue
                if not cb.is_checked():
                    cb.check()
                    n += 1
            except Exception as e:  # noqa: BLE001
                log.debug("checkbox %s ignorado: %s", i, e)
    return n


def ler_valores_na_tela(page: Page, itens: list[Item]) -> dict[int, float | None]:
    """Lê do DOM o valor unitário exibido para cada item (para conferir com o aprovado)."""
    out: dict[int, float | None] = {}
    for it in itens:
        linha = page.get_by_role("row").filter(has_text=re.compile(rf"^\s*{it.numero_item}\b")).first
        try:
            txt = linha.inner_text(timeout=3000) if linha.count() else ""
        except Exception:  # noqa: BLE001
            txt = ""
        vals = [float(v.replace(".", "").replace(",", ".")) for v in re.findall(r"\d{1,3}(?:\.\d{3})*,\d{2}", txt)]
        out[it.numero_item] = next((v for v in vals if abs(v - (it.valor_unit_proposto or -1)) < 0.01), vals[0] if vals else None)
    return out


def verificar_tela(page: Page, itens: list[Item]) -> None:
    lidos = ler_valores_na_tela(page, itens)
    diverg = [(n, v, next(i.valor_unit_proposto for i in itens if i.numero_item == n)) for n, v in lidos.items() if v is None or abs(v - next(i.valor_unit_proposto for i in itens if i.numero_item == n)) > 0.01]
    if diverg:
        raise DivergenciaTela(f"valores na tela divergem do aprovado: {diverg}")


def executar_preenchimento(nav: Navigator, op: Oportunidade, itens: list[Item], docs: list[DocumentoGerado], exige_marca: bool) -> dict:
    """Preenchimento completo (idempotente). Usado tanto no prepare (dry) quanto no envio real."""
    abrir_pregao(nav, op)
    resumo = {"itens": [], "declaracoes": 0, "anexos": []}
    for it in itens:
        resumo["itens"].append(preencher_item(nav, it, exige_marca))
    resumo["declaracoes"] = marcar_declaracoes(nav)
    resumo["anexos"] = upload_anexos(nav, [Path(d.caminho_pdf) for d in docs if d.caminho_pdf and Path(d.caminho_pdf).exists()])
    nav.resumo.update(resumo)
    return resumo


def prepare_proposal(oportunidade_id: int) -> None:
    """Etapa `prepare`: preenche tudo, tira screenshots, para antes do envio."""
    s = get_settings()
    with db_session() as session:
        op = session.get(Oportunidade, oportunidade_id)
        itens = session.exec(select(Item).where(Item.oportunidade_id == op.id, Item.participar == True).order_by(Item.numero_item)).all()  # noqa: E712
        docs = session.exec(select(DocumentoGerado).where(DocumentoGerado.oportunidade_id == op.id, DocumentoGerado.status.in_(["gerado", "do_cofre"])).order_by(DocumentoGerado.ordem)).all()
        from licitabot.pipeline.analysis import get_requisitos

        req = get_requisitos(session, op)
        exige_marca = bool(req and req.proposta_formato.exige_marca_modelo)
        pasta = Path(op.pasta)
        session.expunge_all()
    erro = ""
    ok = False
    with abrir_contexto(headless=False) as (ctx, page):
        nav = Navigator(ctx, page, op.id, "preparar", pasta)
        try:
            ensure_session(page, nav.sel)
            resumo = executar_preenchimento(nav, op, itens, docs, exige_marca)
            (pasta / "resumo_portal.json").write_text(json.dumps(resumo, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            ok = True
        except Exception as e:  # noqa: BLE001
            erro = f"{type(e).__name__}: {e}"
            log.exception("preparação no portal falhou")
        finally:
            nav.finalizar(ok, erro)
    with db_session() as session:
        op = session.get(Oportunidade, oportunidade_id)
        if ok:
            set_status(session, op, Status.PREPARADA_PORTAL, f"preenchida no portal (dry_run={s.dry_run}); aguardando e-mail de aprovação")
        else:
            set_status(session, op, Status.ERRO, f"prepare: {erro}"[:500], erro=erro)
        log_evento(session, "portal", f"preparar: ok={ok} {erro}"[:1000], op.id)
        session.commit()
    if not ok:
        raise PortalError(erro)
