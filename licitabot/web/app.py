"""FastAPI: painel autenticado + endpoints públicos de aprovação.

Duas zonas com regras diferentes:
  - Painel (/, /configuracoes, /onboarding, ...): exige sessão iniciada em /login.
  - Aprovação (/aprovar, /rejeitar, /decidir): sem login, porque o link chega por e-mail.
    A proteção ali é o token assinado, de uso único, com prazo, mais limite de requisições por IP.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlmodel import select

from licitabot.approval.service import decidir_por_token, descricao_token
from licitabot.config import exigir_liberacao, get_settings, load_empresa
from licitabot.db.models import Aprovacao, DocumentoGerado, Evento, Item, OnboardingPasso, Oportunidade, Triagem
from licitabot.db.session import db_session, init_db
from licitabot.pipeline import premissas
from licitabot.pipeline.analysis import get_requisitos
from licitabot.pipeline.states import Status
from licitabot.pipeline.texto import limpar_objeto
from licitabot.web import auth
from licitabot.web import config_service as cfg

log = logging.getLogger(__name__)

BASE = Path(__file__).parent
app = FastAPI(title="Licita-Bot", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE / "templates"))

# Versão dos arquivos estáticos: entra na URL do CSS para que um deploy novo não fique
# preso ao cache do navegador de quem já usou o painel.
try:
    ASSETS_V = str(int((BASE / "static" / "app.css").stat().st_mtime))
except OSError:
    ASSETS_V = "0"
templates.env.globals["assets_v"] = ASSETS_V

_hits: dict[str, list[float]] = defaultdict(list)
_tentativas_login: dict[str, list[float]] = defaultdict(list)

LIMITE_ATIVAS = 300


# ---------- filtros de formatação ----------


def _brl(v) -> str:
    if v in (None, ""):
        return "—"
    return "R$ " + f"{float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _brl_curto(v) -> str:
    """Valores grandes em forma compacta, para caber na tabela."""
    if v in (None, ""):
        return "—"
    v = float(v)
    if v >= 1_000_000:
        return f"{v / 1_000_000:,.1f} mi".replace(".", ",")
    if v >= 1_000:
        return f"{v / 1_000:,.0f} mil".replace(",", ".")
    return f"{v:,.0f}".replace(",", ".")


def _dt(v) -> str:
    return v.strftime("%d/%m/%y %H:%M") if isinstance(v, datetime) else "—"


def _dt_longo(v) -> str:
    return v.strftime("%d/%m/%Y às %H:%M") if isinstance(v, datetime) else "—"


def _faltam(v) -> str:
    """Tempo até um prazo, curto, para a tabela: 'faltam 3 d', 'faltam 5 h', 'encerrado'."""
    if not isinstance(v, datetime):
        return ""
    seg = (v - datetime.now()).total_seconds()
    if seg <= 0:
        return "encerrado"
    if seg < 86400:
        return f"faltam {int(seg // 3600)} h"
    return f"faltam {int(seg // 86400)} d"


def _humano(status: str) -> str:
    return str(status).replace("_", " ").capitalize()


templates.env.filters["brl"] = _brl
templates.env.filters["brl_curto"] = _brl_curto
templates.env.filters["dt"] = _dt
templates.env.filters["dt_longo"] = _dt_longo
templates.env.filters["humano"] = _humano
templates.env.filters["faltam"] = _faltam
templates.env.filters["objeto"] = lambda t: limpar_objeto(t, 260)


@app.on_event("startup")
def _startup() -> None:
    init_db()


# ---------- limites por IP ----------


def _ip(request: Request) -> str:
    return request.client.host if request.client else "?"


def _rate_limited(ip: str, limite: int = 30, janela: int = 60, balde: dict | None = None) -> bool:
    alvo = balde if balde is not None else _hits
    agora = time.time()
    alvo[ip] = [t for t in alvo[ip] if agora - t < janela]
    alvo[ip].append(agora)
    return len(alvo[ip]) > limite


# ---------- sessão ----------


SEM_SEGREDO = """<!doctype html><meta charset="utf-8">
<title>Configuração incompleta</title>
<style>body{font-family:system-ui,sans-serif;max-width:640px;margin:14vh auto;padding:0 24px;color:#1e293b;line-height:1.6}
code{background:#f1f5f9;padding:2px 6px;border-radius:4px;font-size:13px}h1{font-size:20px}</style>
<h1>Falta o APPROVAL_SECRET</h1>
<p>O painel assina a sessão de login com esse segredo, e ele não está definido no arquivo
<code>.env</code> do projeto. Sem ele ninguém consegue entrar.</p>
<p>Gere um valor e coloque no <code>.env</code>:</p>
<pre><code>python3 -c "import secrets;print(secrets.token_urlsafe(48))"</code></pre>
<p>Depois reinicie o serviço.</p>"""


def _sem_segredo() -> bool:
    return len(get_settings().approval_secret) < 32


@app.middleware("http")
async def exigir_login(request: Request, call_next):
    if request.url.path.startswith("/static/") or request.url.path == "/healthz":
        return await call_next(request)
    if _sem_segredo():
        return HTMLResponse(SEM_SEGREDO, status_code=503)
    if auth.rota_publica(request.url.path):
        return await call_next(request)
    if not auth.existe_usuario():
        return RedirectResponse(url="/primeiro-acesso", status_code=303)
    sessao = auth.ler_sessao(request)
    if not sessao:
        destino = request.url.path
        if request.url.query:
            destino += "?" + request.url.query
        return RedirectResponse(url=f"/login?destino={destino}", status_code=303)
    request.state.sessao = sessao
    return await call_next(request)


def _ctx(request: Request, **extra) -> dict:
    """Contexto comum a todas as páginas do painel: navegação, contadores e identificação."""
    with db_session() as session:
        contagem = dict(session.exec(select(Oportunidade.status, func.count()).group_by(Oportunidade.status)).all())
        pendencias = session.exec(
            select(func.count()).select_from(OnboardingPasso).where(OnboardingPasso.concluido == False)  # noqa: E712
        ).one()
        compativeis = session.exec(select(func.count()).select_from(Oportunidade).where(_compativel_clausula())).one()
    inativos = {Status.DESCARTADA, Status.EXPIRADA, Status.REJEITADA, Status.ENCERRADA}
    nav = {
        "ativas": sum(n for st, n in contagem.items() if st not in inativos),
        "aprovacao": contagem.get(Status.AGUARDANDO_APROVACAO, 0),
        "cinzenta": contagem.get(Status.ZONA_CINZENTA, 0),
        "bloqueadas": contagem.get(Status.BLOQUEADA, 0) + contagem.get(Status.ERRO, 0),
        "compativeis": compativeis or 0,
        "pendencias": pendencias or 0,
    }
    sessao = getattr(request.state, "sessao", None) or {}
    base = {
        "nav_contagem": nav,
        "contagem": contagem,
        "usuario": sessao.get("u", ""),
        "dry_run": get_settings().dry_run,
        "exige_liberacao": exigir_liberacao(),
        "empresa_nome": (load_empresa().nome_fantasia or load_empresa().razao_social or "")[:26],
        "mensagem": request.query_params.get("ok"),
    }
    base.update(extra)
    return base


# ---------- login ----------


@app.get("/primeiro-acesso", response_class=HTMLResponse)
def primeiro_acesso_form(request: Request):
    if auth.existe_usuario():
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse(request, "primeiro_acesso.html", {})


@app.post("/primeiro-acesso", response_class=HTMLResponse)
def primeiro_acesso(request: Request, usuario: str = Form(...), senha: str = Form(...), senha2: str = Form(...)):
    if auth.existe_usuario():
        return RedirectResponse(url="/login", status_code=303)
    erro = None
    if not usuario.strip():
        erro = "Escolha um nome de usuário."
    elif senha != senha2:
        erro = "As duas senhas não são iguais."
    else:
        erro = auth.forca_da_senha(senha)
    if erro:
        return templates.TemplateResponse(request, "primeiro_acesso.html", {"erro": erro, "usuario_preenchido": usuario})
    auth.criar_usuario(usuario, senha)
    u = auth.autenticar(usuario, senha)
    resposta = RedirectResponse(url="/", status_code=303)
    _gravar_cookie(request, resposta, u)
    log.info("usuário do painel criado: %s", usuario)
    return resposta


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, destino: str = "/"):
    if not auth.existe_usuario():
        return RedirectResponse(url="/primeiro-acesso", status_code=303)
    if auth.ler_sessao(request):
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"destino": destino})


@app.post("/login", response_class=HTMLResponse)
def login(request: Request, usuario: str = Form(...), senha: str = Form(...), destino: str = Form("/")):
    ip = _ip(request)
    if _rate_limited(ip, limite=8, janela=300, balde=_tentativas_login):
        log.warning("excesso de tentativas de login de %s", ip)
        return templates.TemplateResponse(
            request, "login.html",
            {"erro": "Muitas tentativas. Espere alguns minutos.", "destino": destino}, status_code=429)
    u = auth.autenticar(usuario, senha)
    if not u:
        return templates.TemplateResponse(
            request, "login.html",
            {"erro": "Usuário ou senha incorretos.", "destino": destino, "usuario_preenchido": usuario},
            status_code=401)
    alvo = destino if destino.startswith("/") and not destino.startswith("//") else "/"
    resposta = RedirectResponse(url=alvo, status_code=303)
    _gravar_cookie(request, resposta, u)
    return resposta


def _gravar_cookie(request: Request, resposta: Response, usuario) -> None:
    seguro = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    resposta.set_cookie(
        auth.COOKIE, auth.criar_sessao(usuario),
        max_age=auth.DURACAO_SESSAO_S, httponly=True, samesite="lax", secure=seguro, path="/")


# ---------- criar conta com convite (pública) ----------


@app.get("/criar-conta", response_class=HTMLResponse)
def criar_conta_form(request: Request, convite: str = ""):
    if not auth.existe_usuario():
        return RedirectResponse(url="/primeiro-acesso", status_code=303)
    return templates.TemplateResponse(request, "criar_conta.html", {"convite": convite})


@app.post("/criar-conta", response_class=HTMLResponse)
def criar_conta(request: Request, convite: str = Form(""), usuario: str = Form(...), senha: str = Form(...), senha2: str = Form(...)):
    if not auth.existe_usuario():
        return RedirectResponse(url="/primeiro-acesso", status_code=303)
    ip = _ip(request)
    if _rate_limited(ip, limite=8, janela=300, balde=_tentativas_login):
        return templates.TemplateResponse(request, "criar_conta.html", {"erro": "Muitas tentativas. Espere alguns minutos.", "convite": ""}, status_code=429)
    erro = auth.validar_convite(convite) or auth.validar_novo_usuario(usuario, senha, senha2)
    if erro:
        return templates.TemplateResponse(request, "criar_conta.html", {"erro": erro, "convite": convite, "usuario_preenchido": usuario}, status_code=400)
    auth.criar_usuario(usuario, senha)
    auth.consumir_convite(convite, usuario)
    u = auth.autenticar(usuario, senha)
    resposta = RedirectResponse(url="/", status_code=303)
    _gravar_cookie(request, resposta, u)
    log.info("conta criada por convite: %s (ip %s)", u.usuario, ip)
    return resposta


# ---------- usuários (painel) ----------


def _pagina_usuarios(request: Request, **extra):
    base_url = get_settings().public_base_url.rstrip("/") or f"{request.url.scheme}://{request.url.netloc}"
    return templates.TemplateResponse(request, "usuarios.html", _ctx(
        request, pagina="usuarios", usuarios=auth.listar_usuarios(), convites=auth.convites_abertos(),
        base_url=base_url, eu=request.state.sessao["uid"], **extra))


@app.get("/usuarios", response_class=HTMLResponse)
def usuarios(request: Request):
    return _pagina_usuarios(request)


@app.post("/usuarios/criar", response_class=HTMLResponse)
def usuarios_criar(request: Request, usuario: str = Form(...), senha: str = Form(...), senha2: str = Form(...)):
    erro = auth.validar_novo_usuario(usuario, senha, senha2)
    if erro:
        return _pagina_usuarios(request, erros=[erro])
    auth.criar_usuario(usuario, senha)
    log.info("usuário criado no painel por %s: %s", request.state.sessao["u"], usuario)
    return RedirectResponse(url=f"/usuarios?ok=Usu%C3%A1rio+{usuario.strip().lower()}+criado.", status_code=303)


@app.post("/usuarios/convite", response_class=HTMLResponse)
def usuarios_convite(request: Request):
    codigo, expira = auth.gerar_convite(request.state.sessao["u"])
    return _pagina_usuarios(request, convite_novo=codigo, convite_expira=expira)


@app.post("/usuarios/{uid}/remover")
def usuarios_remover(request: Request, uid: int):
    erro = auth.remover_usuario(uid, request.state.sessao["uid"])
    if erro:
        return _pagina_usuarios(request, erros=[erro])
    return RedirectResponse(url="/usuarios?ok=Usu%C3%A1rio+removido.", status_code=303)


@app.get("/sair")
def sair():
    resposta = RedirectResponse(url="/login", status_code=303)
    resposta.delete_cookie(auth.COOKIE, path="/")
    return resposta


@app.get("/healthz")
def healthz():
    return {"ok": True}


# ---------- conta ----------


@app.get("/conta", response_class=HTMLResponse)
def conta(request: Request):
    from licitabot.db.models import UsuarioPainel

    sessao = request.state.sessao
    with db_session() as session:
        u = session.get(UsuarioPainel, sessao["uid"])
        ultimo = _dt(u.ultimo_acesso_em) if u else "—"
    return templates.TemplateResponse(request, "conta.html", _ctx(request, pagina="conta", ultimo_acesso=ultimo))


@app.post("/conta/senha", response_class=HTMLResponse)
def conta_senha(request: Request, atual: str = Form(...), nova: str = Form(...), nova2: str = Form(...)):
    erro = "As duas senhas novas não são iguais." if nova != nova2 else auth.trocar_senha(request.state.sessao["uid"], atual, nova)
    if erro:
        return templates.TemplateResponse(request, "conta.html", _ctx(request, pagina="conta", erros=[erro], ultimo_acesso=None))
    return RedirectResponse(url="/conta?ok=Senha+alterada.", status_code=303)


# ---------- aprovação (pública) ----------


@app.get("/aprovar/{token}", response_class=HTMLResponse)
@app.get("/rejeitar/{token}", response_class=HTMLResponse)
def confirmar(request: Request, token: str):
    if _rate_limited(_ip(request)):
        return HTMLResponse("Muitas requisições.", status_code=429)
    info = descricao_token(token)
    return templates.TemplateResponse(request, "confirmar.html", {"info": info, "token": token})


@app.post("/decidir", response_class=HTMLResponse)
def decidir(request: Request, token: str = Form(...)):
    ip = _ip(request)
    if _rate_limited(ip):
        return HTMLResponse("Muitas requisições.", status_code=429)
    ok, msg = decidir_por_token(token, ip=ip)
    return templates.TemplateResponse(request, "resultado.html", {"ok": ok, "msg": msg})


# ---------- oportunidades ----------

_TITULOS = {
    Status.AGUARDANDO_APROVACAO: "Aguardando aprovação",
    Status.ZONA_CINZENTA: "Zona cinzenta",
    Status.BLOQUEADA: "Bloqueadas",
    Status.DESCARTADA: "Descartadas",
    Status.ERRO: "Com erro",
}
_PAGINAS = {
    Status.AGUARDANDO_APROVACAO: "aprovacao",
    Status.ZONA_CINZENTA: "cinzenta",
    Status.BLOQUEADA: "bloqueadas",
}


STATUS_COMPATIVEIS = [Status.TRIADA_RELEVANTE, Status.ANALISADA, Status.PRECIFICADA, Status.DOCS_GERADOS, Status.PREPARADA_PORTAL, Status.AGUARDANDO_APROVACAO, Status.APROVADA]


def _compativel_clausula():
    """Compatível = a IA marcou como relevante pelo objeto, ou já passou da triagem completa; nunca encerrada/descartada."""
    from sqlalchemy import and_, or_

    return and_(
        or_(Oportunidade.pre_triagem == "relevante", Oportunidade.status.in_(STATUS_COMPATIVEIS)),
        Oportunidade.status.not_in([Status.DESCARTADA, Status.EXPIRADA, Status.REJEITADA, Status.ENCERRADA]),
    )


def _num(v: str | None) -> float | None:
    """Aceita '200000', '200.000', '200.000,50' e 'R$ 200 mil' (só o número)."""
    if not v:
        return None
    t = str(v).strip().lower().replace("r$", "").replace(" ", "")
    mult = 1.0
    if t.endswith("mil"):
        mult, t = 1_000.0, t[:-3]
    elif t.endswith("mi"):
        mult, t = 1_000_000.0, t[:-2]
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    elif t.count(".") > 1 or (t.count(".") == 1 and len(t.split(".")[1]) == 3):
        t = t.replace(".", "")
    try:
        return float(t) * mult
    except ValueError:
        return None


class Filtros:
    """Filtros da lista, lidos da query string. `link()` monta a URL com um campo trocado, para os atalhos."""

    CAMPOS = ("q", "uf", "mod", "vmin", "vmax", "prazo", "pre", "ordem")

    def __init__(self, request: Request):
        qp = request.query_params
        self.status = qp.get("status") or None
        self.vista = qp.get("vista") or None
        self.q = (qp.get("q") or "").strip()
        self.uf = (qp.get("uf") or "").strip().upper()[:2]
        self.mod = (qp.get("mod") or "").strip()
        self.vmin = _num(qp.get("vmin"))
        self.vmax = _num(qp.get("vmax"))
        try:
            self.prazo = int(qp.get("prazo") or 0) or None
        except ValueError:
            self.prazo = None
        self.pre = (qp.get("pre") or "").strip()
        self.ordem = qp.get("ordem") or "prazo"

    @property
    def ativos(self) -> bool:
        return bool(self.q or self.uf or self.mod or self.vmin or self.vmax or self.prazo or self.pre)

    def link(self, **troca) -> str:
        from urllib.parse import urlencode

        atual = {"status": self.status, "vista": self.vista, "q": self.q, "uf": self.uf, "mod": self.mod,
                 "vmin": int(self.vmin) if self.vmin else "", "vmax": int(self.vmax) if self.vmax else "",
                 "prazo": self.prazo or "", "pre": self.pre, "ordem": self.ordem if self.ordem != "prazo" else ""}
        atual.update(troca)
        return "/?" + urlencode({k: v for k, v in atual.items() if v not in (None, "")})

    def aplicar(self, q):
        from sqlalchemy import or_

        if self.q:
            like = f"%{self.q}%"
            q = q.where(or_(Oportunidade.objeto.ilike(like), Oportunidade.orgao_nome.ilike(like), Oportunidade.municipio.ilike(like)))
        if self.uf:
            q = q.where(Oportunidade.uf == self.uf)
        if self.mod:
            q = q.where(Oportunidade.modalidade_nome == self.mod)
        if self.vmin is not None:
            q = q.where(Oportunidade.valor_estimado >= self.vmin)
        if self.vmax is not None:
            q = q.where(Oportunidade.valor_estimado <= self.vmax)
        if self.prazo:
            from datetime import timedelta

            q = q.where(Oportunidade.data_encerramento_proposta <= datetime.now() + timedelta(days=self.prazo), Oportunidade.data_encerramento_proposta >= datetime.now())
        if self.pre == "nenhuma":
            q = q.where(Oportunidade.pre_triagem == "")
        elif self.pre:
            q = q.where(Oportunidade.pre_triagem == self.pre)
        if self.ordem == "valor":
            q = q.order_by(Oportunidade.valor_estimado.desc().nulls_last())
        elif self.ordem == "valor_asc":
            q = q.order_by(Oportunidade.valor_estimado.asc().nulls_last())
        elif self.ordem == "recente":
            q = q.order_by(Oportunidade.id.desc())
        else:
            q = q.order_by(Oportunidade.data_encerramento_proposta.asc().nulls_last())
        return q


# ---------- liberação para preparar a proposta (link do e-mail, sem login) ----------


@app.get("/liberar/{token}", response_class=HTMLResponse)
def liberar_confirmar(request: Request, token: str):
    """Página de confirmação. Abrir o e-mail (ou o link) não decide nada: só o botão decide."""
    from licitabot.approval.liberacao import descrever

    if _rate_limited(_ip(request), limite=60, janela=60):
        return HTMLResponse("Muitas tentativas. Aguarde um minuto.", status_code=429)
    return templates.TemplateResponse(request, "liberar.html", {"info": descrever(token), "token": token})


@app.post("/liberar", response_class=HTMLResponse)
def liberar(request: Request, token: str = Form(...)):
    from licitabot.approval.liberacao import aplicar

    if _rate_limited(_ip(request), limite=30, janela=60):
        return HTMLResponse("Muitas tentativas. Aguarde um minuto.", status_code=429)
    ok, msg = aplicar(token)
    return templates.TemplateResponse(request, "resultado.html", {"ok": ok, "msg": msg})


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    f = Filtros(request)
    status, vista = f.status, f.vista
    with db_session() as session:
        q = select(Oportunidade)
        if vista == "compativeis":
            q = q.where(_compativel_clausula())
        elif status:
            q = q.where(Oportunidade.status == status)
        else:
            q = q.where(Oportunidade.status.not_in([Status.DESCARTADA, Status.EXPIRADA]))
        ops = session.exec(f.aplicar(q).limit(LIMITE_ATIVAS)).all()
        ufs = sorted({u for u in session.exec(select(Oportunidade.uf).distinct()).all() if u})
        modalidades = sorted({m for m in session.exec(select(Oportunidade.modalidade_nome).distinct()).all() if m})
    if vista == "compativeis":
        titulo, pagina = "Licitações compatíveis", "compativeis"
    elif status:
        titulo, pagina = _TITULOS.get(status, _humano(status)), _PAGINAS.get(status, "oportunidades")
    else:
        titulo, pagina = "Oportunidades ativas", "oportunidades"
    return templates.TemplateResponse(request, "index.html", _ctx(
        request, ops=ops, status=status, vista=vista, f=f, ufs=ufs, modalidades=modalidades,
        limite=LIMITE_ATIVAS, titulo_pagina=titulo, pagina=pagina,
    ))


@app.get("/oportunidades/{oid}", response_class=HTMLResponse)
def detalhe(request: Request, oid: int):
    with db_session() as session:
        op = session.get(Oportunidade, oid)
        if not op:
            return HTMLResponse("Licitação não encontrada.", status_code=404)
        itens = session.exec(select(Item).where(Item.oportunidade_id == oid).order_by(Item.numero_item)).all()
        docs = session.exec(select(DocumentoGerado).where(DocumentoGerado.oportunidade_id == oid).order_by(DocumentoGerado.ordem)).all()
        tri = session.exec(select(Triagem).where(Triagem.oportunidade_id == oid).order_by(Triagem.id.desc())).first()
        req = get_requisitos(session, op)
        evs = session.exec(select(Evento).where(Evento.oportunidade_id == oid).order_by(Evento.id.desc()).limit(40)).all()
        aps = session.exec(select(Aprovacao).where(Aprovacao.oportunidade_id == oid).order_by(Aprovacao.id.desc())).all()
        ck = premissas.para_dict(premissas.montar(session, op, req))
        return templates.TemplateResponse(request, "detalhe.html", _ctx(
            request, op=op, itens=itens, docs=docs, tri=tri, req=req, evs=evs, aps=aps, ck=ck, pagina="oportunidades"))


@app.post("/oportunidades/{oid}/acao")
def acao(request: Request, oid: int, acao: str = Form(...)):
    """Ações do painel, equivalentes aos comandos da CLI."""
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
            set_status(session, op, Status.DESCARTADA, "descartada manualmente no painel")
            session.commit()
    elif acao == "promover":
        with db_session() as session:
            op = session.get(Oportunidade, oid)
            if op.status == Status.ZONA_CINZENTA:
                set_status(session, op, Status.TRIADA_RELEVANTE, "promovida manualmente no painel")
                session.commit()
    elif acao in ("liberar", "nao_participar"):
        from licitabot.approval.liberacao import decidir

        sessao = getattr(request.state, "sessao", None) or {}
        ok, msg = decidir(oid, acao == "liberar", sessao.get("u", "painel"))
        return RedirectResponse(url=f"/oportunidades/{oid}?ok={msg}", status_code=303)
    elif acao == "email_compativel":
        from licitabot.pipeline.notify import send_match_email

        try:
            enviado = send_match_email(oid, forcar=True)
        except Exception as e:  # noqa: BLE001
            log.warning("e-mail de compatível (painel) falhou: %s", e)
            return RedirectResponse(url=f"/oportunidades/{oid}?ok=Falha ao enviar: {str(e)[:120]}", status_code=303)
        msg = "E-mail com o checklist enviado." if enviado else "Nenhum destinatário configurado em Notificações."
        return RedirectResponse(url=f"/oportunidades/{oid}?ok={msg}", status_code=303)
    return RedirectResponse(url=f"/oportunidades/{oid}", status_code=303)


@app.post("/oportunidades/{oid}/precos-salvar")
async def salvar_precos(request: Request, oid: int):
    """Edição de preços por item (form: unit_<numero_item>, part_<numero_item>)."""
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
                it.justificativa_preco = "editado manualmente no painel"
            it.participar = form.get(f"part_{it.numero_item}") is not None
            session.add(it)
        op = session.get(Oportunidade, oid)
        if op.status in (Status.AGUARDANDO_APROVACAO, Status.PREPARADA_PORTAL, Status.DOCS_GERADOS):
            from licitabot.db.session import set_status

            # preços mudaram: volta para regerar documentos e disparar novo e-mail
            set_status(session, op, Status.PRECIFICADA, "preços editados manualmente; documentos serão regerados")
        session.commit()
    return RedirectResponse(url=f"/oportunidades/{oid}", status_code=303)


# ---------- configurações ----------


@app.get("/configuracoes", response_class=HTMLResponse)
def configuracoes_raiz():
    return RedirectResponse(url="/configuracoes/perfil", status_code=303)


@app.get("/configuracoes/{slug}", response_class=HTMLResponse)
def configuracoes(request: Request, slug: str):
    secao = cfg.SECOES_POR_SLUG.get(slug)
    if not secao:
        return HTMLResponse("Seção de configuração não encontrada.", status_code=404)
    extra = {}
    if slug == "perfil":
        with db_session() as session:
            extra["pendentes_pre_triagem"] = session.exec(
                select(func.count()).select_from(Oportunidade)
                .where(Oportunidade.status == Status.DESCOBERTA, Oportunidade.pre_triagem == "")
            ).one()
    return templates.TemplateResponse(request, "configuracoes.html", _ctx(
        request, secao=secao, secoes=cfg.SECOES, campos=cfg.formulario(secao), pagina=f"cfg-{slug}", **extra))


@app.post("/configuracoes/{slug}", response_class=HTMLResponse)
async def configuracoes_salvar(request: Request, slug: str):
    secao = cfg.SECOES_POR_SLUG.get(slug)
    if not secao:
        return HTMLResponse("Seção de configuração não encontrada.", status_code=404)
    form = await request.form()
    erros = cfg.salvar(secao, dict(form))
    if erros:
        # devolve o que a pessoa digitou, para não perder o trabalho
        campos = [(c, form.get(c.chave, "") if c.tipo != "booleano" else form.get(c.chave) is not None) for c in secao.campos]
        return templates.TemplateResponse(request, "configuracoes.html", _ctx(
            request, secao=secao, secoes=cfg.SECOES, campos=campos, erros=erros, pagina=f"cfg-{slug}"), status_code=400)
    log.info("configuração '%s' salva pelo painel", slug)
    return RedirectResponse(url=f"/configuracoes/{slug}?ok=Configura%C3%A7%C3%A3o+salva.", status_code=303)


@app.post("/configuracoes/perfil/pretriar")
def pretriar_agora():
    from licitabot.pipeline.pretriagem import ativa, rodar_em_segundo_plano

    if not ativa():
        return RedirectResponse(url="/configuracoes/perfil?ok=Ligue+a+pr%C3%A9-triagem+e+preencha+o+perfil+antes.", status_code=303)
    if rodar_em_segundo_plano():
        msg = "Pr%C3%A9-triagem+iniciada+em+segundo+plano."
    else:
        msg = "A+pr%C3%A9-triagem+j%C3%A1+est%C3%A1+rodando."
    return RedirectResponse(url=f"/configuracoes/perfil?ok={msg}", status_code=303)


@app.post("/configuracoes/filtros/reaplicar")
def reaplicar_filtros():
    from licitabot.pipeline.discovery import reaplicar_filtro

    n = reaplicar_filtro()
    return RedirectResponse(url=f"/configuracoes/filtros?ok={n}+licita%C3%A7%C3%B5es+descartadas+pelos+filtros+novos.", status_code=303)


@app.post("/configuracoes/notificacoes/testar", response_class=HTMLResponse)
def testar_email(request: Request):
    from licitabot.config import destinatarios
    from licitabot.pipeline.notify import enviar_alerta

    try:
        enviar_alerta("Teste de configuração", "O envio de e-mail está funcionando",
                      ["Este e-mail confirma que as credenciais SMTP e os destinatários estão corretos.",
                       "Enviado a partir do painel do Licita-Bot."])
    except Exception as e:  # noqa: BLE001
        secao = cfg.SECOES_POR_SLUG["notificacoes"]
        return templates.TemplateResponse(request, "configuracoes.html", _ctx(
            request, secao=secao, secoes=cfg.SECOES, campos=cfg.formulario(secao),
            erros=[f"Não foi possível enviar: {e}"], pagina="cfg-notificacoes"), status_code=400)
    quantos = len(destinatarios())
    return RedirectResponse(url=f"/configuracoes/notificacoes?ok=E-mail+de+teste+enviado+para+{quantos}+destinat%C3%A1rio(s).", status_code=303)


# ---------- onboarding ----------


@app.get("/onboarding", response_class=HTMLResponse)
def onboarding(request: Request):
    from licitabot.sicaf.checklist import onboarding_completo

    ok, faltando = onboarding_completo()
    with db_session() as session:
        passos = session.exec(select(OnboardingPasso).order_by(OnboardingPasso.ordem)).all()
        return templates.TemplateResponse(request, "onboarding.html", _ctx(
            request, passos=passos, ok=ok, faltando=faltando, pagina="onboarding"))


@app.post("/onboarding/{chave}")
def onboarding_marcar(chave: str, concluido: str = Form("0")):
    with db_session() as session:
        p = session.exec(select(OnboardingPasso).where(OnboardingPasso.chave == chave)).first()
        if p and not p.verificacao_automatica:
            p.concluido = concluido == "1"
            session.add(p)
            session.commit()
    return RedirectResponse(url="/onboarding", status_code=303)
