"""Checklist de premissas de uma licitação: o que o edital exige, os números que decidem e o que a empresa já tem.

Nasceu da experiência com o Pregão 240/2026 da FDE: o que faltou não foi documento, foi saber ANTES
que o SICAF precisava estar regular até o 3º dia útil anterior, que o balanço era obrigatório e qual
era o piso de inexequibilidade. Este módulo transforma a análise do edital (EditalRequisitos) num
checklist com status por item, cruzando com o cofre e com o cadastro da empresa. Serve ao e-mail de
"licitação compatível", à página de detalhe do painel e ao resumo diário.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlmodel import select

from licitabot.config import load_empresa
from licitabot.db.models import DocumentoCofre, Oportunidade

# Percentuais usuais quando o edital não diz: a Lei 14.133 deixa o piso de inexequibilidade de serviços
# a critério do edital (50% é o mais comum) e limita PL/capital mínimo a 10% do estimado (art. 69, §3º).
PISO_INEXEQUIVEL_PADRAO = 0.50
PL_MINIMO_PADRAO = 0.10
DIAS_UTEIS_SICAF = 3

# tipo exigido no edital -> tipo no cofre. O que não está aqui o robô gera (propostas, declarações).
COFRE_POR_TIPO: dict[str, str] = {
    "contrato_social": "contrato_social",
    "cartao_cnpj": "cartao_cnpj",
    "documento_representante": "documento_representante",
    "procuracao": "procuracao",
    "cnd_federal": "cnd_federal",
    "crf_fgts": "crf_fgts",
    "cndt": "cndt",
    "cnd_estadual": "cnd_estadual",
    "cnd_municipal": "cnd_municipal",
    "certidao_falencia": "certidao_falencia",
    "balanco_patrimonial": "balanco_patrimonial",
    "indices_financeiros": "indices_financeiros",
    "atestado_capacidade_tecnica": "atestado_capacidade_tecnica",
    "registro_conselho": "registro_conselho",
    "certificacao": "certificacao",
    "sicaf": "sicaf",
}
GERADOS_PELO_ROBO = {
    "proposta_comercial", "proposta_tecnica", "planilha_custos", "decl_habilitacao", "decl_me_epp", "decl_menor",
    "decl_fato_impeditivo", "decl_elaboracao_independente", "decl_pcd", "decl_lgpd", "decl_ciencia_edital",
    "decl_nepotismo", "decl_trabalho_degradante", "decl_outra",
}
NOMES: dict[str, str] = {
    "proposta_comercial": "Proposta comercial", "proposta_tecnica": "Proposta técnica", "planilha_custos": "Planilha de custos",
    "decl_habilitacao": "Declaração de habilitação", "decl_me_epp": "Declaração de ME/EPP", "decl_menor": "Declaração de não emprego de menor",
    "decl_fato_impeditivo": "Declaração de fato impeditivo", "decl_elaboracao_independente": "Declaração de elaboração independente",
    "decl_pcd": "Declaração de reserva de cargos (PCD)", "decl_lgpd": "Declaração LGPD", "decl_ciencia_edital": "Declaração de ciência do edital",
    "decl_nepotismo": "Declaração de nepotismo", "decl_trabalho_degradante": "Declaração de trabalho degradante", "decl_outra": "Outra declaração",
    "contrato_social": "Contrato social consolidado", "cartao_cnpj": "Comprovante de inscrição no CNPJ",
    "documento_representante": "Documento do representante", "procuracao": "Procuração",
    "cnd_federal": "Certidão RFB/PGFN", "crf_fgts": "Certificado de regularidade do FGTS", "cndt": "Certidão negativa de débitos trabalhistas",
    "cnd_estadual": "Regularidade fiscal estadual", "cnd_municipal": "Regularidade fiscal municipal",
    "certidao_falencia": "Certidão negativa de falência", "balanco_patrimonial": "Balanço patrimonial e DRE",
    "indices_financeiros": "Índices financeiros", "capital_social_minimo": "Capital social mínimo",
    "garantia_proposta": "Garantia de proposta", "atestado_capacidade_tecnica": "Atestado de capacidade técnica",
    "registro_conselho": "Registro em conselho de classe", "certificacao": "Certificação", "amostra_prova_conceito": "Amostra ou prova de conceito",
    "visita_tecnica": "Visita técnica", "sicaf": "Cadastro no SICAF", "outro": "Outro documento",
}


@dataclass
class ItemChecklist:
    titulo: str
    detalhe: str = ""
    status: str = "verificar"  # ok | pendente | atencao | verificar | robo
    fonte: str = ""  # item/página do edital
    fase: str = ""


@dataclass
class Checklist:
    numeros: list[tuple[str, str, str]] = field(default_factory=list)  # (rótulo, valor formatado, observação)
    prazos: list[tuple[str, str, str]] = field(default_factory=list)
    itens: list[ItemChecklist] = field(default_factory=list)
    alertas: list[str] = field(default_factory=list)
    completo: bool = False  # True quando houve análise do edital; False = só descoberta/pré-triagem

    @property
    def pendentes(self) -> list[ItemChecklist]:
        return [i for i in self.itens if i.status in ("pendente", "atencao")]

    @property
    def resumo(self) -> dict[str, int]:
        out = {"ok": 0, "pendente": 0, "atencao": 0, "verificar": 0, "robo": 0}
        for i in self.itens:
            out[i.status] = out.get(i.status, 0) + 1
        return out


def brl(v: float | None) -> str:
    if v is None:
        return "—"
    return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def dias_uteis_antes(data: datetime, n: int) -> datetime:
    """Devolve a data que fica `n` dias úteis antes de `data` (só desconta fins de semana; feriados não)."""
    d = data
    faltam = n
    while faltam > 0:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            faltam -= 1
    return d


def _tem_no_cofre(session, tipo_cofre: str) -> DocumentoCofre | None:
    agora = datetime.now()
    docs = session.exec(select(DocumentoCofre).where(DocumentoCofre.tipo == tipo_cofre, DocumentoCofre.ativo == True)).all()  # noqa: E712
    validos = [d for d in docs if not d.valido_ate or d.valido_ate >= agora]
    return sorted(validos, key=lambda d: d.emitido_em or datetime.min, reverse=True)[0] if validos else None


def montar(session, op: Oportunidade, req: Any | None) -> Checklist:
    """Monta o checklist. `req` é o EditalRequisitos da análise, ou None quando só houve descoberta/pré-triagem."""
    ck = Checklist(completo=req is not None)
    emp = load_empresa()
    agora = datetime.now()
    estimado = op.valor_estimado

    # ---------- números ----------
    if estimado:
        ck.numeros.append(("Valor estimado pelo órgão", brl(estimado), "acima disso a proposta é desclassificada"))
        piso = estimado * PISO_INEXEQUIVEL_PADRAO
        ck.numeros.append(("Piso de inexequibilidade", brl(piso), "abaixo de 50% do estimado o pregoeiro pode exigir prova de que o preço se sustenta; confira o percentual no edital"))
        pl_pct = None
        if req and req.qualificacao_economica.patrimonio_liquido_minimo_percentual:
            pl_pct = req.qualificacao_economica.patrimonio_liquido_minimo_percentual
            if pl_pct > 1:
                pl_pct /= 100.0
        if pl_pct or (req and req.qualificacao_economica.exige_balanco):
            pct = pl_pct or PL_MINIMO_PADRAO
            ck.numeros.append(("Patrimônio líquido mínimo", brl(estimado * pct), f"{pct * 100:.0f}% do estimado, comprovado no balanço"))
        if req and req.qualificacao_economica.garantia_proposta_percentual:
            g = req.qualificacao_economica.garantia_proposta_percentual
            if g > 1:
                g /= 100.0
            ck.numeros.append(("Garantia de proposta", brl(estimado * g), f"{g * 100:.0f}% do estimado, exigida já na proposta"))
    else:
        ck.numeros.append(("Valor estimado pelo órgão", "não informado no PNCP", "só abrindo o edital"))
    if req and req.prazos.validade_minima_proposta_dias:
        ck.numeros.append(("Validade mínima da proposta", f"{req.prazos.validade_minima_proposta_dias} dias", ""))
    if req and req.prazos.prazo_execucao:
        ck.numeros.append(("Prazo de execução", req.prazos.prazo_execucao, ""))

    # ---------- prazos ----------
    enc = op.data_encerramento_proposta
    if enc:
        restante = enc - agora
        horas = restante.total_seconds() / 3600
        txt = f"{int(horas // 24)} d {int(horas % 24)} h" if horas > 0 else "ENCERRADO"
        ck.prazos.append(("Encerramento das propostas", enc.strftime("%d/%m/%Y %H:%M"), f"faltam {txt}" if horas > 0 else "prazo já passou"))
        exige_sicaf = bool(req and req.exige_sicaf) or op.portal == "comprasgov"
        if exige_sicaf:
            limite = dias_uteis_antes(enc.replace(hour=23, minute=59), DIAS_UTEIS_SICAF)
            passou = limite < agora
            ck.prazos.append((
                f"SICAF regular até o {DIAS_UTEIS_SICAF}º dia útil anterior",
                limite.strftime("%d/%m/%Y"),
                "PRAZO JÁ PASSOU: só participa quem já estava credenciado" if passou else "cadastro precisa estar credenciado e regular até esta data (regra usual do Compras.gov.br; confira o item 3 do edital)",
            ))
            if passou:
                ck.alertas.append("O prazo do SICAF já passou. Se a empresa não estava credenciada e regular nessa data, não dá para participar.")
    if op.data_sessao:
        ck.prazos.append(("Sessão pública", op.data_sessao.strftime("%d/%m/%Y %H:%M"), "disputa de lances"))

    # ---------- itens ----------
    if req is None:
        ck.itens.append(ItemChecklist("Análise do edital ainda não feita", "O robô só leu o objeto publicado no PNCP. Os documentos exigidos aparecem depois de baixar e analisar o edital.", "verificar"))
        sicaf = _tem_no_cofre(session, "sicaf")
        ck.itens.append(ItemChecklist("Cadastro no SICAF", "Credenciamento e níveis de habilitação. O Compras.gov.br só oferece a licitação a quem está com o cadastro regular.", "ok" if sicaf else "verificar"))
        for tipo in ("cnd_federal", "crf_fgts", "cndt", "certidao_falencia", "contrato_social", "balanco_patrimonial", "atestado_capacidade_tecnica"):
            doc = _tem_no_cofre(session, tipo)
            ck.itens.append(ItemChecklist(NOMES[tipo], f"no cofre, válido até {doc.valido_ate:%d/%m/%Y}" if doc and doc.valido_ate else ("no cofre" if doc else "não está no cofre"), "ok" if doc else "pendente"))
        return ck

    # SICAF e benefícios
    if req.exige_sicaf:
        sicaf = _tem_no_cofre(session, "sicaf")
        ck.itens.append(ItemChecklist("Cadastro no SICAF", "Exigido pelo edital. Confira o nível de habilitação: só credenciamento (nível I) basta para enviar proposta, mas sem os níveis de regularidade fiscal os documentos precisam ser enviados no prazo de horas se vencer.", "ok" if sicaf else "verificar", fase="participação"))
    porte = (emp.porte or "").upper()
    if req.beneficio_me_epp == "exclusivo" and porte not in ("ME", "EPP"):
        ck.itens.append(ItemChecklist("Licitação exclusiva para ME/EPP", f"A empresa está cadastrada como porte '{emp.porte or '?'}'. Sem enquadramento, não pode participar.", "atencao", fase="participação"))
    elif req.beneficio_me_epp in ("exclusivo", "cota_reservada", "preferencia") and porte in ("ME", "EPP"):
        ck.itens.append(ItemChecklist("Benefício ME/EPP", f"{req.beneficio_me_epp.replace('_', ' ')}: empate ficto de até 5% e prazo para regularizar documento fiscal. Marque o enquadramento no sistema ao enviar.", "ok", fase="participação"))

    # condições que costumam eliminar
    if req.exige_presenca_fisica:
        ck.itens.append(ItemChecklist("Exige presença física ou equipe no local", "Confira se é compatível com trabalho remoto.", "atencao", fase="execução"))
    if req.exige_visita_tecnica:
        ck.itens.append(ItemChecklist("Exige visita técnica", "Costuma ter data marcada antes do encerramento das propostas.", "atencao", fase="participação"))
    if req.exige_amostra_ou_poc:
        ck.itens.append(ItemChecklist("Exige amostra ou prova de conceito", "Prepare ambiente de demonstração.", "atencao", fase="habilitação"))
    if req.permite_consorcio:
        ck.itens.append(ItemChecklist("Permite consórcio", "Qualificação técnica soma atestados e a econômica soma patrimônios; cada consorciado precisa de SICAF regular.", "verificar", fase="participação"))

    # qualificação técnica
    qt = req.qualificacao_tecnica
    if qt.atestados:
        at = _tem_no_cofre(session, "atestado_capacidade_tecnica")
        det = qt.atestados.descricao or "atestado de serviço similar em nome da própria empresa (matriz ou filial)"
        if qt.atestados.quantidade_minima > 1:
            det += f" · mínimo {qt.atestados.quantidade_minima}"
        if qt.atestados.permite_somatorio:
            det += " · permite somar atestados"
        ck.itens.append(ItemChecklist("Atestado de capacidade técnica", det, "ok" if at else "pendente", fase="habilitação"))
    for c in qt.certificacoes:
        ck.itens.append(ItemChecklist(f"Certificação: {c}", "", "ok" if _tem_no_cofre(session, "certificacao") else "pendente", fase="habilitação"))
    for e in qt.equipe_minima:
        ck.itens.append(ItemChecklist(f"Equipe mínima: {e}", "", "verificar", fase="habilitação"))

    # qualificação econômica
    qe = req.qualificacao_economica
    if qe.exige_balanco:
        bal = _tem_no_cofre(session, "balanco_patrimonial")
        ck.itens.append(ItemChecklist("Balanço patrimonial e DRE", "Do último exercício, registrado; empresa com menos de 2 anos apresenta só o último. Declaração do contador sobre os índices costuma ser exigida junto.", "ok" if bal else "pendente", fase="habilitação"))
    for idx in qe.indices:
        ck.itens.append(ItemChecklist(f"Índice {idx.nome} mínimo {idx.minimo:g}", idx.descricao, "ok" if _tem_no_cofre(session, "indices_financeiros") else "pendente", fase="habilitação"))
    if qe.exige_certidao_falencia:
        ck.itens.append(ItemChecklist(NOMES["certidao_falencia"], "expedida pelo distribuidor da sede", "ok" if _tem_no_cofre(session, "certidao_falencia") else "pendente", fase="habilitação"))

    # documentos listados pelo edital
    vistos = {i.titulo for i in ck.itens}
    for d in req.documentos_exigidos:
        nome = NOMES.get(d.tipo, d.tipo)
        if nome in vistos:
            continue
        vistos.add(nome)
        detalhe = d.descricao_literal[:220]
        if d.tipo in GERADOS_PELO_ROBO:
            ck.itens.append(ItemChecklist(nome, detalhe, "robo", d.referencia, d.fase))
            continue
        tipo_cofre = COFRE_POR_TIPO.get(d.tipo)
        if tipo_cofre:
            doc = _tem_no_cofre(session, tipo_cofre)
            st = "ok" if doc else ("pendente" if d.obrigatorio else "verificar")
            if doc and doc.valido_ate:
                detalhe += f" · no cofre, válido até {doc.valido_ate:%d/%m/%Y}"
            ck.itens.append(ItemChecklist(nome, detalhe, st, d.referencia, d.fase))
        else:
            ck.itens.append(ItemChecklist(nome, detalhe, "verificar", d.referencia, d.fase))

    ck.alertas.extend(req.alertas or [])
    return ck


def para_dict(ck: Checklist) -> dict[str, Any]:
    """Forma serializável, para templates de e-mail e para a API do painel."""
    return {
        "completo": ck.completo,
        "numeros": [{"rotulo": r, "valor": v, "obs": o} for r, v, o in ck.numeros],
        "prazos": [{"rotulo": r, "valor": v, "obs": o} for r, v, o in ck.prazos],
        "itens": [{"titulo": i.titulo, "detalhe": i.detalhe, "status": i.status, "fonte": i.fonte, "fase": i.fase} for i in ck.itens],
        "alertas": list(ck.alertas),
        "resumo": ck.resumo,
    }
