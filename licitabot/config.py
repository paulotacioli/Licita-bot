"""Configuração central: .env (segredos) + YAMLs em config/ (regras de negócio)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TZ_BRASILIA = "America/Sao_Paulo"


class Settings(BaseSettings):
    """Segredos e parâmetros operacionais lidos do .env."""

    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    # Backend de IA: "claude_code" usa a assinatura do Claude Code (CLI `claude`, token de `claude setup-token`);
    # "api" usa a API da Anthropic (ANTHROPIC_API_KEY).
    llm_backend: str = "claude_code"
    claude_cli_path: str = ""
    claude_code_oauth_token: str = ""
    anthropic_api_key: str = ""
    # OpenAI, opcional: use o prefixo "openai:" no modelo da etapa (ex.: LLM_MODEL_PRETRIAGEM=openai:gpt-5-mini)
    openai_api_key: str = ""
    openai_base_url: str = ""
    # Modelos por etapa. Sem prefixo = Claude; "openai:<modelo>" = OpenAI.
    llm_model_pretriagem: str = "haiku"  # classifica só pelo objeto, em lote: precisa ser barato
    llm_model_triagem: str = "sonnet"
    llm_model_analise: str = "opus"
    llm_model_redacao: str = "opus"

    owner_email: str = ""
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    imap_host: str = "imap.gmail.com"
    imap_port: int = 993

    approval_secret: str = ""
    public_base_url: str = ""
    web_host: str = "127.0.0.1"
    web_port: int = 8765

    dry_run: bool = True
    prazo_min_preparar_min: int = 1440
    prazo_min_enviar_min: int = 30

    # Cadência de envios ao dono: no máximo N licitações/dia (e-mails informativos + de aprovação), fila por prazo
    limite_diario_envios: int = 50
    hora_resumo_diario: int = 8

    data_dir: Path = Path("data")
    cofre_dir: Path = Path("cofre")
    config_dir: Path = Path("config")
    templates_dir: Path = Path("templates")

    def _abs(self, p: Path) -> Path:
        return p if p.is_absolute() else PROJECT_ROOT / p

    @property
    def data_path(self) -> Path:
        return self._abs(self.data_dir)

    @property
    def cofre_path(self) -> Path:
        return self._abs(self.cofre_dir)

    @property
    def config_path(self) -> Path:
        return self._abs(self.config_dir)

    @property
    def templates_path(self) -> Path:
        return self._abs(self.templates_dir)

    @property
    def db_path(self) -> Path:
        return self.data_path / "licitabot.db"

    @property
    def oportunidades_path(self) -> Path:
        return self.data_path / "oportunidades"

    @property
    def browser_profile_path(self) -> Path:
        return self.data_path / "browser_profile"

    @property
    def logs_path(self) -> Path:
        return self.data_path / "logs"

    def ensure_dirs(self) -> None:
        for p in (self.data_path, self.oportunidades_path, self.browser_profile_path, self.logs_path):
            p.mkdir(parents=True, exist_ok=True)


# ---------- YAMLs de negócio ----------


class Representante(BaseModel):
    nome: str = ""
    cpf: str = ""
    cargo: str = ""
    email: str = ""
    telefone: str = ""


class ProdutoSaaS(BaseModel):
    nome: str = ""
    descricao: str = ""
    funcionalidades: list[str] = Field(default_factory=list)
    preco_usuario_mes: float | None = None


class Empresa(BaseModel):
    razao_social: str = ""
    nome_fantasia: str = ""
    cnpj: str = ""
    inscricao_estadual: str = ""
    inscricao_municipal: str = ""
    porte: str = "ME"  # ME | EPP | DEMAIS
    optante_simples: bool = False
    endereco: str = ""
    cidade: str = ""
    uf: str = ""
    cep: str = ""
    telefone: str = ""
    email: str = ""
    site: str = ""
    cnaes: list[str] = Field(default_factory=list)
    representante: Representante = Field(default_factory=Representante)
    banco: dict[str, str] = Field(default_factory=dict)
    descricao_empresa: str = ""
    servicos: list[str] = Field(default_factory=list)
    produtos_saas: list[ProdutoSaaS] = Field(default_factory=list)
    metodologia: str = ""
    equipe: list[dict[str, Any]] = Field(default_factory=list)
    diferenciais: list[str] = Field(default_factory=list)
    clientes_referencia: list[str] = Field(default_factory=list)
    validade_proposta_dias: int = 60


class PerfilHora(BaseModel):
    perfil: str
    valor_hora: float


class Precos(BaseModel):
    perfis_hora: list[PerfilHora] = Field(default_factory=list)
    valor_hora_padrao: float = 150.0
    margem_minima: float = 0.20
    desconto_alvo_vs_estimado: float = 0.10  # preço "justo": abaixo do estimado do órgão, sem dumping
    desconto_maximo_vs_estimado: float = 0.30
    valor_maximo_proposta: float = 5_000_000.0
    valor_minimo_oportunidade: float = 0.0
    preco_ponto_funcao: float = 750.0  # R$ por ponto de função (custo; vira ~R$ 1.070 com margem e impostos)
    preco_ust: float = 90.0  # R$ por UST/hora de serviço técnico (custo)
    preco_saas_usuario_mes: float = 50.0
    preco_saas_minimo_mensal: float = 1000.0
    horas_padrao_por_item_sem_estimativa: float = 160.0
    fator_impostos: float = 0.16  # ISS + PIS/COFINS + IRPJ/CSLL aproximado (Lucro Presumido)


class PerfilInteresse(BaseModel):
    """O que a empresa busca e evita, em linguagem natural. Vai para os prompts de triagem.

    `buscamos`/`evitamos` são texto livre escrito pelo dono da empresa. `pre_triagem_ia` liga a
    classificação por IA do objeto do edital antes de baixar qualquer PDF; com ela ligada, as
    palavras positivas deixam de ser obrigatórias (as negativas continuam como corte barato).
    """

    buscamos: str = ""
    evitamos: str = ""
    pre_triagem_ia: bool = False
    lote_pre_triagem: int = 40
    maximo_por_rodada: int = 400

    @property
    def ativo(self) -> bool:
        return bool(self.buscamos.strip() or self.evitamos.strip())


class Triagem(BaseModel):
    perfil: PerfilInteresse = Field(default_factory=PerfilInteresse)
    termos_busca: list[str] = Field(default_factory=lambda: ["software", "sistema", "desenvolvimento de sistema"])
    palavras_negativas: list[str] = Field(default_factory=list)
    palavras_positivas: list[str] = Field(default_factory=list)  # vazio = não exige
    modalidades_aceitas: list[int] = Field(default_factory=lambda: [6, 4, 8, 12])
    ufs: list[str] = Field(default_factory=list)  # vazio = todas
    esferas: list[str] = Field(default_factory=list)  # vazio = todas (Federal, Estadual, Municipal)
    prazo_minimo_dias: int = 3
    score_minimo: int = 60
    score_zona_cinzenta: int = 40
    categorias_aceitas: list[str] = Field(default_factory=lambda: ["desenvolvimento", "licenciamento_saas", "manutencao"])
    max_paginas_por_termo: int = 50


class Notificacoes(BaseModel):
    """config/notificacoes.yaml: preferências de envio, editáveis pelo painel.

    Quando um campo aqui está preenchido, ele vence o equivalente do .env. Isso mantém os
    segredos no .env e as preferências do dia a dia num arquivo que o painel pode reescrever.
    """

    destinatarios: list[str] = Field(default_factory=list)
    limite_diario_envios: int | None = None
    hora_resumo_diario: int | None = None


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def load_empresa(settings: Settings | None = None) -> Empresa:
    s = settings or get_settings()
    return Empresa.model_validate(_load_yaml(s.config_path / "empresa.yaml"))


def load_precos(settings: Settings | None = None) -> Precos:
    s = settings or get_settings()
    return Precos.model_validate(_load_yaml(s.config_path / "precos.yaml"))


def load_triagem(settings: Settings | None = None) -> Triagem:
    s = settings or get_settings()
    return Triagem.model_validate(_load_yaml(s.config_path / "triagem.yaml"))


def load_selectors(portal: str = "comprasgov", settings: Settings | None = None) -> dict[str, Any]:
    s = settings or get_settings()
    return _load_yaml(s.config_path / "selectors" / f"{portal}.yaml")


def load_notificacoes(settings: Settings | None = None) -> Notificacoes:
    s = settings or get_settings()
    return Notificacoes.model_validate(_load_yaml(s.config_path / "notificacoes.yaml"))


def destinatarios(settings: Settings | None = None) -> list[str]:
    """E-mails que recebem avisos, resumos e pedidos de aprovação.

    Usa a lista de config/notificacoes.yaml; se estiver vazia, cai para o OWNER_EMAIL do .env.
    """
    s = settings or get_settings()
    lista = [e.strip() for e in load_notificacoes(s).destinatarios if e and e.strip()]
    if lista:
        return lista
    return [s.owner_email.strip()] if s.owner_email.strip() else []


def limite_diario(settings: Settings | None = None) -> int:
    s = settings or get_settings()
    v = load_notificacoes(s).limite_diario_envios
    return v if v is not None else s.limite_diario_envios


def hora_resumo(settings: Settings | None = None) -> int:
    s = settings or get_settings()
    v = load_notificacoes(s).hora_resumo_diario
    return v if v is not None else s.hora_resumo_diario
