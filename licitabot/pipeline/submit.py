"""Envio após aprovação: preflight (prazo, snapshot, hashes, certidões) e chamada do portal."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path

from sqlmodel import select

from licitabot.config import get_settings
from licitabot.db.models import Aprovacao, DocumentoGerado, Oportunidade
from licitabot.db.session import db_session, log_evento, set_status
from licitabot.pipeline.states import Status
from licitabot.pncp.files import sha256_of

log = logging.getLogger(__name__)


def preflight(session, op: Oportunidade) -> tuple[Aprovacao | None, list[str]]:
    s = get_settings()
    problemas: list[str] = []
    ap = session.exec(select(Aprovacao).where(Aprovacao.oportunidade_id == op.id, Aprovacao.decisao == "aprovada").order_by(Aprovacao.id.desc())).first()
    if not ap:
        problemas.append("sem aprovação registrada")
        return None, problemas
    if op.status != Status.APROVADA:
        problemas.append(f"status {op.status} != APROVADA")
    agora = datetime.now()
    if not op.data_encerramento_proposta:
        problemas.append("sem prazo de encerramento conhecido")
    elif op.data_encerramento_proposta < agora + timedelta(minutes=s.prazo_min_enviar_min):
        problemas.append(f"menos de {s.prazo_min_enviar_min} min para o encerramento ({op.data_encerramento_proposta:%d/%m %H:%M})")
    from licitabot.approval.service import snapshot_atual_bate

    if not snapshot_atual_bate(session, op, ap):
        problemas.append("preços/documentos mudaram após a aprovação")
    docs = session.exec(select(DocumentoGerado).where(DocumentoGerado.oportunidade_id == op.id)).all()
    for d in docs:
        if d.status == "PRECISA_ASSINATURA":
            assinado = Path(op.pasta) / "proposta" / "assinados" / Path(d.caminho_pdf).name
            if assinado.exists():
                d.caminho_pdf = str(assinado)
                d.hash_sha256 = sha256_of(assinado)
                d.status = "gerado"
                session.add(d)
            else:
                problemas.append(f"{d.tipo} precisa de assinatura digital (salvar em proposta/assinados/)")
        elif d.status in ("gerado", "do_cofre"):
            p = Path(d.caminho_pdf or d.caminho_docx)
            if not p.exists():
                problemas.append(f"{d.tipo}: arquivo sumiu ({p.name})")
            elif d.hash_sha256 and sha256_of(p) != d.hash_sha256:
                problemas.append(f"{d.tipo}: arquivo alterado após a aprovação")
        if d.origem == "cofre" and d.status == "do_cofre":
            from licitabot.db.models import DocumentoCofre

            dc = session.exec(select(DocumentoCofre).where(DocumentoCofre.hash_sha256 == d.hash_sha256)).first()
            if dc and dc.valido_ate and dc.valido_ate < (op.data_sessao or op.data_encerramento_proposta or agora):
                problemas.append(f"{d.tipo}: certidão vence antes da sessão ({dc.valido_ate:%d/%m/%Y})")
    if op.portal != "comprasgov":
        problemas.append(f"portal {op.portal} não suportado")
    return ap, problemas


def submit(oportunidade_id: int) -> None:
    s = get_settings()
    with db_session() as session:
        op = session.get(Oportunidade, oportunidade_id)
        ap, problemas = preflight(session, op)
        if problemas:
            set_status(session, op, Status.ERRO, "preflight: " + "; ".join(problemas)[:800])
            session.commit()
            _alerta(op, "Envio bloqueado no preflight", problemas)
            return
        ap_id = ap.id
        session.commit()
    from licitabot.portal.comprasgov.envio import submit_no_portal

    try:
        res = submit_no_portal(oportunidade_id, ap_id)
    except Exception as e:  # noqa: BLE001
        with db_session() as session:
            op = session.get(Oportunidade, oportunidade_id)
            set_status(session, op, Status.ERRO, f"envio falhou: {e}"[:800], erro=str(e))
            session.commit()
            _alerta(op, "Envio FALHOU", [str(e), "Nenhum reenvio automático será feito. Verifique screenshots e o portal."])
        raise
    with db_session() as session:
        op = session.get(Oportunidade, oportunidade_id)
        if res.get("enviada"):
            set_status(session, op, Status.ENVIADA, f"proposta enviada em {datetime.now():%d/%m %H:%M}; comprovante: {res.get('comprovante', '')}")
            anexos = [Path(res["comprovante"])] if res.get("comprovante") and Path(res["comprovante"]).exists() else []
            _alerta(op, "Proposta ENVIADA", [f"Enviada em {datetime.now():%d/%m/%Y %H:%M}.", f"Sessão pública: {op.data_sessao or 'ver edital'}", "Acompanhe os lances no dia da sessão; o sistema avisará sobre convocações."], anexos)
        elif res.get("dry_run"):
            set_status(session, op, Status.ENVIADA, "DRY_RUN: envio simulado (parou antes do clique). Ajuste DRY_RUN=false para envios reais.")
            _alerta(op, "DRY-RUN concluído (nada enviado)", ["O preenchimento e a verificação de tela passaram.", "Nada foi enviado porque DRY_RUN=true."])
        log_evento(session, "envio", str(res)[:2000], op.id)
        session.commit()


def enviar_aprovadas() -> int:
    n = 0
    with db_session() as session:
        ids = list(session.exec(select(Oportunidade.id).where(Oportunidade.status == Status.APROVADA)).all())
    for oid in ids:
        try:
            submit(oid)
            n += 1
        except Exception as e:  # noqa: BLE001
            log.error("envio %s falhou: %s", oid, e)
    return n


def _alerta(op: Oportunidade, assunto: str, linhas: list[str], anexos: list[Path] | None = None) -> None:
    try:
        from licitabot.pipeline.notify import enviar_alerta

        enviar_alerta(assunto, assunto, linhas, op, anexos)
    except Exception as e:  # noqa: BLE001
        log.warning("alerta não enviado: %s", e)
