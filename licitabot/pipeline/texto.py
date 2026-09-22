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
