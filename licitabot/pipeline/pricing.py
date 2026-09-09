"""Precificação por regras (sem LLM). A IA só sugere horas; piso/teto/margem vêm de config/precos.yaml."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from sqlmodel import select

from licitabot.config import load_empresa, load_precos
from licitabot.db.models import Item, Oportunidade
from licitabot.db.session import db_session, set_status
from licitabot.llm.schemas import EditalRequisitos
from licitabot.pipeline.analysis import get_requisitos
from licitabot.pipeline.states import Status

log = logging.getLogger(__name__)


@dataclass
class ResultadoItem:
    numero_item: int
    participar: bool
    valor_unitario: float | None
    valor_total: float | None
    justificativa: str


def _round2(v: float) -> float:
    return float(f"{v:.2f}")


def _meses_de(texto: str, unidade: str, quantidade: float) -> float:
    u = f"{unidade} {texto}".lower()
    if "mês" in u or "mes" in u or "mensal" in u:
        return quantidade or 12
    if "ano" in u or "anual" in u:
        return (quantidade or 1) * 12
    m = re.search(r"(\d+)\s*meses", u)
    if m:
        return float(m.group(1))
    return 12.0


def _usuarios_de(texto: str) -> int | None:
    m = re.search(r"(\d+)\s*(usu[aá]rios|licen[cç]as|acessos)", texto.lower())
    return int(m.group(1)) if m else None


def precificar_item(it: Item, req: EditalRequisitos | None, exclusivo_me_epp_ok: bool) -> ResultadoItem:
    p = load_precos()
    qtd = it.quantidade or 1
    est_unit = it.valor_unit_estimado
    est_total = it.valor_total_estimado or (est_unit * qtd if est_unit else None)

    # Benefício exclusivo ME/EPP com porte incompatível
    if "exclusiv" in (it.tipo_beneficio or "").lower() and not exclusivo_me_epp_ok:
        return ResultadoItem(it.numero_item, False, None, None, "item exclusivo ME/EPP e a empresa não é ME/EPP")

    tipo = it.tipo_precificacao or "outro"
    texto = f"{it.descricao} {it.unidade}"
    custo_total: float
    memoria: str

    if tipo == "saas":
        meses = _meses_de(it.descricao, it.unidade, qtd)
        usuarios = _usuarios_de(texto)
        if usuarios:
            mensal = max(usuarios * p.preco_saas_usuario_mes, p.preco_saas_minimo_mensal)
        else:
            mensal = p.preco_saas_minimo_mensal
        custo_total = mensal * meses
        memoria = f"SaaS: {usuarios or '?'} usuários x R$ {p.preco_saas_usuario_mes} x {meses:.0f} meses (mín. mensal R$ {p.preco_saas_minimo_mensal})"
    elif tipo in ("desenvolvimento", "manutencao"):
        unidade = f"{it.unidade} {it.descricao[:80]}".lower()
        if re.search(r"ponto.?s?.?de.?fun|\bpf\b", unidade):
            custo_total = qtd * p.preco_ponto_funcao
            memoria = f"{tipo}: {qtd:g} PF x R$ {p.preco_ponto_funcao:.2f}/PF"
        elif re.search(r"\bust\b|unidade.?de.?servi", unidade):
            custo_total = qtd * p.preco_ust
            memoria = f"{tipo}: {qtd:g} UST x R$ {p.preco_ust:.2f}/UST"
        else:
            horas = it.horas_estimadas or p.horas_padrao_por_item_sem_estimativa
            if re.search(r"\bhora|\bhst\b|\bh\b", unidade):
                horas = qtd  # a quantidade já é o esforço
            custo_total = horas * p.valor_hora_padrao
            memoria = f"{tipo}: {horas:.0f} h x R$ {p.valor_hora_padrao:.2f}/h"
    else:
        if not est_total:
            return ResultadoItem(it.numero_item, False, None, None, "tipo de item não precificável e sem valor estimado")
        custo_total = est_total * 0.7
        memoria = "item genérico: 70% do estimado como custo base"

    preco_base = custo_total * (1 + p.margem_minima) / (1 - p.fator_impostos)
    preco_total = preco_base
    memoria += f"; base R$ {preco_base:,.2f} (margem {p.margem_minima:.0%}, impostos {p.fator_impostos:.0%})"

    # Preço justo: referência é o estimado do órgão (pesquisa oficial de preços)
    if est_total:
        piso = est_total * (1 - p.desconto_maximo_vs_estimado)
        alvo = est_total * (1 - p.desconto_alvo_vs_estimado)
        teto = est_total
        if preco_base > teto:
            return ResultadoItem(
                it.numero_item, False, None, None, f"inexequível: custo+margem R$ {preco_base:,.2f} acima do estimado R$ {teto:,.2f} ({memoria})"
            )
        if preco_base < alvo:
            preco_total = alvo
            memoria += f"; ajustado ao preço justo de {100 * (1 - p.desconto_alvo_vs_estimado):.0f}% do estimado (R$ {alvo:,.2f})"
        if preco_total < piso:
            preco_total = piso
            memoria += f"; elevado ao piso de {100 * (1 - p.desconto_maximo_vs_estimado):.0f}% do estimado"
    if preco_total > p.valor_maximo_proposta:
        return ResultadoItem(it.numero_item, False, None, None, f"acima do teto absoluto R$ {p.valor_maximo_proposta:,.2f}")

    unit = _round2(preco_total / qtd) if qtd else _round2(preco_total)
    total = _round2(unit * qtd)
    if unit <= 0:
        return ResultadoItem(it.numero_item, False, None, None, "preço unitário zero/negativo")
    return ResultadoItem(it.numero_item, True, unit, total, memoria)


def price(oportunidade_id: int) -> list[ResultadoItem]:
    emp = load_empresa()
    p = load_precos()
    with db_session() as session:
        op = session.get(Oportunidade, oportunidade_id)
        if not op:
            raise ValueError(f"Oportunidade {oportunidade_id} não existe")
        req = get_requisitos(session, op)
        itens = list(session.exec(select(Item).where(Item.oportunidade_id == op.id).order_by(Item.numero_item)))
        me_epp_ok = emp.porte in ("ME", "EPP")
        if req and req.beneficio_me_epp == "exclusivo" and not me_epp_ok:
            set_status(session, op, Status.DESCARTADA, "licitação exclusiva ME/EPP e a empresa não é ME/EPP")
            session.commit()
            return []
        resultados: list[ResultadoItem] = []
        total_proposta = 0.0
        for it in itens:
            r = precificar_item(it, req, me_epp_ok)
            it.participar = r.participar
            it.valor_unit_proposto = r.valor_unitario
            it.valor_total_proposto = r.valor_total
            it.justificativa_preco = r.justificativa
            if not it.marca_modelo:
                it.marca_modelo = "Serviço próprio" if it.tipo_precificacao != "saas" else (emp.produtos_saas[0].nome if emp.produtos_saas else "Solução própria")
            session.add(it)
            resultados.append(r)
            total_proposta += r.valor_total or 0
        viaveis = [r for r in resultados if r.participar]
        inviaveis = [r for r in resultados if not r.participar]
        if req and req.julgamento_por in ("grupo", "global") and inviaveis and viaveis:
            # Julgamento por grupo/global: precisa cotar todos os itens; um inviável derruba a participação
            motivo = "julgamento por grupo/global e há item inviável: " + "; ".join(f"item {r.numero_item}: {r.justificativa}" for r in inviaveis)
            for it in itens:
                it.participar = False
                session.add(it)
            set_status(session, op, Status.DESCARTADA, motivo[:1500])
        elif not viaveis:
            set_status(session, op, Status.DESCARTADA, "sem item com preço viável: " + "; ".join(r.justificativa for r in resultados)[:1500])
        elif total_proposta > p.valor_maximo_proposta:
            set_status(session, op, Status.DESCARTADA, f"total R$ {total_proposta:,.2f} acima do teto absoluto")
        else:
            set_status(session, op, Status.PRECIFICADA, f"{len(viaveis)}/{len(resultados)} itens; total R$ {total_proposta:,.2f}")
        session.commit()
        return resultados
