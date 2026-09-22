from datetime import datetime, timedelta

import pytest
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
        op.liberado_gerar = True  # sem a liberação o portão para antes, na espera pelo OK do dono
        assert "não suportado" in _gate(session, op, "prepare")


def test_gate_espera_liberacao_antes_de_precificar_e_gerar():
    """Sem o OK do dono, nada de precificar nem gerar documento: fica na fila, sem erro e sem bloqueio."""
    from licitabot.pipeline.runner import Adiar

    with db_session() as session:
        op = Oportunidade(numero_controle_pncp="t-lib", orgao_cnpj="1", ano=2026, sequencial=1,
                          portal="comprasgov", data_encerramento_proposta=datetime.now() + timedelta(days=5))
        session.add(op)
        session.commit()
        for etapa in ("pricing", "docgen"):
            with pytest.raises(Adiar, match="liberação"):
                _gate(session, op, etapa)
        assert _gate(session, op, "triage") is None  # triagem e análise seguem livres
        op.liberado_gerar = True
        assert _gate(session, op, "pricing") is None


def test_liberacao_por_token_libera_uma_vez_e_descarte_encerra():
    from licitabot.approval.liberacao import aplicar, decidir
    from licitabot.approval.tokens import gerar_token_liberacao

    with db_session() as session:
        op = Oportunidade(numero_controle_pncp="t-lib2", orgao_cnpj="1", ano=2026, sequencial=1,
                          data_encerramento_proposta=datetime.now() + timedelta(days=5))
        session.add(op)
        session.commit()
        oid = op.id

    ok, msg = aplicar(gerar_token_liberacao(oid, "liberar"))
    assert ok
    with db_session() as session:
        assert session.get(Oportunidade, oid).liberado_gerar

    assert aplicar("token-falsificado")[0] is False

    ok, _ = decidir(oid, False, "teste")
    with db_session() as session:
        assert ok and session.get(Oportunidade, oid).status == "DESCARTADA"


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
