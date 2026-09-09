"""Autenticação do painel: usuário/senha no banco, sessão em cookie assinado.

A senha nunca é gravada em texto: guardamos um hash PBKDF2-SHA256 com sal aleatório.
No primeiro acesso não existe usuário, e o painel pede para criar um em /primeiro-acesso.
Os endpoints de aprovação (/aprovar, /rejeitar, /decidir) ficam fora do login de propósito:
são abertos a partir do e-mail e a proteção deles é o token assinado, de uso único.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from datetime import datetime

from fastapi import Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlmodel import select

from licitabot.config import get_settings
from licitabot.db.models import UsuarioPainel
from licitabot.db.session import db_session

log = logging.getLogger(__name__)

COOKIE = "licitabot_sessao"
DURACAO_SESSAO_S = 7 * 24 * 3600
ITERACOES = 240_000
# Prefixos liberados sem login. Tudo o mais exige sessão.
ROTAS_PUBLICAS = ("/aprovar/", "/rejeitar/", "/decidir", "/login", "/primeiro-acesso", "/healthz", "/favicon.ico")


# ---------- senha ----------


def gerar_hash(senha: str) -> str:
    sal = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", senha.encode(), bytes.fromhex(sal), ITERACOES)
    return f"pbkdf2_sha256${ITERACOES}${sal}${dk.hex()}"


def conferir_senha(senha: str, hash_guardado: str) -> bool:
    try:
        algo, it, sal, esperado = hash_guardado.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", senha.encode(), bytes.fromhex(sal), int(it))
    except (ValueError, AttributeError):
        return False
    return hmac.compare_digest(dk.hex(), esperado)


def forca_da_senha(senha: str) -> str | None:
    """Retorna o motivo da recusa, ou None se a senha serve."""
    if len(senha) < 10:
        return "A senha precisa de pelo menos 10 caracteres."
    if senha.isdigit() or senha.isalpha():
        return "Misture letras e números."
    return None


# ---------- usuários ----------


def existe_usuario() -> bool:
    with db_session() as session:
        return session.exec(select(UsuarioPainel).limit(1)).first() is not None


def criar_usuario(usuario: str, senha: str) -> None:
    with db_session() as session:
        session.add(UsuarioPainel(usuario=usuario.strip().lower(), senha_hash=gerar_hash(senha)))
        session.commit()


def autenticar(usuario: str, senha: str) -> UsuarioPainel | None:
    with db_session() as session:
        u = session.exec(select(UsuarioPainel).where(UsuarioPainel.usuario == usuario.strip().lower())).first()
        if not u or not conferir_senha(senha, u.senha_hash):
            return None
        u.ultimo_acesso_em = datetime.now()
        session.add(u)
        session.commit()
        session.refresh(u)
        session.expunge(u)
        return u


def trocar_senha(usuario_id: int, senha_atual: str, senha_nova: str) -> str | None:
    """Retorna mensagem de erro, ou None em caso de sucesso."""
    with db_session() as session:
        u = session.get(UsuarioPainel, usuario_id)
        if not u or not conferir_senha(senha_atual, u.senha_hash):
            return "Senha atual incorreta."
        motivo = forca_da_senha(senha_nova)
        if motivo:
            return motivo
        u.senha_hash = gerar_hash(senha_nova)
        session.add(u)
        session.commit()
    return None


# ---------- sessão ----------


def _serializer() -> URLSafeTimedSerializer:
    s = get_settings()
    if len(s.approval_secret) < 32:
        raise RuntimeError("APPROVAL_SECRET ausente ou curto no .env (mínimo 32 caracteres)")
    return URLSafeTimedSerializer(s.approval_secret, salt="painel-sessao")


def criar_sessao(usuario: UsuarioPainel) -> str:
    return _serializer().dumps({"uid": usuario.id, "u": usuario.usuario})


def ler_sessao(request: Request) -> dict | None:
    raw = request.cookies.get(COOKIE)
    if not raw:
        return None
    try:
        return _serializer().loads(raw, max_age=DURACAO_SESSAO_S)
    except (BadSignature, SignatureExpired):
        return None


def rota_publica(caminho: str) -> bool:
    return any(caminho == p or caminho.startswith(p) for p in ROTAS_PUBLICAS)
