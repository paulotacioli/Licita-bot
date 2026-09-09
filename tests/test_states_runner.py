from datetime import datetime, timedelta

from sqlmodel import select

from licitabot.db.models import Oportunidade
from licitabot.db.session import db_session, set_status
from licitabot.pipeline.runner import _gate, run_step
from licitabot.pipeline.states import Status, next_step_for


def test_next_step():
    assert next_step_for(Status.DESCOBERTA) == "ingest"
    assert next_step_for(Status.PRECIFICADA) == "docgen"
    assert next_step_for(Status.AGUARDANDO_APROVACAO) is None
    assert next_step_for(Status.APROVADA) == "submit"


def test_gate_prazo_encerrado_e_portal():
    with db_session() as session:
        op = Oportunidade(numero_controle_pncp="t-1", orgao_cnpj="1", ano=2026, sequencial=1, data_encerramento_proposta=datetime.now() - timedelta(hours=1))
        session.add(op)
        session.commit()
        assert "encerrado" in _gate(session, op, "triage")
        op.data_encerramento_proposta = datetime.now() + timedelta(days=5)
        op.portal = "licitanet"
        assert "não suportado" in _gate(session, op, "prepare")


def test_run_step_status_errado_nao_roda():
    with db_session() as session:
        op = Oportunidade(numero_controle_pncp="t-2", orgao_cnpj="1", ano=2026, sequencial=2, status=Status.DESCARTADA)
        session.add(op)
        session.commit()
        oid = op.id
    assert run_step(oid, "triage") is False
