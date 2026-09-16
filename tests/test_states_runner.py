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


def test_run_step_analysis_dispara_email_de_compativel(monkeypatch):
    """Depois de uma análise bem-sucedida o runner tenta o e-mail de compatível; falha ali não derruba a etapa."""
    from datetime import datetime, timedelta

    from licitabot.db.models import Oportunidade
    from licitabot.db.session import db_session, set_status
    from licitabot.pipeline import runner
    from licitabot.pipeline.states import Status

    with db_session() as s:
        op = Oportunidade(numero_controle_pncp=f"rs-{datetime.now().timestamp()}", orgao_cnpj="1", ano=2026, sequencial=1,
                          status=Status.TRIADA_RELEVANTE, data_encerramento_proposta=datetime.now() + timedelta(days=5))
        s.add(op)
        s.commit()
        oid = op.id

    def analyze_fake(oid_):
        with db_session() as s:
            set_status(s, s.get(Oportunidade, oid_), Status.ANALISADA, "fake")
            s.commit()

    chamados = []
    monkeypatch.setattr(runner, "_fn", lambda step: analyze_fake)
    import licitabot.pipeline.notify as notify

    def falha(oid_, forcar=False):
        chamados.append(oid_)
        raise RuntimeError("smtp fora")

    monkeypatch.setattr(notify, "send_match_email", falha)
    assert runner.run_step(oid, "analysis") is True
    assert chamados == [oid]
