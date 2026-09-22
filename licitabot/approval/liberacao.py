"""Liberação para preparar a proposta: o robô só gasta tempo e IA depois de um OK explícito.

O fluxo que o dono pediu: o robô descobre, filtra e manda por e-mail; ele lê, e só as licitações em
que quer participar recebem o OK. A partir daí o pipeline segue (precificação, documentos, portal).
O link do e-mail abre uma página de confirmação — nada acontece só por abrir o e-mail.
"""

from __future__ import annotations

import logging
from datetime import datetime

from licitabot.approval.tokens import gerar_token_liberacao, validar_token_liberacao
from licitabot.db.models import Oportunidade
from licitabot.db.session import db_session, log_evento, set_status
from licitabot.pipeline.states import Status

log = logging.getLogger(__name__)


def link_liberar(base: str, oportunidade_id: int) -> str:
    return f"{base.rstrip('/')}/liberar/{gerar_token_liberacao(oportunidade_id, 'liberar')}" if base else ""


def link_descartar(base: str, oportunidade_id: int) -> str:
    return f"{base.rstrip('/')}/liberar/{gerar_token_liberacao(oportunidade_id, 'descartar')}" if base else ""


def descrever(token: str) -> dict | None:
    """Dados para a página de confirmação, ou None se o link não presta."""
    try:
        dados = validar_token_liberacao(token)
    except ValueError:
        return None
    with db_session() as session:
        op = session.get(Oportunidade, dados["o"])
        if not op:
            return None
        return {
            "oid": op.id,
            "acao": dados.get("acao", "liberar"),
            "orgao": op.orgao_nome,
            "objeto": op.objeto,
            "valor": op.valor_estimado,
            "prazo": op.data_encerramento_proposta.strftime("%d/%m/%Y %H:%M") if op.data_encerramento_proposta else "—",
            "ja_liberada": op.liberado_gerar,
            "status": op.status,
        }


def aplicar(token: str, quem: str = "e-mail") -> tuple[bool, str]:
    """Executa a decisão do link. Devolve (ok, mensagem para a tela)."""
    try:
        dados = validar_token_liberacao(token)
    except ValueError as e:
        return False, f"Link {e}."
    return decidir(dados["o"], dados.get("acao", "liberar") == "liberar", quem)


def decidir(oportunidade_id: int, liberar: bool, quem: str = "painel") -> tuple[bool, str]:
    with db_session() as session:
        op = session.get(Oportunidade, oportunidade_id)
        if not op:
            return False, "Licitação não encontrada."
        if not liberar:
            if op.status in (Status.ENVIADA, Status.APROVADA):
                return False, "Esta licitação já foi enviada; não dá para descartar por aqui."
            set_status(session, op, Status.DESCARTADA, f"descartada por {quem}: não vamos participar")
            session.commit()
            return True, "Certo, esta licitação foi descartada."
        if op.liberado_gerar:
            return True, "Esta licitação já estava liberada; o robô segue preparando."
        if op.data_encerramento_proposta and op.data_encerramento_proposta < datetime.now():
            return False, "O prazo de propostas desta licitação já encerrou."
        op.liberado_gerar = True
        op.liberado_em = datetime.now()
        op.liberado_por = quem
        session.add(op)
        if op.status == Status.DESCARTADA:
            set_status(session, op, Status.TRIADA_RELEVANTE, f"reaberta e liberada por {quem}")
        log_evento(session, "liberacao", f"liberada para preparar a proposta por {quem}", op.id)
        session.commit()
        log.info("%s liberada para gerar documentos (%s)", oportunidade_id, quem)
        return True, "Liberado. O robô vai precificar, gerar os documentos e avisar você antes de enviar."
