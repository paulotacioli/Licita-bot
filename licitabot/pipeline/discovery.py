"""Descoberta: busca no PNCP, dedupe e filtros locais baratos (antes de qualquer IA)."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta

from sqlmodel import select

from licitabot.config import load_precos, load_triagem
from licitabot.db.models import Oportunidade
from licitabot.db.session import db_session, log_evento, set_status
from licitabot.pipeline.states import Status
from licitabot.pncp.client import PNCPClient
from licitabot.pncp.schemas import SearchItem, parse_dt
from licitabot.pncp.search import iter_editais

log = logging.getLogger(__name__)


def _filtro_local(item: SearchItem, agora: datetime) -> str | None:
    """Retorna motivo de descarte ou None se passa."""
    tri = load_triagem()
    precos = load_precos()
    if item.cancelado:
        return "cancelado"
    if item.modalidade_id not in tri.modalidades_aceitas:
        return f"modalidade {item.modalidade_licitacao_nome or item.modalidade_id} não aceita"
    if tri.ufs and item.uf not in tri.ufs:
        return f"UF {item.uf} fora do escopo"
    if tri.esferas and item.esfera_nome not in tri.esferas:
        return f"esfera {item.esfera_nome} fora do escopo"
    prazo = item.prazo_proposta
    if prazo and prazo < agora + timedelta(days=tri.prazo_minimo_dias):
        return f"prazo curto (encerra {prazo:%d/%m/%Y %H:%M})"
    texto = f" {item.title} {item.description} ".lower()
    # Remove o prefixo "[Nome do Portal]" e "sistema de registro de preços" (SRP), que não indicam software
    texto_pos = re.sub(r"\[[^\]]{0,60}\]", " ", texto)
    texto_pos = re.sub(r"sistemas?\s+de\s+registro\s+de\s+pre[çc]os?", " ", texto_pos)
    if tri.palavras_positivas and not any(p.lower() in texto_pos for p in tri.palavras_positivas):
        return "objeto sem palavra-chave de software"
    for neg in tri.palavras_negativas:
        if neg.lower() in texto:
            return f"palavra negativa: {neg}"
    if item.valor_global and precos.valor_minimo_oportunidade and item.valor_global < precos.valor_minimo_oportunidade:
        return f"valor global {item.valor_global:.2f} abaixo do mínimo"
    return None


def _to_oportunidade(item: SearchItem) -> Oportunidade:
    return Oportunidade(
        numero_controle_pncp=item.numero_controle_pncp or f"{item.cnpj}-1-{item.seq_int:06d}/{item.ano_int}",
        orgao_cnpj=item.cnpj,
        ano=item.ano_int,
        sequencial=item.seq_int,
        orgao_nome=item.orgao_nome or "",
        unidade_nome=item.unidade_nome or "",
        esfera=item.esfera_nome or "",
        poder=item.poder_nome or "",
        uf=item.uf or "",
        municipio=item.municipio_nome or "",
        modalidade_id=item.modalidade_id,
        modalidade_nome=item.modalidade_licitacao_nome or "",
        objeto=item.description or item.title or "",
        valor_estimado=item.valor_global,
        situacao_nome=item.situacao_nome or "",
        data_publicacao=parse_dt(item.data_publicacao_pncp),
        data_abertura_proposta=parse_dt(item.data_inicio_vigencia),
        data_encerramento_proposta=item.prazo_proposta,
        raw_search_json=item.model_dump(),
    )


def reaplicar_filtro() -> int:
    """Reaplica o filtro local às oportunidades ainda em DESCOBERTA (útil após mudar triagem.yaml)."""
    agora = datetime.now()
    n = 0
    with db_session() as session:
        ops = session.exec(select(Oportunidade).where(Oportunidade.status == Status.DESCOBERTA)).all()
        for op in ops:
            try:
                item = SearchItem.model_validate(op.raw_search_json)
            except Exception:  # noqa: BLE001
                continue
            motivo = _filtro_local(item, agora)
            if motivo:
                set_status(session, op, Status.DESCARTADA, f"filtro_local: {motivo}")
                n += 1
        session.commit()
    return n


def run_discovery(termos: list[str] | None = None, max_paginas: int | None = None) -> dict[str, int]:
    """Busca todos os termos configurados e grava oportunidades novas. Retorna contadores."""
    tri = load_triagem()
    termos = termos or tri.termos_busca
    max_paginas = max_paginas or tri.max_paginas_por_termo
    agora = datetime.now()
    stats = {"vistos": 0, "novos": 0, "atualizados": 0, "descartados_filtro": 0, "erros": 0}

    with PNCPClient() as client, db_session() as session:
        for termo in termos:
            try:
                for item in iter_editais(client, termo, max_paginas=max_paginas):
                    stats["vistos"] += 1
                    controle = item.numero_controle_pncp
                    existente = None
                    if controle:
                        existente = session.exec(
                            select(Oportunidade).where(Oportunidade.numero_controle_pncp == controle)
                        ).first()
                    if existente:
                        novo_prazo = item.prazo_proposta
                        if novo_prazo and novo_prazo != existente.data_encerramento_proposta:
                            existente.data_encerramento_proposta = novo_prazo
                            existente.situacao_nome = item.situacao_nome or existente.situacao_nome
                            existente.atualizado_em = datetime.utcnow()
                            session.add(existente)
                            stats["atualizados"] += 1
                        continue
                    op = _to_oportunidade(item)
                    motivo = _filtro_local(item, agora)
                    session.add(op)
                    session.flush()
                    if motivo:
                        set_status(session, op, Status.DESCARTADA, f"filtro_local: {motivo}")
                        stats["descartados_filtro"] += 1
                    else:
                        log_evento(session, "descoberta", f"termo='{termo}'", op.id)
                        stats["novos"] += 1
                    session.commit()
            except Exception as e:  # noqa: BLE001
                log.exception("Falha na busca do termo '%s': %s", termo, e)
                stats["erros"] += 1
                session.rollback()
    log.info("Descoberta concluída: %s", stats)
    return stats
