"""Pós-envio: no dia da sessão, lê a situação dos itens/chat a cada ciclo e alerta o humano (lances são manuais na v1)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlmodel import select

from licitabot.db.models import Oportunidade, SessaoPregao
from licitabot.db.session import db_session, log_evento, set_status
from licitabot.pipeline.states import Status

log = logging.getLogger(__name__)


def monitor(oportunidade_id: int) -> None:
    from licitabot.portal.comprasgov.monitor import ler_situacao
    from licitabot.pipeline.notify import enviar_alerta

    with db_session() as session:
        op = session.get(Oportunidade, oportunidade_id)
        session.expunge(op)
    dados = ler_situacao(op)
    with db_session() as session:
        op = session.get(Oportunidade, oportunidade_id)
        anteriores = {s.item_numero: s for s in session.exec(select(SessaoPregao).where(SessaoPregao.oportunidade_id == op.id))}
        novidades: list[str] = []
        for it in dados.get("itens", []):
            reg = anteriores.get(it["item"]) or SessaoPregao(oportunidade_id=op.id, item_numero=it["item"])
            if reg.situacao != it["situacao"]:
                novidades.append(f"Item {it['item']}: {it['situacao']}")
            reg.situacao = it["situacao"]
            reg.ultima_leitura_em = datetime.utcnow()
            reg.mensagens_chat = [{"t": m} for m in dados.get("mensagens", [])][:40]
            session.add(reg)
        for a in dados.get("alertas", []):
            novidades.append(f"Chat: {a}")
        if dados.get("erro"):
            log_evento(session, "monitor", f"erro: {dados['erro']}", op.id)
        if novidades:
            enviar_alerta("Novidades na sessão pública", "Sessão pública: atualizações", novidades + ["Se houver convocação para lances ou documentos, atue no navegador (perfil já logado)."], op)
            log_evento(session, "monitor", "; ".join(novidades)[:2000], op.id)
        if op.status == Status.ENVIADA and op.data_sessao and datetime.now() >= op.data_sessao:
            set_status(session, op, Status.EM_SESSAO, "sessão pública iniciada")
        if any(s in ("homologado", "homologada", "adjudicado", "adjudicada", "cancelado", "cancelada") for s in (i["situacao"] for i in dados.get("itens", []))):
            set_status(session, op, Status.ENCERRADA, "certame encerrado no portal")
        session.commit()


def monitorar_enviadas() -> int:
    """Roda para oportunidades ENVIADA/EM_SESSAO cuja sessão é hoje (ou já começou e não encerrou)."""
    agora = datetime.now()
    n = 0
    with db_session() as session:
        ops = session.exec(select(Oportunidade).where(Oportunidade.status.in_([Status.ENVIADA, Status.EM_SESSAO]))).all()
        alvo = [o.id for o in ops if o.data_sessao and (o.data_sessao - timedelta(hours=1)) <= agora <= (o.data_sessao + timedelta(days=3))]
    for oid in alvo:
        try:
            monitor(oid)
            n += 1
        except Exception as e:  # noqa: BLE001
            log.error("monitor %s falhou: %s", oid, e)
    return n
