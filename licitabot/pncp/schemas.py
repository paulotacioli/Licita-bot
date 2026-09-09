"""Schemas Pydantic para as respostas do PNCP (tolerantes a campos extras/nulos)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

MODALIDADES: dict[int, str] = {
    1: "Leilão - Eletrônico",
    2: "Diálogo Competitivo",
    3: "Concurso",
    4: "Concorrência - Eletrônica",
    5: "Concorrência - Presencial",
    6: "Pregão - Eletrônico",
    7: "Pregão - Presencial",
    8: "Dispensa de Licitação",
    9: "Inexigibilidade",
    10: "Manifestação de Interesse",
    11: "Pré-qualificação",
    12: "Credenciamento",
    13: "Leilão - Presencial",
}


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    s = str(value).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[: len(fmt) + (6 if "%f" in fmt else 0)], fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s.replace("Z", ""))
    except ValueError:
        return None


class SearchItem(BaseModel):
    """Um item da API de busca (`/api/search/`)."""

    model_config = ConfigDict(extra="allow")

    id: str = ""
    title: str = ""
    description: str = ""
    item_url: str = ""
    document_type: str = ""
    numero_controle_pncp: str = ""
    ano: str = ""
    numero_sequencial: str = ""
    orgao_cnpj: str = ""
    orgao_nome: str = ""
    unidade_nome: str = ""
    esfera_nome: str = ""
    poder_nome: str = ""
    municipio_nome: str = ""
    uf: str = ""
    modalidade_licitacao_id: str = "0"
    modalidade_licitacao_nome: str = ""
    situacao_nome: str = ""
    data_publicacao_pncp: str | None = None
    data_atualizacao_pncp: str | None = None
    data_inicio_vigencia: str | None = None
    data_fim_vigencia: str | None = None
    cancelado: bool = False
    valor_global: float | None = None
    tem_resultado: bool = False

    @field_validator("*", mode="before")
    @classmethod
    def _none_to_default(cls, v):  # PNCP manda null em campos string
        return v

    @property
    def cnpj(self) -> str:
        return self.orgao_cnpj or self.item_url.strip("/").split("/")[1]

    @property
    def ano_int(self) -> int:
        return int(self.ano or self.item_url.strip("/").split("/")[2])

    @property
    def seq_int(self) -> int:
        return int(self.numero_sequencial or self.item_url.strip("/").split("/")[3])

    @property
    def modalidade_id(self) -> int:
        try:
            return int(self.modalidade_licitacao_id or 0)
        except ValueError:
            return 0

    @property
    def prazo_proposta(self) -> datetime | None:
        return parse_dt(self.data_fim_vigencia)


class SearchResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    total: int = 0
    items: list[SearchItem] = Field(default_factory=list)


class ArquivoPNCP(BaseModel):
    model_config = ConfigDict(extra="allow")
    sequencialDocumento: int = 0
    titulo: str = ""
    tipoDocumentoNome: str = ""
    tipoDocumentoDescricao: str = ""
    url: str = ""
    statusAtivo: bool = True
    dataPublicacaoPncp: str | None = None


class ItemPNCP(BaseModel):
    model_config = ConfigDict(extra="allow")
    numeroItem: int = 0
    descricao: str = ""
    quantidade: float = 0
    unidadeMedida: str = ""
    valorUnitarioEstimado: float | None = None
    valorTotal: float | None = None
    tipoBeneficioNome: str = ""
    criterioJulgamentoNome: str = ""
    situacaoCompraItemNome: str = ""
    materialOuServicoNome: str = ""


class CompraDetalhe(BaseModel):
    model_config = ConfigDict(extra="allow")
    objetoCompra: str = ""
    informacaoComplementar: str | None = None
    modalidadeNome: str = ""
    modalidadeId: int | None = None
    modoDisputaNome: str = ""
    situacaoCompraNome: str = ""
    dataAberturaProposta: str | None = None
    dataEncerramentoProposta: str | None = None
    linkSistemaOrigem: str | None = None
    valorTotalEstimado: float | None = None
    srp: bool = False
    numeroCompra: str = ""
    tipoInstrumentoConvocatorioNome: str = ""
    unidadeOrgao: dict[str, Any] | None = None
    orgaoEntidade: dict[str, Any] | None = None
