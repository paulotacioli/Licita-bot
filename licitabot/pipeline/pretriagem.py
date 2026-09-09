"""Pré-triagem por IA a partir do objeto do edital, antes de baixar qualquer PDF.

Substitui a exigência de palavra-chave positiva por uma leitura do perfil escrito em linguagem
natural (config/triagem.yaml, chave `perfil`). Roda em lote com o modelo mais barato: cada
chamada julga dezenas de objetos de uma vez. O que sair como `irrelevante` é descartado com o
motivo registrado; `relevante` e `incerto` seguem para a ingestão e para a triagem completa,
que aí sim lê o edital.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime

from sqlmodel import select

from licitabot.config import get_settings, load_triagem
from licitabot.db.models import Oportunidade
from licitabot.db.session import db_session, log_evento, set_status
from licitabot.llm.client import get_llm_client, text_block
from licitabot.llm.prompts import render_prompt
from licitabot.llm.schemas import PreTriagemLote
from licitabot.pipeline.states import Status

log = logging.getLogger(__name__)

OBJETO_MAX = 700
_em_execucao = threading.Lock()


def ativa() -> bool:
    perfil = load_triagem().perfil
    return perfil.pre_triagem_ia and perfil.ativo


def pendentes(session, limite: int) -> list[Oportunidade]:
    q = (
        select(Oportunidade)
        .where(Oportunidade.status == Status.DESCOBERTA, Oportunidade.pre_triagem == "")
        .order_by(Oportunidade.data_encerramento_proposta)
        .limit(limite)
    )
    return list(session.exec(q).all())


def _entrada(op: Oportunidade) -> dict:
    objeto = " ".join((op.objeto or "").split())
    if len(objeto) > OBJETO_MAX:
        objeto = objeto[:OBJETO_MAX] + " […]"
    return {
        "ref": op.id,
        "orgao": op.orgao_nome or "?",
        "uf": op.uf or "?",
        "modalidade": op.modalidade_nome or "?",
        "valor": f"R$ {op.valor_estimado:,.0f}" if op.valor_estimado else "não informado",
        "objeto": objeto or "(sem objeto)",
    }


def classificar_lote(ops: list[Oportunidade], client=None) -> dict[int, tuple[str, str]]:
    """Chama a IA para um lote. Retorna {id: (veredito, motivo)} só para os itens que ela respondeu."""
    from licitabot.pipeline.triage import empresa_resumo

    s = get_settings()
    tri = load_triagem()
    client = client or get_llm_client()
    prompt = render_prompt("pretriagem", empresa_resumo=empresa_resumo(), perfil=tri.perfil, itens=[_entrada(o) for o in ops])
    lote, meta = client.structured(s.llm_model_pretriagem, PreTriagemLote, [text_block(prompt)], effort="low", max_tokens=6000)
    validos = {o.id for o in ops}
    saida: dict[int, tuple[str, str]] = {}
    for it in lote.itens:
        if it.ref in validos and it.ref not in saida:
            saida[it.ref] = (it.veredito, " ".join(it.motivo.split())[:400])
    faltaram = validos - saida.keys()
    if faltaram:
        log.warning("pré-triagem: modelo não respondeu %d de %d itens (ficam para a próxima rodada)", len(faltaram), len(ops))
    log.info("pré-triagem: lote de %d, %d respondidos, %s tokens", len(ops), len(saida), meta.tokens_in + meta.tokens_out)
    return saida


def aplicar(session, op: Oportunidade, veredito: str, motivo: str) -> None:
    op.pre_triagem = veredito
    op.pre_triagem_motivo = motivo
    op.atualizado_em = datetime.utcnow()
    session.add(op)
    if veredito == "irrelevante":
        set_status(session, op, Status.DESCARTADA, f"pré-triagem IA: {motivo}")
    else:
        log_evento(session, "pre_triagem", f"{veredito}: {motivo}", op.id, veredito=veredito)


def pretriar_pendentes(limite: int | None = None, client=None) -> dict[str, int]:
    """Classifica as oportunidades em DESCOBERTA que ainda não passaram pela IA. Idempotente e reentrante."""
    stats = {"avaliadas": 0, "relevantes": 0, "incertas": 0, "descartadas": 0, "sem_resposta": 0, "lotes_com_erro": 0}
    if not ativa():
        return stats
    if not _em_execucao.acquire(blocking=False):
        log.info("pré-triagem já em execução; pulando")
        return stats
    try:
        perfil = load_triagem().perfil
        limite = limite or perfil.maximo_por_rodada
        tamanho = max(1, min(perfil.lote_pre_triagem, 60))
        with db_session() as session:
            ids = [o.id for o in pendentes(session, limite)]
        for inicio in range(0, len(ids), tamanho):
            fatia = ids[inicio : inicio + tamanho]
            with db_session() as session:
                ops = [o for o in (session.get(Oportunidade, i) for i in fatia) if o and o.status == Status.DESCOBERTA]
                if not ops:
                    continue
                try:
                    resultado = classificar_lote(ops, client)
                except Exception as e:  # noqa: BLE001
                    stats["lotes_com_erro"] += 1
                    log.exception("pré-triagem: lote falhou: %s", e)
                    continue
                for op in ops:
                    if op.id not in resultado:
                        stats["sem_resposta"] += 1
                        continue
                    veredito, motivo = resultado[op.id]
                    aplicar(session, op, veredito, motivo)
                    stats["avaliadas"] += 1
                    stats["relevantes" if veredito == "relevante" else "incertas" if veredito == "incerto" else "descartadas"] += 1
                session.commit()
        log.info("pré-triagem concluída: %s", stats)
        return stats
    finally:
        _em_execucao.release()


def rodar_em_segundo_plano() -> bool:
    """Para o botão do painel: dispara numa thread e volta na hora. False se já estava rodando."""
    if _em_execucao.locked():
        return False
    threading.Thread(target=pretriar_pendentes, name="pretriagem", daemon=True).start()
    return True
