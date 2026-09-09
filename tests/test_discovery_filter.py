from datetime import datetime, timedelta

from licitabot.pipeline.discovery import _filtro_local
from licitabot.pncp.schemas import SearchItem


def _item(**kw):
    base = dict(
        item_url="/compras/1/2026/1",
        modalidade_licitacao_id="6",
        data_fim_vigencia=(datetime.now() + timedelta(days=10)).strftime("%Y-%m-%dT%H:%M"),
        description="Contratação de empresa para desenvolvimento de sistema web",
        uf="SP",
        esfera_nome="Municipal",
    )
    base.update(kw)
    return SearchItem(**base)


def test_passa_filtro_basico():
    assert _filtro_local(_item(), datetime.now()) is None


def test_modalidade_rejeitada():
    assert "modalidade" in _filtro_local(_item(modalidade_licitacao_id="7"), datetime.now())


def test_prazo_curto():
    curto = (datetime.now() + timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M")
    assert "prazo" in _filtro_local(_item(data_fim_vigencia=curto), datetime.now())


def test_sem_palavra_positiva():
    assert "palavra-chave" in _filtro_local(_item(description="Aquisição de materiais hospitalares"), datetime.now())


def test_palavra_negativa():
    assert "negativa" in _filtro_local(_item(description="Aquisição de notebooks com sistema operacional"), datetime.now())
