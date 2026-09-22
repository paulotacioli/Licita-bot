"""Limpeza do objeto do edital para leitura humana.

O campo `objeto` do PNCP quase sempre começa com o mesmo preâmbulo cartorial — "Contratação de
empresa especializada para a prestação de serviços de..." — e às vezes vem com a etiqueta do
portal na frente. Cortado em 150 caracteres, sobra só o preâmbulo e nenhuma informação. Aqui
tiramos a casca e cortamos no fim de uma frase ou palavra, para que o resumo do e-mail diga o
que o órgão quer comprar.
"""

from __future__ import annotations

import re

# Etiqueta do portal: "[Portal de Compras Públicas] - ", "[BLL] – "
RX_ETIQUETA = re.compile(r"^\s*\[[^\]]{2,60}\]\s*[-–—:]?\s*")

# Preâmbulos removidos da frente, na ordem. Cada um só sai se o que vier depois ainda tiver conteúdo.
PREAMBULOS = [
    re.compile(r"^(constitui\s+)?objeto\s+(d[oa]\s+presente|dest[ae]|d[oa]\s+presente\s+\w+)[^:.]{0,60}[:.]\s*", re.I),
    re.compile(r"^o\s+objeto\s+d[oa]\s+\w+[^:.]{0,60}[:.]\s*", re.I),
    re.compile(r"^registro\s+de\s+preç?os?\s+(para|visando)\s+(a\s+)?(eventual\s+e\s+futura\s+)?", re.I),
    re.compile(r"^contrataç[ãa]o\s+de\s+(empresa|pessoa\s+jur[íi]dica|instituiç[ãa]o)\s*"
               r"(especializada|do\s+ramo[^,]{0,40})?\s*[,]?\s*(para|visando|objetivando|com\s+o\s+objetivo\s+de)\s+"
               r"(a\s+|o\s+)?(prestaç[ãa]o\s+d[eo]s?\s+serviç?os?\s+d[eo]\s+|fornecimento\s+d[eo]\s+|execuç[ãa]o\s+d[eo]\s+)?", re.I),
    re.compile(r"^contrataç[ãa]o\s+(de\s+)?(serviç?os?\s+(t[ée]cnicos?\s+)?(especializados?\s+)?d[eo]\s+|empresa\s+para\s+)?", re.I),
    re.compile(r"^prestaç[ãa]o\s+de\s+serviç?os?\s+(t[ée]cnicos?\s+)?(especializados?\s+)?d[eo]\s+", re.I),
    re.compile(r"^aquisiç[ãa]o\s+d[eo]\s+", re.I),
    re.compile(r"^(eventual\s+e\s+futura\s+)?(aquisiç[ãa]o|contrataç[ãa]o)\s+", re.I),
]
MINIMO_APROVEITAVEL = 25  # abaixo disso, tirar o preâmbulo deixaria o texto sem sentido


def limpar_objeto(texto: str, limite: int = 300) -> str:
    """Devolve o objeto sem preâmbulo cartorial, em uma linha, cortado numa fronteira legível."""
    t = " ".join((texto or "").split())
    if not t:
        return ""
    t = RX_ETIQUETA.sub("", t)
    for rx in PREAMBULOS:
        novo = rx.sub("", t, count=1)
        if novo != t and len(novo) >= MINIMO_APROVEITAVEL:
            t = novo
            break
    t = t.lstrip(" ,;:-–—")
    if t and t[0].islower():
        t = t[0].upper() + t[1:]
    return _cortar(t, limite)


def _cortar(t: str, limite: int) -> str:
    if len(t) <= limite:
        return t
    trecho = t[:limite]
    # de preferência no fim de uma frase; senão, na última palavra inteira
    ponto = max(trecho.rfind(". "), trecho.rfind("; "))
    if ponto >= limite * 0.6:
        return trecho[: ponto + 1]
    espaco = trecho.rfind(" ")
    return (trecho[:espaco] if espaco > 0 else trecho).rstrip(" ,;:-") + "…"


# ---------- período do contrato ----------
# O PNCP publica um valor só, quase sempre o total do contrato. Saber em quantos meses ele se
# divide muda a leitura: R$ 106 mil por 12 meses é um SaaS de R$ 8,8 mil/mês. O prazo costuma
# estar escrito no próprio objeto ("pelo prazo de 12 (doze) meses").

_EXTENSO = {
    "um": 1, "seis": 6, "oito": 8, "dez": 10, "doze": 12, "quinze": 15, "dezoito": 18, "vinte": 20,
    "vinte e quatro": 24, "trinta": 30, "trinta e seis": 36, "quarenta e oito": 48, "sessenta": 60,
}
RX_MESES_NUM = re.compile(r"(\d{1,3})\s*(?:\([^)]{0,40}\)\s*)?(?:\(?\s*)?mes(?:es|)\b", re.I)
RX_MESES_EXT = re.compile(r"\b(" + "|".join(sorted(_EXTENSO, key=len, reverse=True)) + r")\s*(?:\([^)]{0,20}\)\s*)?mes(?:es|)\b", re.I)
RX_ANOS = re.compile(r"(\d{1,2}|um|dois|três|tres|quatro|cinco)\s*(?:\([^)]{0,20}\)\s*)?ano(?:s|)\b", re.I)
_ANOS_EXTENSO = {"um": 1, "dois": 2, "três": 3, "tres": 3, "quatro": 4, "cinco": 5}


def periodo_meses(*textos: str | None) -> int | None:
    """Meses de vigência citados no texto ('12 (doze) meses', '1 (um) ano'), ou None se não disser."""
    for texto in textos:
        t = " ".join((texto or "").split()).lower()
        if not t:
            continue
        m = RX_MESES_NUM.search(t)
        if m and 1 <= int(m.group(1)) <= 120:
            return int(m.group(1))
        m = RX_MESES_EXT.search(t)
        if m:
            return _EXTENSO[m.group(1).lower()]
        m = RX_ANOS.search(t)
        if m:
            bruto = m.group(1).lower()
            anos = int(bruto) if bruto.isdigit() else _ANOS_EXTENSO.get(bruto, 0)
            if 1 <= anos <= 10:
                return anos * 12
    return None


def valor_por_mes(valor_total: float | None, meses: int | None) -> float | None:
    if not valor_total or not meses or meses <= 1:
        return None
    return valor_total / meses
