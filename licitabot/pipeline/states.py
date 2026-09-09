"""Máquina de estados de uma oportunidade."""

from __future__ import annotations

from enum import StrEnum


class Status(StrEnum):
    DESCOBERTA = "DESCOBERTA"
    BAIXADA = "BAIXADA"
    TRIADA_RELEVANTE = "TRIADA_RELEVANTE"
    ZONA_CINZENTA = "ZONA_CINZENTA"
    DESCARTADA = "DESCARTADA"
    ANALISADA = "ANALISADA"
    PRECIFICADA = "PRECIFICADA"
    DOCS_GERADOS = "DOCS_GERADOS"
    PREPARADA_PORTAL = "PREPARADA_PORTAL"
    AGUARDANDO_APROVACAO = "AGUARDANDO_APROVACAO"
    APROVADA = "APROVADA"
    REJEITADA = "REJEITADA"
    EXPIRADA = "EXPIRADA"
    ENVIADA = "ENVIADA"
    EM_SESSAO = "EM_SESSAO"
    ENCERRADA = "ENCERRADA"
    BLOQUEADA = "BLOQUEADA"
    ERRO = "ERRO"


# Ordem "feliz" do pipeline. Usada para saber qual etapa roda a seguir.
PIPELINE_ORDER: list[Status] = [
    Status.DESCOBERTA,
    Status.BAIXADA,
    Status.TRIADA_RELEVANTE,
    Status.ANALISADA,
    Status.PRECIFICADA,
    Status.DOCS_GERADOS,
    Status.PREPARADA_PORTAL,
    Status.AGUARDANDO_APROVACAO,
    Status.APROVADA,
    Status.ENVIADA,
    Status.EM_SESSAO,
    Status.ENCERRADA,
]

# Etapa (nome) -> status de entrada que ela consome e status de saída em sucesso.
STEPS: dict[str, tuple[Status, Status]] = {
    "ingest": (Status.DESCOBERTA, Status.BAIXADA),
    "triage": (Status.BAIXADA, Status.TRIADA_RELEVANTE),
    "analysis": (Status.TRIADA_RELEVANTE, Status.ANALISADA),
    "pricing": (Status.ANALISADA, Status.PRECIFICADA),
    "docgen": (Status.PRECIFICADA, Status.DOCS_GERADOS),
    "prepare": (Status.DOCS_GERADOS, Status.PREPARADA_PORTAL),
    "notify": (Status.PREPARADA_PORTAL, Status.AGUARDANDO_APROVACAO),
    "submit": (Status.APROVADA, Status.ENVIADA),
    "monitor": (Status.ENVIADA, Status.EM_SESSAO),
}

TERMINAL: set[Status] = {Status.DESCARTADA, Status.REJEITADA, Status.EXPIRADA, Status.ENCERRADA}


def next_step_for(status: Status) -> str | None:
    """Nome da próxima etapa automática a partir de um status, ou None se depende de humano/terminal."""
    for name, (entrada, _saida) in STEPS.items():
        if entrada == status:
            return name
    return None
