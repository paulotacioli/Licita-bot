"""Checklist de premissas: números, prazos e cruzamento com o cofre."""

from datetime import datetime, timedelta

from licitabot.db.models import DocumentoCofre, Oportunidade
from licitabot.db.session import db_session
from licitabot.llm.schemas import DocumentoExigido, EditalRequisitos, ExigenciaAtestado, QualificacaoEconomica, QualificacaoTecnica
from licitabot.pipeline import premissas


def _op(**kw):
    base = dict(numero_controle_pncp=f"t-{datetime.now().timestamp()}", orgao_cnpj="1", ano=2026, sequencial=1, orgao_nome="Órgão",
                objeto="Sistema", valor_estimado=100_000.0, portal="comprasgov",
                data_encerramento_proposta=datetime.now() + timedelta(days=10))
    base.update(kw)
    return Oportunidade(**base)


def _req(**kw):
    base = dict(objeto_resumido="x", exige_sicaf=True, beneficio_me_epp="exclusivo",
                documentos_exigidos=[
                    DocumentoExigido(tipo="cnd_federal", descricao_literal="RFB/PGFN", referencia="8.3"),
                    DocumentoExigido(tipo="proposta_comercial", descricao_literal="proposta", fase="proposta"),
                    DocumentoExigido(tipo="outro", descricao_literal="declaração exótica", obrigatorio=False),
                ],
                qualificacao_tecnica=QualificacaoTecnica(atestados=ExigenciaAtestado(quantidade_minima=1)),
                qualificacao_economica=QualificacaoEconomica(exige_balanco=True, patrimonio_liquido_minimo_percentual=10, exige_certidao_falencia=True))
    base.update(kw)
    return EditalRequisitos(**base)


def test_dias_uteis_pula_fim_de_semana():
    # terça 15/09/2026 -> quinta 10/09; segunda 21/09 -> quarta 16/09
    assert premissas.dias_uteis_antes(datetime(2026, 9, 15, 10), 3).date() == datetime(2026, 9, 10).date()
    assert premissas.dias_uteis_antes(datetime(2026, 9, 21, 10), 3).date() == datetime(2026, 9, 16).date()


def test_numeros_e_prazos_com_analise():
    with db_session() as s:
        ck = premissas.montar(s, _op(), _req())
    rot = {n[0]: n[1] for n in ck.numeros}
    assert rot["Valor estimado pelo órgão"] == "R$ 100.000,00"
    assert rot["Piso de inexequibilidade"] == "R$ 50.000,00"
    assert rot["Patrimônio líquido mínimo"] == "R$ 10.000,00"
    assert any("SICAF" in p[0] for p in ck.prazos)
    assert ck.completo


def test_cofre_marca_ok_e_pendente():
    with db_session() as s:
        s.add(DocumentoCofre(tipo="cnd_federal", titulo="CND", valido_ate=datetime.now() + timedelta(days=30)))
        s.add(DocumentoCofre(tipo="certidao_falencia", titulo="vencida", valido_ate=datetime.now() - timedelta(days=1)))
        s.commit()
        ck = premissas.montar(s, _op(), _req())
        por = {i.titulo: i.status for i in ck.itens}
        assert por["Certidão RFB/PGFN"] == "ok"
        assert por["Certidão negativa de falência"] == "pendente"  # venceu ontem
        assert por["Balanço patrimonial e DRE"] == "pendente"
        assert por["Proposta comercial"] == "robo"
        assert por["Outro documento"] == "verificar"
        assert ck.resumo["pendente"] >= 2
        for d in s.exec(__import__("sqlmodel").select(DocumentoCofre)).all():
            s.delete(d)
        s.commit()


def test_exclusivo_me_epp_alerta_quando_porte_nao_bate(monkeypatch):
    from licitabot.config import Empresa

    monkeypatch.setattr(premissas, "load_empresa", lambda: Empresa(porte="DEMAIS"))
    with db_session() as s:
        ck = premissas.montar(s, _op(), _req())
    assert any(i.status == "atencao" and "exclusiva" in i.titulo.lower() for i in ck.itens)


def test_sicaf_prazo_passado_vira_alerta():
    with db_session() as s:
        ck = premissas.montar(s, _op(data_encerramento_proposta=datetime.now() + timedelta(days=1)), _req())
    assert any("SICAF" in a for a in ck.alertas)


def test_sem_analise_fallback():
    with db_session() as s:
        ck = premissas.montar(s, _op(valor_estimado=None), None)
    assert not ck.completo
    assert ck.itens[0].status == "verificar"
    assert ck.numeros[0][1] == "não informado no PNCP"


def test_para_dict_serializa():
    with db_session() as s:
        d = premissas.para_dict(premissas.montar(s, _op(), _req()))
    assert set(d) == {"completo", "numeros", "prazos", "itens", "alertas", "resumo"}
    assert d["itens"][0]["status"] in ("ok", "pendente", "atencao", "verificar", "robo")


def test_completar_valores_usa_o_detalhe_e_cai_para_os_itens(monkeypatch):
    """Sem valor no detalhe, a soma dos itens vale; e o detalhe corrige o objeto truncado da busca."""
    from licitabot.db.models import Oportunidade
    from licitabot.pipeline import valores
    from licitabot.pipeline.states import Status

    with db_session() as s:
        a = Oportunidade(numero_controle_pncp="v-1", orgao_cnpj="1", ano=2026, sequencial=1,
                         status=Status.DESCOBERTA, pre_triagem="relevante", objeto="Objeto curto")
        b = Oportunidade(numero_controle_pncp="v-2", orgao_cnpj="2", ano=2026, sequencial=2,
                         status=Status.DESCOBERTA, pre_triagem="incerto", objeto="Outro")
        s.add(a), s.add(b)
        s.commit()
        ids = (a.id, b.id)

    class ClienteFake:
        def __enter__(self):
            return self

        def __exit__(self, *e):
            return False

        def compra_detalhe(self, cnpj, ano, seq):
            if cnpj == "1":
                return {"valorTotalEstimado": 120000.0, "objetoCompra": "Objeto curto, agora completo e bem maior"}
            return {"valorTotalEstimado": None}

        def compra_itens(self, cnpj, ano, seq):
            return [{"valorTotal": 300.0}, {"valorTotal": 700.0}]

    monkeypatch.setattr(valores, "PNCPClient", ClienteFake)
    stats = valores.completar(limite=10)
    assert stats["consultadas"] == 2 and stats["com_valor"] == 2
    with db_session() as s:
        a, b = s.get(Oportunidade, ids[0]), s.get(Oportunidade, ids[1])
        assert a.valor_estimado == 120000.0 and "bem maior" in a.objeto
        assert b.valor_estimado == 1000.0  # soma dos itens
        for o in (a, b):
            s.delete(o)
        s.commit()


def test_valores_nao_reconsulta_no_mesmo_dia():
    """Órgão que não publica valor não pode ser consultado a cada meia hora para sempre."""
    from datetime import datetime, timedelta

    from licitabot.db.models import Oportunidade
    from licitabot.pipeline import valores
    from licitabot.pipeline.states import Status

    with db_session() as s:
        agora = Oportunidade(numero_controle_pncp="v-3", orgao_cnpj="3", ano=2026, sequencial=3, status=Status.DESCOBERTA,
                             pre_triagem="relevante", valor_consultado_em=datetime.now() - timedelta(hours=2))
        antiga = Oportunidade(numero_controle_pncp="v-4", orgao_cnpj="4", ano=2026, sequencial=4, status=Status.DESCOBERTA,
                              pre_triagem="relevante", valor_consultado_em=datetime.now() - timedelta(hours=30))
        s.add(agora), s.add(antiga)
        s.commit()
        pend = {o.numero_controle_pncp for o in valores.pendentes(s, 50)}
        assert "v-4" in pend and "v-3" not in pend
        for o in (agora, antiga):
            s.delete(o)
        s.commit()
