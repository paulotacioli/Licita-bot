"""Envio efetivo da proposta. ÚNICO módulo que conhece o botão de envio. Exige Aprovacao aprovada e preflight ok."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from sqlmodel import select

from licitabot.approval.service import snapshot_atual_bate
from licitabot.config import get_settings
from licitabot.db.models import Aprovacao, DocumentoGerado, Item, Oportunidade
from licitabot.db.session import db_session
from licitabot.portal.base import DivergenciaTela, PortalError
from licitabot.portal.comprasgov.navigator import Navigator
from licitabot.portal.comprasgov.proposta import executar_preenchimento, verificar_tela
from licitabot.portal.comprasgov.session import abrir_contexto, ensure_session

log = logging.getLogger(__name__)


def submit_no_portal(oportunidade_id: int, aprovacao_id: int) -> dict:
    """Reexecuta o preenchimento, confere a tela contra o aprovado e clica em enviar (salvo DRY_RUN)."""
    s = get_settings()
    with db_session() as session:
        op = session.get(Oportunidade, oportunidade_id)
        ap = session.get(Aprovacao, aprovacao_id)
        if not ap or ap.oportunidade_id != op.id or ap.decisao != "aprovada":
            raise PortalError("aprovação inválida para envio")
        if not snapshot_atual_bate(session, op, ap):
            raise PortalError("snapshot aprovado não corresponde ao estado atual (preços/documentos mudaram)")
        itens = session.exec(select(Item).where(Item.oportunidade_id == op.id, Item.participar == True).order_by(Item.numero_item)).all()  # noqa: E712
        docs = session.exec(select(DocumentoGerado).where(DocumentoGerado.oportunidade_id == op.id, DocumentoGerado.status.in_(["gerado", "do_cofre"])).order_by(DocumentoGerado.ordem)).all()
        from licitabot.pipeline.analysis import get_requisitos

        req = get_requisitos(session, op)
        exige_marca = bool(req and req.proposta_formato.exige_marca_modelo)
        pasta = Path(op.pasta)
        session.expunge_all()

    resultado: dict = {"dry_run": s.dry_run, "enviada": False}
    erro = ""
    ok = False
    with abrir_contexto(headless=False) as (ctx, page):
        nav = Navigator(ctx, page, op.id, "enviar", pasta)
        try:
            ensure_session(page, nav.sel)
            # idempotência: se o portal já mostra a proposta como enviada, não reenviar
            if nav.sel.exists(page, "envio", "recibo_marker", timeout_ms=1500):
                resultado["enviada"] = True
                resultado["observacao"] = "portal já indicava proposta enviada"
                ok = True
            else:
                executar_preenchimento(nav, op, itens, docs, exige_marca)
                verificar_tela(page, itens)
                if s.dry_run:
                    nav.shot("DRY_RUN_parada_antes_do_envio")
                    resultado["observacao"] = "DRY_RUN: parou antes do clique final"
                    ok = True
                else:
                    with nav.step("ENVIAR"):
                        nav.sel.find(page, "envio", "botao_enviar_proposta").click()
                        page.wait_for_timeout(1500)
                        conf = nav.sel.find(page, "envio", "confirmar_dialogo", timeout_ms=5000, must=False)
                        if conf:
                            conf.click()
                        page.wait_for_timeout(4000)
                    with nav.step("recibo"):
                        if not nav.sel.exists(page, "envio", "recibo_marker", timeout_ms=20000):
                            raise PortalError("confirmação de envio não apareceu na tela")
                        comprov = pasta / "comprovante"
                        comprov.mkdir(exist_ok=True)
                        png = comprov / f"recibo_{datetime.now():%Y%m%d_%H%M%S}.png"
                        page.screenshot(path=str(png), full_page=True)
                        try:
                            page.pdf(path=str(png.with_suffix(".pdf")))
                        except Exception:  # noqa: BLE001
                            pass
                        resultado["comprovante"] = str(png)
                        resultado["enviada"] = True
                        resultado["texto_recibo"] = page.inner_text("body")[:2000]
                    ok = True
        except DivergenciaTela as e:
            erro = f"DIVERGÊNCIA: {e}"
            log.error(erro)
        except Exception as e:  # noqa: BLE001
            erro = f"{type(e).__name__}: {e}"
            log.exception("envio falhou")
        finally:
            nav.resumo.update(resultado)
            nav.finalizar(ok, erro)
    if not ok:
        raise PortalError(erro)
    return resultado
