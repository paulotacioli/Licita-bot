from datetime import datetime, timedelta

from licitabot.db.models import Oportunidade
from licitabot.pipeline.notify import _env, _fmt_brl


def _op(**kw):
    base = dict(id=1, numero_controle_pncp="x-1", orgao_cnpj="1", ano=2026, sequencial=1, orgao_nome="Órgão", esfera="Federal", uf="DF",
                modalidade_nome="Pregão - Eletrônico", objeto="Sistema", pasta="C:/tmp/1", status="ANALISADA",
                data_encerramento_proposta=datetime.now() + timedelta(days=3), valor_estimado=1000.0)
    base.update(kw)
    return Oportunidade(**base)


def test_template_informativo_renderiza():
    html = _env().get_template("aprovacao.html.j2").render(
        op=_op(), emp=None, itens=[], docs=[], req=None, total="1,00", estimado="2,00", desconto="50%", horas_restantes="72 h",
        prazo="01/01", expira="", link_aprovar="", link_rejeitar="", dashboard="http://x", shots=[], fmt=_fmt_brl, dry_run=True,
        informativo=True, motivo_portal="onboarding incompleto", link_pncp="https://pncp.gov.br",
    )
    assert "Licitação compatível" in html and "APROVAR" not in html


def test_template_resumo_diario_renderiza():
    html = _env().get_template("resumo_diario.html.j2").render(
        data="08/09/2026", novas=[_op()], relevantes=[], fila=3, erros=0, enviados_ontem=2, limite=50, ok_onb=False,
        faltando=["e-CNPJ"], fmt=_fmt_brl, dashboard="http://x", pncp=lambda o: "https://pncp.gov.br",
    )
    assert "Resumo" in html and "e-CNPJ" in html and "Órgão" in html
