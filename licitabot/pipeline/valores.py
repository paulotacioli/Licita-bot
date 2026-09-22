"""Valor estimado cedo, sem baixar o edital.

A busca do PNCP quase nunca traz o valor: ele só aparece no detalhe da compra e nos itens. Antes,
o valor só chegava no `ingest`, que baixa todos os PDFs — caro demais para rodar em tudo que a
descoberta encontra. Aqui fazemos uma chamada de JSON por licitação (duas, se o detalhe vier sem
valor) só para as que interessam, para que o e-mail diário já mostre quanto vale cada uma.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlmodel import or_, select

from licitabot.db.models import Oportunidade
from licitabot.db.session import db_session
from licitabot.pipeline.states import Status
from licitabot.pncp.client import PNCPClient

from licitabot.pncp.schemas import CompraDetalhe, parse_dt

log = logging.getLogger(__name__)


# O edital pode ser retificado e ganhar valor depois; por isso reconsultamos, mas só uma vez por dia.
INTERVALO_RECONSULTA_H = 24


def pendentes(session, limite: int) -> list[Oportunidade]:
    """Licitações que interessam, ainda sem valor, ainda não baixadas e não consultadas hoje."""
    corte = datetime.now() - timedelta(hours=INTERVALO_RECONSULTA_H)
    return list(
        session.exec(
            select(Oportunidade)
            .where(
                Oportunidade.valor_estimado == None,  # noqa: E711
                Oportunidade.status == Status.DESCOBERTA,
                or_(Oportunidade.valor_consultado_em == None, Oportunidade.valor_consultado_em < corte),  # noqa: E711
                or_(Oportunidade.pre_triagem == "relevante", Oportunidade.pre_triagem == "incerto"),
            )
            .order_by(Oportunidade.data_encerramento_proposta)
            .limit(limite)
        ).all()
    )


def completar(limite: int = 300) -> dict[str, int]:
    """Preenche valor (e o que mais vier de graça no detalhe) das licitações relevantes sem valor."""
    stats = {"consultadas": 0, "com_valor": 0, "sem_valor_publicado": 0, "erros": 0}
    with db_session() as session:
        alvos = [(o.id, o.orgao_cnpj, o.ano, o.sequencial) for o in pendentes(session, limite)]
    if not alvos:
        return stats
    with PNCPClient() as client:
        for oid, cnpj, ano, seq in alvos:
            stats["consultadas"] += 1
            valor = None
            det = None
            try:
                det = CompraDetalhe.model_validate(client.compra_detalhe(cnpj, ano, seq))
                valor = det.valorTotalEstimado
            except Exception as e:  # noqa: BLE001
                log.debug("detalhe indisponível para %s/%s/%s: %s", cnpj, ano, seq, e)
                stats["erros"] += 1
            if not valor:
                try:  # alguns órgãos deixam o total em branco e só preenchem os itens
                    itens = client.compra_itens(cnpj, ano, seq)
                    valor = sum((r.get("valorTotal") or 0) for r in itens) or None
                except Exception as e:  # noqa: BLE001
                    log.debug("itens indisponíveis para %s/%s/%s: %s", cnpj, ano, seq, e)
            with db_session() as session:
                op = session.get(Oportunidade, oid)
                if not op:
                    continue
                op.valor_consultado_em = datetime.now()
                if valor:
                    op.valor_estimado = valor
                    stats["com_valor"] += 1
                else:
                    stats["sem_valor_publicado"] += 1
                if det:  # o detalhe corrige prazo e link do portal, que a busca às vezes erra
                    op.data_encerramento_proposta = parse_dt(det.dataEncerramentoProposta) or op.data_encerramento_proposta
                    op.data_abertura_proposta = parse_dt(det.dataAberturaProposta) or op.data_abertura_proposta
                    op.modo_disputa = det.modoDisputaNome or op.modo_disputa
                    op.srp = bool(det.srp) or op.srp
                    op.numero_compra = det.numeroCompra or op.numero_compra
                    if det.linkSistemaOrigem:
                        from licitabot.pipeline.ingest import detectar_portal

                        op.link_sistema_origem = det.linkSistemaOrigem
                        op.portal = detectar_portal(det.linkSistemaOrigem)
                    if det.objetoCompra and len(det.objetoCompra) > len(op.objeto or ""):
                        op.objeto = det.objetoCompra  # a busca trunca o objeto; o detalhe traz inteiro
                session.add(op)
                session.commit()
    log.info("valores completados: %s", stats)
    return stats
