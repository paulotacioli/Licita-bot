from datetime import datetime

from licitabot.pncp.schemas import SearchItem, parse_dt


def test_search_item_props():
    it = SearchItem(item_url="/compras/63762009000150/2026/67", modalidade_licitacao_id="6", data_fim_vigencia="2026-09-10T10:00")
    assert it.cnpj == "63762009000150"
    assert it.ano_int == 2026
    assert it.seq_int == 67
    assert it.modalidade_id == 6
    assert it.prazo_proposta == datetime(2026, 9, 10, 10, 0)


def test_parse_dt_variants():
    assert parse_dt("2026-08-21T12:10:34.480734") == datetime(2026, 8, 21, 12, 10, 34, 480734)
    assert parse_dt("2026-08-21T12:10:14") == datetime(2026, 8, 21, 12, 10, 14)
    assert parse_dt("2026-08-21") == datetime(2026, 8, 21)
    assert parse_dt(None) is None
    assert parse_dt("lixo") is None
