"""Schemas de saída estruturada do LLM (triagem, análise do edital, redação)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Categoria = Literal["desenvolvimento", "licenciamento_saas", "manutencao", "outsourcing", "hardware_misto", "irrelevante"]


class PreTriagemItem(BaseModel):
    ref: int = Field(description="o número de referência do item, copiado da entrada")
    veredito: Literal["relevante", "irrelevante", "incerto"]
    motivo: str = Field(description="uma frase curta, em português, dizendo por quê")


class PreTriagemLote(BaseModel):
    itens: list[PreTriagemItem]


class TriagemResult(BaseModel):
    relevante: bool
    score: int = Field(ge=0, le=100, description="0-100: quão adequado para uma fábrica de software / SaaS")
    categoria: Categoria
    exige_presenca_fisica: bool = False
    exige_hardware: bool = False
    exige_atestado_especifico: bool = False
    resumo_objeto: str = Field(description="1-2 frases sobre o que o órgão quer")
    justificativa: str


class Prazo(BaseModel):
    abertura_propostas: str | None = Field(None, description="ISO 8601 se encontrado")
    encerramento_propostas: str | None = None
    sessao_publica: str | None = None
    validade_minima_proposta_dias: int | None = None
    prazo_execucao: str | None = Field(None, description="texto literal, ex.: '12 meses'")
    prazo_vigencia_contrato: str | None = None


class DocumentoExigido(BaseModel):
    tipo: Literal[
        "proposta_comercial",
        "proposta_tecnica",
        "planilha_custos",
        "decl_habilitacao",
        "decl_me_epp",
        "decl_menor",
        "decl_fato_impeditivo",
        "decl_elaboracao_independente",
        "decl_pcd",
        "decl_lgpd",
        "decl_ciencia_edital",
        "decl_nepotismo",
        "decl_trabalho_degradante",
        "decl_outra",
        "contrato_social",
        "cartao_cnpj",
        "documento_representante",
        "procuracao",
        "cnd_federal",
        "crf_fgts",
        "cndt",
        "cnd_estadual",
        "cnd_municipal",
        "certidao_falencia",
        "balanco_patrimonial",
        "indices_financeiros",
        "capital_social_minimo",
        "garantia_proposta",
        "atestado_capacidade_tecnica",
        "registro_conselho",
        "certificacao",
        "amostra_prova_conceito",
        "visita_tecnica",
        "sicaf",
        "outro",
    ]
    descricao_literal: str = Field(description="texto do edital que exige o documento, resumido")
    obrigatorio: bool = True
    fase: Literal["proposta", "habilitacao", "contratacao"] = "habilitacao"
    referencia: str = Field("", description="item/seção/página do edital")
    aceita_sicaf: bool = False
    modelo_no_anexo: str = Field("", description="nome do anexo com modelo, se houver")
    observacoes: str = ""


class ExigenciaAtestado(BaseModel):
    quantidade_minima: int = 1
    descricao: str = ""
    percentual_do_objeto: float | None = None
    exige_registro_conselho: bool = False
    permite_somatorio: bool | None = None


class QualificacaoTecnica(BaseModel):
    atestados: ExigenciaAtestado | None = None
    equipe_minima: list[str] = Field(default_factory=list)
    certificacoes: list[str] = Field(default_factory=list)
    outras: list[str] = Field(default_factory=list)


class IndiceFinanceiro(BaseModel):
    nome: Literal["LG", "SG", "LC", "outro"]
    minimo: float
    descricao: str = ""


class QualificacaoEconomica(BaseModel):
    exige_balanco: bool = False
    indices: list[IndiceFinanceiro] = Field(default_factory=list, description="índices exigidos e valores mínimos")
    capital_minimo_percentual: float | None = None
    patrimonio_liquido_minimo_percentual: float | None = None
    exige_certidao_falencia: bool = False
    garantia_proposta_percentual: float | None = None


class ItemRequisito(BaseModel):
    numero_item: int | None = None
    descricao: str
    tipo: Literal["desenvolvimento", "saas", "manutencao", "consultoria", "treinamento", "hardware", "outro"] = "outro"
    unidade: str = ""
    quantidade: float | None = None
    valor_estimado_unitario: float | None = None
    horas_estimadas_sugeridas: float | None = Field(None, description="estimativa de esforço, se aplicável")
    exigencias_tecnicas: list[str] = Field(default_factory=list)
    exige_marca_modelo: bool = False


class PropostaFormato(BaseModel):
    campos_obrigatorios: list[str] = Field(default_factory=list)
    exige_marca_modelo: bool = False
    exige_planilha_custos: bool = False
    exige_assinatura_digital_icp: bool = False
    limite_paginas_proposta_tecnica: int | None = None
    idioma: str = "pt-BR"
    observacoes: str = ""


class Citacao(BaseModel):
    requisito: str
    arquivo: str = ""
    pagina: int | None = None
    trecho: str = ""


class EditalRequisitos(BaseModel):
    numero_edital: str = ""
    orgao: str = ""
    uasg: str = ""
    objeto_resumido: str
    modalidade: str = ""
    criterio_julgamento: Literal["menor_preco", "maior_desconto", "tecnica_e_preco", "melhor_tecnica", "outro"] = "menor_preco"
    julgamento_por: Literal["item", "grupo", "global", "desconhecido"] = "desconhecido"
    modo_disputa: str = ""
    beneficio_me_epp: Literal["exclusivo", "cota_reservada", "preferencia", "nenhum", "desconhecido"] = "desconhecido"
    exige_sicaf: bool = False
    exige_visita_tecnica: bool = False
    exige_amostra_ou_poc: bool = False
    exige_presenca_fisica: bool = False
    permite_consorcio: bool | None = None
    permite_subcontratacao: bool | None = None
    prazos: Prazo = Field(default_factory=Prazo)
    documentos_exigidos: list[DocumentoExigido] = Field(default_factory=list)
    qualificacao_tecnica: QualificacaoTecnica = Field(default_factory=QualificacaoTecnica)
    qualificacao_economica: QualificacaoEconomica = Field(default_factory=QualificacaoEconomica)
    itens: list[ItemRequisito] = Field(default_factory=list)
    proposta_formato: PropostaFormato = Field(default_factory=PropostaFormato)
    penalidades_relevantes: list[str] = Field(default_factory=list)
    alertas: list[str] = Field(default_factory=list, description="riscos, exigências atípicas, incompatibilidades")
    citacoes: list[Citacao] = Field(default_factory=list)
    confianca: float = Field(0.7, ge=0, le=1)
    baseado_em_minuta_agu: bool | None = None


class SecaoRedigida(BaseModel):
    chave: str
    titulo: str
    texto: str


class PropostaTecnicaRedigida(BaseModel):
    secoes: list[SecaoRedigida]
    resumo_executivo: str
    premissas: list[str] = Field(default_factory=list)


class CompatAtestado(BaseModel):
    compativel: bool
    score: int = Field(ge=0, le=100)
    justificativa: str


class DeclaracaoAjustada(BaseModel):
    titulo: str
    texto: str = Field(description="texto integral da declaração, já com os dados da empresa")
    diferencas_vs_modelo_padrao: list[str] = Field(default_factory=list)
