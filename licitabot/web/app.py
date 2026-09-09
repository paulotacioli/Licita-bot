"""FastAPI: dashboard local + endpoints públicos de aprovação (GET mostra confirmação, POST decide)."""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import select

from licitabot.approval.service import decidir_por_token, descricao_token
from licitabot.config import get_settings
from licitabot.db.models import Aprovacao, DocumentoGerado, Evento, Item, OnboardingPasso, Oportunidade, Triagem
from licitabot.db.session import db_session, init_db
from licitabot.pipeline.analysis import get_requisitos
from licitabot.pipeline.states import Status

log = logging.getLogger(__name__)
app = FastAPI(title="Licita-Bot")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
_hits: dict[str, list[float]] = defaultdict(list)


@app.on_event("startup")
def _startup() -> None:
    init_db()


def _rate_limited(ip: str, limite: int = 30, janela: int = 60) -> bool:
    agora = time.time()
    _hits[ip] = [t for t in _hits[ip] if agora - t < janela]
    _hits[ip].append(agora)
    return len(_hits[ip]) > limite


# ---------- aprovação (pública via tunnel) ----------


@app.get("/aprovar/{token}", response_class=HTMLResponse)
@app.get("/rejeitar/{token}", response_class=HTMLResponse)
def confirmar(request: Request, token: str):
    if _rate_limited(request.client.host if request.client else "?"):
        return HTMLResponse("Muitas requisições.", status_code=429)
    info = descricao_token(token)
    return templates.TemplateResponse(request, "confirmar.html", {"info": info, "token": token})


@app.post("/decidir", response_class=HTMLResponse)
def decidir(request: Request, token: str = Form(...)):
    ip = request.client.host if request.client else "?"
    if _rate_limited(ip):
        return HTMLResponse("Muitas requisições.", status_code=429)
    ok, msg = decidir_por_token(token, ip=ip)
    return templates.TemplateResponse(request, "resultado.html", {"ok": ok, "msg": msg})


# ---------- dashboard (local) ----------


@app.get("/", response_class=HTMLResponse)
def index(request: Request, status: str | None = None):
    with db_session() as session:
        q = select(Oportunidade).order_by(Oportunidade.data_encerramento_proposta)
        if status:
            q = q.where(Oportunidade.status == status)
        else:
            q = q.where(Oportunidade.status.not_in([Status.DESCARTADA, Status.EXPIRADA]))
        ops = session.exec(q.limit(300)).all()
        from sqlalchemy import func

        contagem = dict(session.exec(select(Oportunidade.status, func.count()).group_by(Oportunidade.status)).all())
        passos = session.exec(select(OnboardingPasso).order_by(OnboardingPasso.ordem)).all()
        return templates.TemplateResponse(request, "index.html", {"ops": ops, "contagem": contagem, "status": status, "passos": passos, "dry_run": get_settings().dry_run},
        )


@app.get("/oportunidades/{oid}", response_class=HTMLResponse)
def detalhe(request: Request, oid: int):
    with db_session() as session:
        op = session.get(Oportunidade, oid)
        if not op:
            return HTMLResponse("não encontrada", status_code=404)
        itens = session.exec(select(Item).where(Item.oportunidade_id == oid).order_by(Item.numero_item)).all()
        docs = session.exec(select(DocumentoGerado).where(DocumentoGerado.oportunidade_id == oid).order_by(DocumentoGerado.ordem)).all()
        tri = session.exec(select(Triagem).where(Triagem.oportunidade_id == oid).order_by(Triagem.id.desc())).first()
        req = get_requisitos(session, op)
        evs = session.exec(select(Evento).where(Evento.oportunidade_id == oid).order_by(Evento.id.desc()).limit(40)).all()
        aps = session.exec(select(Aprovacao).where(Aprovacao.oportunidade_id == oid).order_by(Aprovacao.id.desc())).all()
        return templates.TemplateResponse(request, "detalhe.html", {"op": op, "itens": itens, "docs": docs, "tri": tri, "req": req, "evs": evs, "aps": aps},
        )


@app.post("/oportunidades/{oid}/acao")
def acao(oid: int, acao: str = Form(...)):
    """Ações locais do dashboard: reprocessar, descartar, retomar, aprovar/rejeitar (CLI-equivalente)."""
    from licitabot.approval.service import decidir_por_cli
    from licitabot.db.session import set_status
    from licitabot.pipeline.runner import retomar_erro

    if acao == "aprovar":
        decidir_por_cli(oid, True)
    elif acao == "rejeitar":
        decidir_por_cli(oid, False)
    elif acao == "retomar":
        retomar_erro(oid)
    elif acao == "descartar":
        with db_session() as session:
            op = session.get(Oportunidade, oid)
            set_status(session, op, Status.DESCARTADA, "descartada manualmente no dashboard")
            session.commit()
    elif acao == "promover":
        with db_session() as session:
            op = session.get(Oportunidade, oid)
            if op.status == Status.ZONA_CINZENTA:
                set_status(session, op, Status.TRIADA_RELEVANTE, "promovida manualmente no dashboard")
                session.commit()
    return RedirectResponse(url=f"/oportunidades/{oid}", status_code=303)


@app.post("/oportunidades/{oid}/precos-salvar")
async def salvar_precos(request: Request, oid: int):
    """Edição de preços por item (form: unit_<numero_item>=valor, part_<numero_item>=on)."""
    form = await request.form()
    with db_session() as session:
        itens = session.exec(select(Item).where(Item.oportunidade_id == oid)).all()
        for it in itens:
            raw = form.get(f"unit_{it.numero_item}")
            if raw not in (None, ""):
                try:
                    v = float(str(raw).replace(".", "").replace(",", ".")) if "," in str(raw) else float(raw)
                except ValueError:
                    continue
                it.valor_unit_proposto = round(v, 2)
                it.valor_total_proposto = round(v * (it.quantidade or 1), 2)
                it.justificativa_preco = "editado manualmente no dashboard"
            it.participar = form.get(f"part_{it.numero_item}") is not None
            session.add(it)
        op = session.get(Oportunidade, oid)
        if op.status in (Status.AGUARDANDO_APROVACAO, Status.PREPARADA_PORTAL, Status.DOCS_GERADOS):
            from licitabot.db.session import set_status

            # preços mudaram: volta para regenerar documentos e novo e-mail
            set_status(session, op, Status.PRECIFICADA, "preços editados manualmente; documentos serão regerados")
        session.commit()
    return RedirectResponse(url=f"/oportunidades/{oid}", status_code=303)


@app.get("/onboarding", response_class=HTMLResponse)
def onboarding(request: Request):
    from licitabot.sicaf.checklist import onboarding_completo

    ok, faltando = onboarding_completo()
    with db_session() as session:
        passos = session.exec(select(OnboardingPasso).order_by(OnboardingPasso.ordem)).all()
        return templates.TemplateResponse(request, "onboarding.html", {"passos": passos, "ok": ok, "faltando": faltando})


@app.post("/onboarding/{chave}")
def onboarding_marcar(chave: str, concluido: str = Form("0")):
    with db_session() as session:
        p = session.exec(select(OnboardingPasso).where(OnboardingPasso.chave == chave)).first()
        if p and not p.verificacao_automatica:
            p.concluido = concluido == "1"
            session.add(p)
            session.commit()
    return RedirectResponse(url="/onboarding", status_code=303)
