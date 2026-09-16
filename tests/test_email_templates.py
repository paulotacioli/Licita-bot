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


def test_template_compativel_renderiza():
    ck = {"completo": True, "alertas": ["O prazo do SICAF já passou."],
          "numeros": [{"rotulo": "Valor estimado", "valor": "R$ 1.000,00", "obs": ""}],
          "prazos": [{"rotulo": "SICAF", "valor": "10/09/2026", "obs": "PRAZO JÁ PASSOU"}],
          "itens": [{"titulo": "Balanço", "detalhe": "", "status": "pendente", "fonte": "8.5", "fase": "habilitação"}],
          "resumo": {"ok": 0, "pendente": 1, "atencao": 0, "verificar": 0, "robo": 0}}
    html = _env().get_template("compativel.html.j2").render(op=_op(), req=None, ck=ck, fmt=_fmt_brl, dashboard="http://x/oportunidades/1", link_pncp="https://pncp.gov.br")
    assert "FALTA" in html and "Balanço" in html and "SICAF já passou" in html and "http://x/oportunidades/1" in html


def test_template_resumo_diario_mostra_pendencias():
    html = _env().get_template("resumo_diario.html.j2").render(
        data="08/09/2026", novas=[_op()], relevantes=[_op()], fila=3, erros=0, enviados_ontem=2, limite=50, ok_onb=True,
        faltando=[], fmt=_fmt_brl, dashboard="http://x/", pncp=lambda o: "https://pncp.gov.br",
        premissas={1: {"pendentes": ["Balanço patrimonial e DRE"], "n": 1, "completo": True}},
    )
    assert "1 pendência(s)" in html and "Falta: Balanço" in html
