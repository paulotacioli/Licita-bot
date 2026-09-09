"""Regras de decisão de aprovação (usadas pelo endpoint web, pelo polling IMAP e pela CLI)."""

from __future__ import annotations

import logging
from datetime import datetime

from sqlmodel import select

from licitabot.approval.tokens import snapshot_hash, token_hash, validar_token
from licitabot.db.models import Aprovacao, Item, Oportunidade
from licitabot.db.session import db_session, get_oportunidade, log_evento, set_status
from licitabot.pipeline.states import Status

log = logging.getLogger(__name__)


def montar_snapshot(session, op: Oportunidade) -> dict:
    """Congela o que está sendo aprovado: preços por item, hashes dos documentos, prazo."""
    from licitabot.db.models import DocumentoGerado

    itens = session.exec(select(Item).where(Item.oportunidade_id == op.id, Item.participar == True).order_by(Item.numero_item)).all()  # noqa: E712
    docs = session.exec(select(DocumentoGerado).where(DocumentoGerado.oportunidade_id == op.id).order_by(DocumentoGerado.ordem)).all()
    return {
        "oportunidade": op.numero_controle_pncp,
        "prazo": op.data_encerramento_proposta.isoformat() if op.data_encerramento_proposta else None,
        "itens": [{"n": i.numero_item, "unit": i.valor_unit_proposto, "total": i.valor_total_proposto, "marca": i.marca_modelo} for i in itens],
        "documentos": [{"tipo": d.tipo, "hash": d.hash_sha256} for d in docs if d.status in ("gerado", "do_cofre")],
    }


def snapshot_atual_bate(session, op: Oportunidade, ap: Aprovacao) -> bool:
    return snapshot_hash(montar_snapshot(session, op)) == ap.snapshot_hash


def _aplicar_decisao(session, op: Oportunidade, ap: Aprovacao, aprovar: bool, canal: str, ip: str = "") -> tuple[bool, str]:
    if ap.decisao != "pendente":
        return False, f"esta solicitação já foi {ap.decisao}"
    if datetime.now() > ap.expira_em:
        ap.decisao = "expirada"
        session.add(ap)
        set_status(session, op, Status.EXPIRADA, "aprovação expirou")
        return False, "prazo de aprovação expirado"
    if op.status != Status.AGUARDANDO_APROVACAO:
        return False, f"oportunidade não está aguardando aprovação (status {op.status})"
    if aprovar and not snapshot_atual_bate(session, op, ap):
        return False, "os dados (preços/documentos) mudaram desde o e-mail; será enviado um novo e-mail"
    ap.decisao = "aprovada" if aprovar else "rejeitada"
    ap.decidido_em = datetime.now()
    ap.canal = canal
    ap.ip_origem = ip
    session.add(ap)
    if aprovar:
        set_status(session, op, Status.APROVADA, f"aprovada via {canal}")
        msg = "Proposta APROVADA. O envio será feito automaticamente em instantes."
    else:
        set_status(session, op, Status.REJEITADA, f"rejeitada via {canal}")
        msg = "Proposta REJEITADA. Nada será enviado."
    log_evento(session, "aprovacao", msg, op.id, canal=canal, ip=ip)
    return True, msg


def decidir_por_token(token: str, ip: str = "") -> tuple[bool, str]:
    try:
        payload = validar_token(token, max_age_s=72 * 3600)
    except ValueError as e:
        return False, str(e)
    with db_session() as session:
        ap = session.get(Aprovacao, int(payload["a"]))
        op = session.get(Oportunidade, int(payload["o"]))
        if not ap or not op or ap.oportunidade_id != op.id:
            return False, "solicitação não encontrada"
        if ap.token_hash != token_hash(token) or ap.snapshot_hash != payload.get("h"):
            return False, "link não corresponde à solicitação vigente"
        ok, msg = _aplicar_decisao(session, op, ap, aprovar=(payload["acao"] == "aprovar"), canal="link", ip=ip)
        session.commit()
        return ok, msg


def decidir_por_cli(ref: int | str, aprovar: bool) -> tuple[bool, str]:
    with db_session() as session:
        op = get_oportunidade(session, ref)
        if not op:
            return False, "não encontrada"
        ap = session.exec(select(Aprovacao).where(Aprovacao.oportunidade_id == op.id, Aprovacao.decisao == "pendente").order_by(Aprovacao.id.desc())).first()
        if not ap:
            return False, "sem solicitação pendente"
        ok, msg = _aplicar_decisao(session, op, ap, aprovar=aprovar, canal="cli")
        session.commit()
        return ok, msg


def decidir_por_email(oportunidade_id: int, aprovar: bool, remetente: str) -> tuple[bool, str]:
    with db_session() as session:
        op = session.get(Oportunidade, oportunidade_id)
        if not op:
            return False, "não encontrada"
        ap = session.exec(select(Aprovacao).where(Aprovacao.oportunidade_id == op.id, Aprovacao.decisao == "pendente").order_by(Aprovacao.id.desc())).first()
        if not ap:
            return False, "sem solicitação pendente"
        ok, msg = _aplicar_decisao(session, op, ap, aprovar=aprovar, canal="email_reply", ip=remetente)
        session.commit()
        return ok, msg


def descricao_token(token: str) -> dict | None:
    """Para a página GET de confirmação: mostra o que será decidido sem decidir."""
    try:
        payload = validar_token(token, max_age_s=72 * 3600)
    except ValueError:
        return None
    with db_session() as session:
        op = session.get(Oportunidade, int(payload["o"]))
        ap = session.get(Aprovacao, int(payload["a"]))
        if not op or not ap:
            return None
        return {
            "acao": payload["acao"],
            "orgao": op.orgao_nome,
            "objeto": op.objeto[:300],
            "prazo": op.data_encerramento_proposta,
            "decisao_atual": ap.decisao,
            "itens": ap.snapshot_json.get("itens", []),
            "total": sum((i.get("total") or 0) for i in ap.snapshot_json.get("itens", [])),
        }
