from datetime import datetime, timedelta

import pytest

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


def _resumo(**kw):
    base = dict(data="08/09/2026", novas=[_op()], prioritarias=[], demais=[_op()], total_novas="R$ 1.000,00",
                total_prioritarias="R$ 0,00", sem_valor=0, relevantes=[], fila=3, erros=0, enviados_ontem=2,
                limite=50, ok_onb=False, faltando=["e-CNPJ"], fmt=_fmt_brl, dashboard="http://x/",
                pncp=lambda o: "https://pncp.gov.br", premissas={}, obj=lambda o: o.objeto,
                exige_ok=True, liberar=lambda o: "http://x/liberar/t",
                meses=lambda t: 12, por_mes=lambda o: "R$ 83,33")
    base.update(kw)
    return _env().get_template("resumo_diario.html.j2").render(**base)


def test_template_resumo_diario_renderiza():
    html = _resumo()
    assert "Resumo" in html and "e-CNPJ" in html and "Órgão" in html and "R$ 1.000,00" in html


def test_template_resumo_diario_destaca_prioritarias():
    prio = _op(id=9, objeto="Captura de NF-e e CT-e em nuvem")
    html = _resumo(prioritarias=[prio], demais=[], novas=[prio], total_prioritarias="R$ 1.000,00")
    assert "Prioridade" in html and "NF-e" in html


def test_template_resumo_diario_mostra_pendencias():
    html = _resumo(premissas={1: {"pendentes": ["Balanço patrimonial e DRE"], "n": 1, "completo": True}})
    assert "1 pendência(s)" in html and "Balanço" in html


def test_separar_prioritarias_usa_a_lista_do_yaml():
    from licitabot.pipeline.notify import separar_prioritarias

    prio, resto = separar_prioritarias([_op(objeto="Guarda em nuvem de CT-e e MDF-e"), _op(objeto="Fábrica de software")])
    assert len(prio) == 1 and "CT-e" in prio[0].objeto
    assert len(resto) == 1


def test_somente_resumo_bloqueia_email_avulso(monkeypatch):
    """Com 'um e-mail por dia' ligado, a licitação nova não gera e-mail próprio — entra no resumo."""
    from licitabot.pipeline import notify

    monkeypatch.setattr(notify, "somente_resumo", lambda: True)
    monkeypatch.setattr(notify, "destinatarios", lambda: ["a@b.com"])
    monkeypatch.setattr(notify, "enviar_email", lambda *a, **k: pytest.fail("não deveria enviar"))
    assert notify.send_match_email(1) is False


def test_limpar_objeto_tira_preambulo_e_corta_na_palavra():
    from licitabot.pipeline.texto import limpar_objeto

    t = limpar_objeto("Contratação de empresa especializada para a prestação de serviços de captura e guarda "
                      "de documentos fiscais eletrônicos (NF-e, CT-e) em nuvem, com suporte técnico", 120)
    assert t.startswith("Captura e guarda")
    assert "Contratação de empresa" not in t  # sem o preâmbulo, o texto inteiro coube no limite
    assert len(t) <= 120 and not t.endswith("…")
    curto = limpar_objeto("Contratação de empresa especializada para a prestação de serviços de captura e guarda "
                          "de documentos fiscais eletrônicos (NF-e, CT-e) em nuvem, com suporte técnico", 40)
    assert curto.endswith("…") and len(curto) <= 41


def test_limpar_objeto_preserva_texto_curto_e_tira_etiqueta_do_portal():
    from licitabot.pipeline.texto import limpar_objeto

    assert limpar_objeto("[Portal de Compras Públicas] - Sistema de gestão de saúde") == "Sistema de gestão de saúde"
    assert limpar_objeto("Aquisição de software") == "Aquisição de software"  # cortar deixaria sem sentido


def test_resumo_diario_mostra_objeto_limpo_e_motivo_da_ia():
    from licitabot.pipeline.texto import limpar_objeto

    op = _op(objeto="Contratação de empresa especializada para prestação de serviços de guarda de CT-e em nuvem",
             pre_triagem_motivo="Guarda de CT-e em nuvem — encaixa no perfil.")
    html = _resumo(novas=[op], demais=[op], obj=lambda o: limpar_objeto(o.objeto, 300))
    assert "Guarda de CT-e em nuvem — encaixa no perfil." in html
    assert "Contratação de empresa especializada" not in html


def test_resumo_mostra_valor_mensal_quando_o_objeto_diz_o_prazo():
    op = _op(objeto="Guarda de CT-e em nuvem pelo prazo de 12 (doze) meses", valor_estimado=1000.0)
    html = _resumo(novas=[op], demais=[op])
    assert "R$ 83,33/mês" in html and "total de 12 meses" in html


def test_resumo_poe_a_frase_da_ia_antes_do_objeto():
    op = _op(objeto="Contratação de empresa para guarda de CT-e", pre_triagem_motivo="Guarda de CT-e em nuvem.")
    html = _resumo(novas=[op], demais=[op])
    assert html.index("Guarda de CT-e em nuvem.") < html.index("Contratação de empresa para guarda")


def test_periodo_meses_ignora_texto_sem_prazo():
    from licitabot.pipeline.texto import periodo_meses, valor_por_mes

    assert periodo_meses("Locação de sistema em nuvem") is None
    assert valor_por_mes(1200.0, None) is None
    assert valor_por_mes(1200.0, 1) is None  # contrato de um mês: o total já é o mensal
    assert valor_por_mes(1200.0, 12) == 100.0
