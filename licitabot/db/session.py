"""Engine SQLite e helpers de sessão/eventos."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine, select

from licitabot.config import get_settings
from licitabot.db import models  # noqa: F401  (registra as tabelas)
from licitabot.db.models import Evento, Oportunidade
from licitabot.pipeline.states import Status

_engine: Engine | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        s = get_settings()
        s.ensure_dirs()
        _engine = create_engine(f"sqlite:///{s.db_path}", connect_args={"check_same_thread": False, "timeout": 30})

        @event.listens_for(_engine, "connect")
        def _pragmas(dbapi_conn, _):  # pragma: no cover
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    return _engine


def init_db() -> None:
    SQLModel.metadata.create_all(get_engine())


@contextmanager
def db_session() -> Iterator[Session]:
    with Session(get_engine()) as session:
        yield session


def log_evento(session: Session, tipo: str, mensagem: str, oportunidade_id: int | None = None, **dados: Any) -> None:
    session.add(Evento(oportunidade_id=oportunidade_id, tipo=tipo, mensagem=mensagem, dados_json=dados))


def set_status(session: Session, op: Oportunidade, status: Status, motivo: str = "", erro: str = "") -> None:
    anterior = op.status
    op.status = status
    op.status_motivo = motivo
    if erro:
        op.erro_ultimo = erro
    op.atualizado_em = datetime.utcnow()
    session.add(op)
    log_evento(session, "status", f"{anterior} -> {status}: {motivo}"[:2000], op.id, de=anterior, para=str(status))


def get_oportunidade(session: Session, id_or_controle: int | str) -> Oportunidade | None:
    if isinstance(id_or_controle, int) or str(id_or_controle).isdigit():
        return session.get(Oportunidade, int(id_or_controle))
    return session.exec(select(Oportunidade).where(Oportunidade.numero_controle_pncp == id_or_controle)).first()
