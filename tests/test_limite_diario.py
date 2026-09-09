from datetime import datetime

import pytest

from licitabot.db.models import Oportunidade
from licitabot.db.session import db_session, log_evento
from licitabot.pipeline.notify import envios_hoje, limite_diario_atingido
from licitabot.pipeline.runner import Adiar, _gate


def test_envios_hoje_conta_apenas_hoje():
    hoje = datetime.now().strftime("%Y-%m-%d")
    with db_session() as s:
        op = Oportunidade(numero_controle_pncp="lim-1", orgao_cnpj="1", ano=2026, sequencial=1)
        s.add(op)
        s.flush()
        antes = envios_hoje(s)
        log_evento(s, "email_oportunidade", "x", op.id, dia=hoje, canal="informativo")
        log_evento(s, "email_oportunidade", "y", op.id, dia="2000-01-01", canal="informativo")
        s.commit()
        assert envios_hoje(s) == antes + 1


def test_gate_adia_quando_limite_atingido(monkeypatch):
    # O limite efetivo vem de config/notificacoes.yaml (editável no painel) e só cai
    # para o .env quando o YAML não define nada. Aqui forçamos o valor já resolvido.
    monkeypatch.setattr("licitabot.pipeline.notify.limite_diario", lambda: 0)
    monkeypatch.setattr("licitabot.pipeline.runner.limite_diario", lambda: 0)
    assert limite_diario_atingido()
    with db_session() as s:
        op = Oportunidade(numero_controle_pncp="lim-2", orgao_cnpj="1", ano=2026, sequencial=2, portal="comprasgov")
        s.add(op)
        s.commit()
        with pytest.raises(Adiar):
            _gate(s, op, "notify")
