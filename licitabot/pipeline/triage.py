"""Triagem de relevância com Claude (modelo rápido) + regras de corte."""

from __future__ import annotations

import logging
from pathlib import Path

from sqlmodel import select

from licitabot.config import get_settings, load_empresa, load_triagem
from licitabot.db.models import ArquivoEdital, Item, Oportunidade, Triagem
from licitabot.db.session import db_session, set_status
from licitabot.llm.client import get_llm_client, text_block
from licitabot.llm.prompts import render_prompt
from licitabot.llm.schemas import TriagemResult
from licitabot.pipeline.states import Status

log = logging.getLogger(__name__)

TRECHO_MAX = 12000


def empresa_resumo() -> str:
    e = load_empresa()
    partes = [e.descricao_empresa.strip()]
    if e.servicos:
        partes.append("Serviços: " + "; ".join(e.servicos))
    if e.produtos_saas:
        partes.append("Produtos SaaS: " + "; ".join(f"{p.nome} ({p.descricao})" for p in e.produtos_saas))
    partes.append(f"Porte: {e.porte}. UF sede: {e.uf or '?'}.")
    return "\n".join(p for p in partes if p)


def _trecho_inicial(session, op: Oportunidade) -> str:
    regs = list(session.exec(select(ArquivoEdital).where(ArquivoEdital.oportunidade_id == op.id)))
    regs = [r for r in regs if r.texto_extraido and r.caminho_texto]
    ordem = {"edital": 0, "termo_referencia": 1}
    regs.sort(key=lambda r: (ordem.get(r.papel, 5), -r.paginas))
    out: list[str] = []
    budget = TRECHO_MAX
    for r in regs[:2]:
        t = Path(r.caminho_texto).read_text(encoding="utf-8", errors="ignore")
        parte = t[: budget // (2 if len(regs) > 1 else 1)]
        out.append(f"--- {r.titulo} ---\n{parte}")
        budget -= len(parte)
        if budget <= 0:
            break
    return "\n".join(out)


def triage(oportunidade_id: int, client=None) -> TriagemResult:
    s = get_settings()
    tri = load_triagem()
    client = client or get_llm_client()
    with db_session() as session:
        op = session.get(Oportunidade, oportunidade_id)
        if not op:
            raise ValueError(f"Oportunidade {oportunidade_id} não existe")
        itens = list(session.exec(select(Item).where(Item.oportunidade_id == op.id).order_by(Item.numero_item)))
        prompt = render_prompt(
            "triagem",
            empresa_resumo=empresa_resumo(),
            perfil=tri.perfil,
            op=op,
            itens=itens[:40],
            valor_estimado=f"R$ {op.valor_estimado:,.2f}" if op.valor_estimado else "não informado",
            prazo=op.data_encerramento_proposta.strftime("%d/%m/%Y %H:%M") if op.data_encerramento_proposta else "?",
            trecho=_trecho_inicial(session, op),
        )
        result, meta = client.structured(s.llm_model_triagem, TriagemResult, [text_block(prompt)], effort="low", max_tokens=2000)
        session.add(
            Triagem(
                oportunidade_id=op.id,
                relevante=result.relevante,
                score=result.score,
                categoria=result.categoria,
                justificativa=result.justificativa,
                exige_presenca_fisica=result.exige_presenca_fisica,
                exige_hardware=result.exige_hardware,
                exige_atestado_especifico=result.exige_atestado_especifico,
                modelo_llm=meta.model,
                tokens_in=meta.tokens_in,
                tokens_out=meta.tokens_out,
            )
        )
        aceita = result.score >= tri.score_minimo and result.categoria in tri.categorias_aceitas
        if aceita:
            set_status(session, op, Status.TRIADA_RELEVANTE, f"score {result.score} / {result.categoria}")
        elif result.score >= tri.score_zona_cinzenta and result.categoria in tri.categorias_aceitas + ["outsourcing"]:
            set_status(session, op, Status.ZONA_CINZENTA, f"score {result.score} / {result.categoria}: {result.justificativa[:300]}")
        else:
            set_status(session, op, Status.DESCARTADA, f"triagem: score {result.score} / {result.categoria}: {result.justificativa[:300]}")
        session.commit()
        return result
