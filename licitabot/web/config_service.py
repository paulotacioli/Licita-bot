"""Leitura e escrita dos YAML de configuração a partir do painel.

Usa ruamel.yaml em modo round-trip para preservar comentários e formatação: os arquivos em
config/ explicam as regras de negócio e essas explicações não podem sumir quando alguém
salva um formulário. A gravação é atômica (arquivo temporário + troca) e cada campo é
validado antes, para que um erro de digitação não deixe o robô com uma configuração quebrada.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ruamel.yaml import YAML

from licitabot.config import get_settings

log = logging.getLogger(__name__)

RX_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", re.I)
RX_UF = re.compile(r"^[A-Z]{2}$")


def _yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.width = 4096  # não re-quebrar linhas longas dos comentários
    return y


# ---------- tipos de campo ----------


@dataclass
class Campo:
    """Um campo editável do painel, ligado a um caminho dentro do YAML."""

    chave: str  # caminho separado por ponto, ex.: "representante.nome"
    label: str
    tipo: str = "texto"  # texto | inteiro | decimal | percentual | dinheiro | booleano | lista | area
    ajuda: str = ""
    minimo: float | None = None
    maximo: float | None = None
    obrigatorio: bool = False
    validador: Callable[[Any], str | None] | None = None


@dataclass
class Secao:
    slug: str
    titulo: str
    arquivo: str
    descricao: str = ""
    campos: list[Campo] = field(default_factory=list)


def _valida_emails(v: Any) -> str | None:
    ruins = [e for e in (v or []) if not RX_EMAIL.match(str(e).strip())]
    return f"E-mail inválido: {', '.join(ruins)}" if ruins else None


def _valida_ufs(v: Any) -> str | None:
    ruins = [u for u in (v or []) if not RX_UF.match(str(u).strip().upper())]
    return f"UF inválida: {', '.join(ruins)}. Use a sigla com duas letras." if ruins else None


def _valida_cnpj(v: Any) -> str | None:
    d = re.sub(r"\D", "", str(v or ""))
    return None if len(d) == 14 or not d else "CNPJ deve ter 14 dígitos."


def _valida_cpf(v: Any) -> str | None:
    d = re.sub(r"\D", "", str(v or ""))
    return None if len(d) == 11 or not d else "CPF deve ter 11 dígitos."


# ---------- seções do painel ----------

SECOES: list[Secao] = [
    Secao(
        slug="perfil",
        titulo="Perfil de interesse",
        arquivo="triagem.yaml",
        descricao="Escreva com suas palavras o que vale a pena e o que não vale. A IA usa isto para julgar cada edital.",
        campos=[
            Campo("perfil.buscamos", "O que buscamos", "area", obrigatorio=True,
                  ajuda="Descreva os tipos de contratação que interessam: objeto, forma de trabalho, modelo comercial. "
                        "Exemplo: sistemas e softwares sob demanda, sustentação, SaaS próprio, trabalho remoto."),
            Campo("perfil.evitamos", "O que evitamos", "area",
                  ajuda="O que deve ser descartado mesmo que envolva software. Exemplo: exigência de equipe presencial, "
                        "revenda de licença de fabricante, outsourcing de postos, hardware."),
            Campo("perfil.pre_triagem_ia", "Pré-triagem por IA ligada", "booleano",
                  ajuda="A IA lê o objeto de cada edital novo, em lotes, com o modelo mais barato, e descarta o que não "
                        "combina antes de baixar qualquer PDF. Com isto ligado, as palavras positivas dos filtros técnicos "
                        "deixam de ser obrigatórias. Exigências que o objeto não mostra, como presença física, são "
                        "verificadas depois, na triagem completa que lê o edital."),
            Campo("perfil.lote_pre_triagem", "Objetos por chamada à IA", "inteiro", minimo=5, maximo=60),
            Campo("perfil.maximo_por_rodada", "Máximo por rodada", "inteiro", minimo=10, maximo=5000,
                  ajuda="Quantos editais a pré-triagem avalia por execução. O restante fica para a próxima."),
        ],
    ),
    Secao(
        slug="notificacoes",
        titulo="Notificações",
        arquivo="notificacoes.yaml",
        descricao="Quem recebe os e-mails de licitação e com que frequência.",
        campos=[
            Campo("destinatarios", "Quem recebe os e-mails", "lista", validador=_valida_emails,
                  ajuda="Um e-mail por linha. Todos recebem os avisos, o resumo diário e os pedidos de aprovação. "
                        "Se ficar vazio, vale o OWNER_EMAIL do arquivo .env."),
            Campo("limite_diario_envios", "Limite de licitações por dia", "inteiro", minimo=1, maximo=500,
                  ajuda="Máximo de licitações enviadas por e-mail em um dia. O excedente fica na fila, "
                        "sempre priorizando o prazo mais próximo."),
            Campo("hora_resumo_diario", "Hora do resumo diário", "inteiro", minimo=0, maximo=23,
                  ajuda="Hora de Brasília em que sai o resumo com as novas licitações e o estado da fila."),
        ],
    ),
    Secao(
        slug="filtros",
        titulo="Filtros técnicos",
        arquivo="triagem.yaml",
        descricao="Cortes estruturais e baratos, aplicados antes da IA. Os termos de busca definem o que é pesquisado no PNCP.",
        campos=[
            Campo("termos_busca", "Termos pesquisados no PNCP", "lista", obrigatorio=True,
                  ajuda="Um termo por linha. Cada termo vira uma busca separada no PNCP."),
            Campo("palavras_positivas", "Palavras que o objeto precisa ter", "lista",
                  ajuda="Um item por linha. Só é obrigatório quando a pré-triagem por IA está desligada; com ela "
                        "ligada, quem julga o objeto é a IA a partir do seu perfil de interesse."),
            Campo("palavras_negativas", "Palavras que descartam na hora", "lista",
                  ajuda="Um item por linha. Se o objeto contiver qualquer uma, o edital é descartado "
                        "sem chamar a IA. Serve para cortar compra de hardware, veículos e afins."),
            Campo("ufs", "Estados de interesse", "lista", validador=_valida_ufs,
                  ajuda="Siglas de duas letras, uma por linha. Vazio significa o Brasil inteiro."),
            Campo("prazo_minimo_dias", "Prazo mínimo até o encerramento (dias)", "inteiro", minimo=0, maximo=90,
                  ajuda="Ignora editais que fecham antes desse prazo, porque não daria tempo de montar a proposta."),
            Campo("score_minimo", "Nota mínima da triagem", "inteiro", minimo=0, maximo=100,
                  ajuda="A IA dá uma nota de 0 a 100. Acima deste valor o edital segue para análise completa."),
            Campo("score_zona_cinzenta", "Nota da zona cinzenta", "inteiro", minimo=0, maximo=100,
                  ajuda="Entre esta nota e a mínima, o edital fica separado para você decidir manualmente."),
            Campo("max_paginas_por_termo", "Páginas por termo de busca", "inteiro", minimo=1, maximo=200,
                  ajuda="Cada página traz 100 resultados. Aumentar amplia o alcance e deixa a busca mais lenta."),
        ],
    ),
    Secao(
        slug="precos",
        titulo="Preços",
        arquivo="precos.yaml",
        descricao="As regras que decidem o preço. A IA só sugere horas; o número final sai daqui.",
        campos=[
            Campo("valor_hora_padrao", "Valor da hora padrão", "dinheiro", minimo=1, maximo=10000,
                  ajuda="Usado quando o perfil profissional do item não é identificado."),
            Campo("margem_minima", "Margem sobre o custo", "percentual", minimo=0, maximo=3,
                  ajuda="Percentual de lucro aplicado sobre o custo estimado."),
            Campo("fator_impostos", "Impostos embutidos", "percentual", minimo=0, maximo=1,
                  ajuda="Carga tributária aproximada embutida no preço."),
            Campo("desconto_alvo_vs_estimado", "Desconto alvo sobre o estimado", "percentual", minimo=0, maximo=1,
                  ajuda="O preço que o robô mira, abaixo do valor estimado pelo órgão."),
            Campo("desconto_maximo_vs_estimado", "Desconto máximo", "percentual", minimo=0, maximo=1,
                  ajuda="Piso absoluto. Abaixo disso o órgão costuma exigir prova de que o preço é exequível."),
            Campo("valor_maximo_proposta", "Teto por proposta", "dinheiro", minimo=0,
                  ajuda="O robô não monta proposta acima deste valor."),
            Campo("valor_minimo_oportunidade", "Valor mínimo da licitação", "dinheiro", minimo=0,
                  ajuda="Editais abaixo deste valor são descartados na descoberta."),
            Campo("preco_ponto_funcao", "Custo por ponto de função", "dinheiro", minimo=0),
            Campo("preco_ust", "Custo por UST", "dinheiro", minimo=0),
            Campo("preco_saas_usuario_mes", "SaaS por usuário/mês", "dinheiro", minimo=0),
            Campo("preco_saas_minimo_mensal", "SaaS mínimo mensal", "dinheiro", minimo=0),
            Campo("horas_padrao_por_item_sem_estimativa", "Horas padrão por item", "inteiro", minimo=1, maximo=10000,
                  ajuda="Usado quando o edital não dá elementos para a IA estimar o esforço."),
        ],
    ),
    Secao(
        slug="empresa",
        titulo="Empresa",
        arquivo="empresa.yaml",
        descricao="Dados usados nas propostas e declarações. Confira antes de aprovar qualquer envio.",
        campos=[
            Campo("razao_social", "Razão social", "texto", obrigatorio=True),
            Campo("nome_fantasia", "Nome fantasia", "texto"),
            Campo("cnpj", "CNPJ", "texto", obrigatorio=True, validador=_valida_cnpj),
            Campo("inscricao_estadual", "Inscrição estadual", "texto"),
            Campo("inscricao_municipal", "Inscrição municipal", "texto"),
            Campo("porte", "Porte", "texto", ajuda="ME, EPP ou DEMAIS. Define o direito aos benefícios da LC 123/2006."),
            Campo("optante_simples", "Optante pelo Simples Nacional", "booleano"),
            Campo("endereco", "Endereço", "texto"),
            Campo("cidade", "Cidade", "texto"),
            Campo("uf", "UF", "texto"),
            Campo("cep", "CEP", "texto"),
            Campo("telefone", "Telefone", "texto"),
            Campo("email", "E-mail da empresa", "texto"),
            Campo("site", "Site", "texto"),
            Campo("representante.nome", "Quem assina as propostas", "texto",
                  ajuda="Nome completo do sócio-administrador, exatamente como no contrato social."),
            Campo("representante.cpf", "CPF de quem assina", "texto", validador=_valida_cpf),
            Campo("representante.cargo", "Cargo", "texto"),
            Campo("representante.email", "E-mail do representante", "texto"),
            Campo("representante.telefone", "Telefone do representante", "texto"),
            Campo("banco.banco", "Banco", "texto"),
            Campo("banco.agencia", "Agência", "texto"),
            Campo("banco.conta", "Conta", "texto"),
            Campo("validade_proposta_dias", "Validade da proposta (dias)", "inteiro", minimo=1, maximo=365),
        ],
    ),
]

SECOES_POR_SLUG = {s.slug: s for s in SECOES}

PADRAO_NOTIFICACOES = """# Quem recebe os e-mails do Licita-Bot e com que frequência.
# Editável pelo painel em Configurações > Notificações.

# Um e-mail por item. Se ficar vazio, vale o OWNER_EMAIL do .env.
destinatarios: []

# Máximo de licitações enviadas por e-mail em um dia; o excedente fica na fila para o dia seguinte.
limite_diario_envios: 50

# Hora de Brasília (0-23) em que sai o resumo diário.
hora_resumo_diario: 8
"""


def caminho(arquivo: str) -> Path:
    return get_settings().config_path / arquivo


def garantir_notificacoes() -> Path:
    p = caminho("notificacoes.yaml")
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(PADRAO_NOTIFICACOES, encoding="utf-8")
    return p


def carregar(arquivo: str) -> Any:
    p = garantir_notificacoes() if arquivo == "notificacoes.yaml" else caminho(arquivo)
    if not p.exists():
        return {}
    with p.open("r", encoding="utf-8") as f:
        return _yaml().load(f) or {}


def _get(dados: Any, chave: str) -> Any:
    atual: Any = dados
    for parte in chave.split("."):
        if not isinstance(atual, dict) or parte not in atual:
            return None
        atual = atual[parte]
    return atual


def _set(dados: Any, chave: str, valor: Any) -> None:
    partes = chave.split(".")
    atual = dados
    for parte in partes[:-1]:
        if parte not in atual or not isinstance(atual[parte], dict):
            atual[parte] = {}
        atual = atual[parte]
    atual[partes[-1]] = valor


def valores_atuais(secao: Secao) -> dict[str, Any]:
    dados = carregar(secao.arquivo)
    return {c.chave: _get(dados, c.chave) for c in secao.campos}


def _converter(campo: Campo, bruto: str) -> tuple[Any, str | None]:
    """Converte o texto do formulário para o tipo do campo. Retorna (valor, erro)."""
    bruto = (bruto or "").strip()
    if campo.tipo == "lista":
        itens = [ln.strip() for ln in bruto.replace(",", "\n").splitlines() if ln.strip()]
        if campo.chave == "ufs":
            itens = [i.upper() for i in itens]
        if campo.obrigatorio and not itens:
            return None, f"{campo.label}: preencha ao menos um item."
        return itens, None
    if campo.tipo == "booleano":
        return bruto.lower() in ("1", "on", "true", "sim"), None
    if campo.tipo in ("inteiro", "decimal", "dinheiro", "percentual"):
        if not bruto:
            return None, f"{campo.label}: preencha um número."
        texto = bruto.replace("R$", "").replace("%", "").strip()
        # aceita 1.234,56 e 1234.56
        if "," in texto:
            texto = texto.replace(".", "").replace(",", ".")
        try:
            numero = float(texto)
        except ValueError:
            return None, f"{campo.label}: '{bruto}' não é um número válido."
        if campo.tipo == "percentual":
            numero = numero / 100.0
        if campo.tipo == "inteiro":
            numero = int(round(numero))
        else:
            numero = round(numero, 4)
        if campo.minimo is not None and numero < campo.minimo:
            return None, f"{campo.label}: não pode ser menor que {campo.minimo}."
        if campo.maximo is not None and numero > campo.maximo:
            return None, f"{campo.label}: não pode ser maior que {campo.maximo}."
        return numero, None
    if campo.obrigatorio and not bruto:
        return None, f"{campo.label}: campo obrigatório."
    if campo.tipo == "area":
        return " ".join(bruto.split()) if "\n" not in bruto.strip() else bruto.strip(), None
    return bruto, None


def salvar(secao: Secao, form: dict[str, Any]) -> list[str]:
    """Aplica o formulário ao YAML da seção. Retorna a lista de erros; vazia significa salvo."""
    dados = carregar(secao.arquivo)
    erros: list[str] = []
    pendentes: list[tuple[Campo, Any]] = []

    for campo in secao.campos:
        if campo.tipo == "booleano":
            bruto = "on" if form.get(campo.chave) is not None else ""
        elif campo.chave not in form:
            continue  # campo não veio no formulário: não mexe no que está gravado
        else:
            bruto = str(form.get(campo.chave, ""))
        valor, erro = _converter(campo, bruto)
        if erro:
            erros.append(erro)
            continue
        if campo.validador:
            erro = campo.validador(valor)
            if erro:
                erros.append(f"{campo.label}: {erro}")
                continue
        pendentes.append((campo, valor))

    if erros:
        return erros

    for campo, valor in pendentes:
        _set(dados, campo.chave, valor)

    _gravar_atomico(secao.arquivo, dados)
    _limpar_caches()
    return []


def _gravar_atomico(arquivo: str, dados: Any) -> None:
    destino = caminho(arquivo)
    destino.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(destino.parent), prefix=f".{destino.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            _yaml().dump(dados, f)
        os.replace(tmp, destino)
        log.info("configuração gravada: %s", destino)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise


def _limpar_caches() -> None:
    """As configurações são lidas por funções sem cache, mas get_settings usa lru_cache."""
    try:
        get_settings.cache_clear()
    except AttributeError:
        pass


def valor_para_form(campo: Campo, valor: Any) -> Any:
    """Converte o valor gravado no YAML para o que o formulário mostra."""
    if campo.tipo == "lista":
        return "\n".join(str(v) for v in (valor or []))
    if campo.tipo == "booleano":
        return bool(valor)
    if valor is None:
        return ""
    if campo.tipo == "percentual":
        return f"{float(valor) * 100:g}"
    if campo.tipo == "dinheiro":
        return f"{float(valor):.2f}".rstrip("0").rstrip(".") if float(valor) % 1 else f"{int(valor)}"
    if campo.tipo == "inteiro":
        return str(int(valor))
    return str(valor)


def formulario(secao: Secao) -> list[tuple[Campo, Any]]:
    """Pares (campo, valor pronto para o formulário) na ordem do esquema."""
    atuais = valores_atuais(secao)
    return [(c, valor_para_form(c, atuais.get(c.chave))) for c in secao.campos]
