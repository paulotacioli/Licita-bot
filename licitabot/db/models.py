"""Modelos SQLModel (SQLite). Uma linha em `oportunidade` por edital do PNCP."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import Column, JSON, Text, UniqueConstraint
from sqlmodel import Field, SQLModel

from licitabot.pipeline.states import Status


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Oportunidade(SQLModel, table=True):
    __tablename__ = "oportunidade"

    id: Optional[int] = Field(default=None, primary_key=True)
    numero_controle_pncp: str = Field(index=True, unique=True)
    orgao_cnpj: str = Field(index=True)
    ano: int
    sequencial: int
    orgao_nome: str = ""
    unidade_nome: str = ""
    esfera: str = ""
    poder: str = ""
    uf: str = ""
    municipio: str = ""
    modalidade_id: int = 0
    modalidade_nome: str = ""
    modo_disputa: str = ""
    srp: bool = False
    objeto: str = Field(default="", sa_column=Column(Text))
    informacao_complementar: str = Field(default="", sa_column=Column(Text))
    valor_estimado: Optional[float] = None
    link_sistema_origem: str = ""
    portal: str = "desconhecido"  # comprasgov | licitanet | bll | bnc | pcp | outro | desconhecido
    numero_compra: str = ""
    situacao_nome: str = ""
    uasg: str = ""

    data_publicacao: Optional[datetime] = None
    data_abertura_proposta: Optional[datetime] = None
    data_encerramento_proposta: Optional[datetime] = None
    data_sessao: Optional[datetime] = None

    status: str = Field(default=Status.DESCOBERTA, index=True)
    status_motivo: str = Field(default="", sa_column=Column(Text))
    # Pré-triagem por IA a partir do objeto (antes de baixar o edital): relevante | irrelevante | incerto | ""
    pre_triagem: str = Field(default="", index=True)
    pre_triagem_motivo: str = Field(default="", sa_column=Column(Text))
    erro_ultimo: str = Field(default="", sa_column=Column(Text))
    tentativas: int = 0
    pasta: str = ""
    raw_search_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))

    criado_em: datetime = Field(default_factory=utcnow)
    atualizado_em: datetime = Field(default_factory=utcnow)


class ArquivoEdital(SQLModel, table=True):
    __tablename__ = "arquivo_edital"

    id: Optional[int] = Field(default=None, primary_key=True)
    oportunidade_id: int = Field(foreign_key="oportunidade.id", index=True)
    sequencial_doc: int = 0
    titulo: str = ""
    tipo: str = ""
    url: str = ""
    caminho_local: str = ""
    hash_sha256: str = ""
    tamanho_bytes: int = 0
    texto_extraido: bool = False
    precisa_ocr: bool = False
    paginas: int = 0
    caminho_texto: str = ""
    papel: str = "anexo"  # edital | termo_referencia | minuta_contrato | modelo_declaracao | planilha | anexo
    criado_em: datetime = Field(default_factory=utcnow)


class Item(SQLModel, table=True):
    __tablename__ = "item"
    __table_args__ = (UniqueConstraint("oportunidade_id", "numero_item"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    oportunidade_id: int = Field(foreign_key="oportunidade.id", index=True)
    numero_item: int
    descricao: str = Field(default="", sa_column=Column(Text))
    quantidade: float = 0
    unidade: str = ""
    valor_unit_estimado: Optional[float] = None
    valor_total_estimado: Optional[float] = None
    tipo_beneficio: str = ""
    criterio_julgamento: str = ""
    situacao: str = ""
    participar: bool = True
    valor_unit_proposto: Optional[float] = None
    valor_total_proposto: Optional[float] = None
    marca_modelo: str = ""
    descricao_detalhada_proposta: str = Field(default="", sa_column=Column(Text))
    justificativa_preco: str = Field(default="", sa_column=Column(Text))
    horas_estimadas: Optional[float] = None
    tipo_precificacao: str = ""  # desenvolvimento | saas | manutencao | outro
    raw_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))


class Triagem(SQLModel, table=True):
    __tablename__ = "triagem"

    id: Optional[int] = Field(default=None, primary_key=True)
    oportunidade_id: int = Field(foreign_key="oportunidade.id", index=True)
    relevante: bool = False
    score: int = 0
    categoria: str = ""
    justificativa: str = Field(default="", sa_column=Column(Text))
    exige_presenca_fisica: bool = False
    exige_hardware: bool = False
    exige_atestado_especifico: bool = False
    modelo_llm: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    criado_em: datetime = Field(default_factory=utcnow)


class Analise(SQLModel, table=True):
    __tablename__ = "analise"

    id: Optional[int] = Field(default=None, primary_key=True)
    oportunidade_id: int = Field(foreign_key="oportunidade.id", index=True, unique=True)
    modelo_llm: str = ""
    versao_prompt: str = ""
    json_requisitos: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    confianca: float = 0.0
    alertas: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    tokens_in: int = 0
    tokens_out: int = 0
    criado_em: datetime = Field(default_factory=utcnow)


class DocumentoGerado(SQLModel, table=True):
    __tablename__ = "documento_gerado"

    id: Optional[int] = Field(default=None, primary_key=True)
    oportunidade_id: int = Field(foreign_key="oportunidade.id", index=True)
    tipo: str
    titulo: str = ""
    origem: str = "gerado"  # gerado | cofre
    caminho_docx: str = ""
    caminho_pdf: str = ""
    hash_sha256: str = ""
    exigido_pelo_edital: bool = True
    anexado_no_portal: bool = False
    validado: bool = False
    precisa_assinatura_icp: bool = False
    status: str = "gerado"  # gerado | do_cofre | FALTANDO | VENCIDO | PRECISA_ASSINATURA
    observacao: str = Field(default="", sa_column=Column(Text))
    ordem: int = 0
    criado_em: datetime = Field(default_factory=utcnow)


class DocumentoCofre(SQLModel, table=True):
    __tablename__ = "documento_cofre"

    id: Optional[int] = Field(default=None, primary_key=True)
    tipo: str = Field(index=True)
    titulo: str = ""
    caminho: str = ""
    hash_sha256: str = ""
    emitido_em: Optional[datetime] = None
    valido_ate: Optional[datetime] = None
    orgao_emissor: str = ""
    descricao: str = Field(default="", sa_column=Column(Text))
    ativo: bool = True
    criado_em: datetime = Field(default_factory=utcnow)


class Aprovacao(SQLModel, table=True):
    __tablename__ = "aprovacao"

    id: Optional[int] = Field(default=None, primary_key=True)
    oportunidade_id: int = Field(foreign_key="oportunidade.id", index=True)
    token_hash: str = Field(index=True)
    criado_em: datetime = Field(default_factory=utcnow)
    expira_em: datetime
    decisao: str = "pendente"  # pendente | aprovada | rejeitada | expirada
    decidido_em: Optional[datetime] = None
    ip_origem: str = ""
    canal: str = ""  # link | email_reply | cli
    snapshot_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    snapshot_hash: str = ""
    lembrete_24h_enviado: bool = False
    lembrete_6h_enviado: bool = False


class ExecucaoPortal(SQLModel, table=True):
    __tablename__ = "execucao_portal"

    id: Optional[int] = Field(default=None, primary_key=True)
    oportunidade_id: Optional[int] = Field(default=None, foreign_key="oportunidade.id", index=True)
    portal: str = "comprasgov"
    etapa: str = ""
    iniciado_em: datetime = Field(default_factory=utcnow)
    finalizado_em: Optional[datetime] = None
    sucesso: bool = False
    dry_run: bool = True
    screenshots: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    trace_caminho: str = ""
    resumo_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    erro: str = Field(default="", sa_column=Column(Text))


class Evento(SQLModel, table=True):
    __tablename__ = "evento"

    id: Optional[int] = Field(default=None, primary_key=True)
    oportunidade_id: Optional[int] = Field(default=None, foreign_key="oportunidade.id", index=True)
    tipo: str = Field(index=True)
    mensagem: str = Field(default="", sa_column=Column(Text))
    dados_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    criado_em: datetime = Field(default_factory=utcnow)


class SessaoPregao(SQLModel, table=True):
    __tablename__ = "sessao_pregao"

    id: Optional[int] = Field(default=None, primary_key=True)
    oportunidade_id: int = Field(foreign_key="oportunidade.id", index=True)
    item_numero: int = 0
    situacao: str = ""
    posicao_atual: Optional[int] = None
    melhor_lance: Optional[float] = None
    nosso_lance: Optional[float] = None
    mensagens_chat: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    ultima_leitura_em: datetime = Field(default_factory=utcnow)


class OnboardingPasso(SQLModel, table=True):
    __tablename__ = "onboarding_passo"

    id: Optional[int] = Field(default=None, primary_key=True)
    chave: str = Field(index=True, unique=True)
    titulo: str = ""
    descricao: str = Field(default="", sa_column=Column(Text))
    ordem: int = 0
    concluido: bool = False
    concluido_em: Optional[datetime] = None
    verificacao_automatica: bool = False
    observacao: str = Field(default="", sa_column=Column(Text))


class UsuarioPainel(SQLModel, table=True):
    """Usuário do painel web. A senha é guardada como hash PBKDF2, nunca em texto."""

    __tablename__ = "usuario_painel"

    id: Optional[int] = Field(default=None, primary_key=True)
    usuario: str = Field(index=True, unique=True)
    senha_hash: str = ""
    criado_em: datetime = Field(default_factory=utcnow)
    ultimo_acesso_em: Optional[datetime] = None


class ConvitePainel(SQLModel, table=True):
    """Convite para criar conta no painel. Guarda só o hash do código; o código em si vai para quem foi convidado."""

    __tablename__ = "convite_painel"

    id: Optional[int] = Field(default=None, primary_key=True)
    codigo_hash: str = Field(index=True, unique=True)
    criado_por: str = ""
    criado_em: datetime = Field(default_factory=utcnow)
    expira_em: datetime
    usado_em: Optional[datetime] = None
    usado_por: str = ""
