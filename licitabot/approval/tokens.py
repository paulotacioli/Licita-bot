"""Tokens de aprovação: HMAC assinado (itsdangerous), com expiração, ação e hash do snapshot aprovado."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from licitabot.config import get_settings


def _serializer() -> URLSafeTimedSerializer:
    s = get_settings()
    if not s.approval_secret or len(s.approval_secret) < 32:
        raise RuntimeError("APPROVAL_SECRET ausente ou curto demais (mínimo 32 caracteres) no .env")
    return URLSafeTimedSerializer(s.approval_secret, salt="licitabot-aprovacao-v1")


def snapshot_hash(snapshot: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def gerar_token(oportunidade_id: int, aprovacao_id: int, acao: str, snap_hash: str) -> str:
    return _serializer().dumps({"o": oportunidade_id, "a": aprovacao_id, "acao": acao, "h": snap_hash})


def validar_token(token: str, max_age_s: int) -> dict[str, Any]:
    """Levanta ValueError com mensagem amigável se inválido/expirado."""
    try:
        return _serializer().loads(token, max_age=max_age_s)
    except SignatureExpired as e:
        raise ValueError("link expirado") from e
    except BadSignature as e:
        raise ValueError("link inválido") from e


def expiracao_para(prazo: datetime | None, horas_max: int = 48) -> datetime:
    base = datetime.now() + timedelta(hours=horas_max)
    if prazo:
        base = min(base, prazo - timedelta(hours=2))
    return base
