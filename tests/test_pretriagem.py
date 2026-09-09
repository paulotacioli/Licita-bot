"""Pré-triagem por IA a partir do objeto: lote, aplicação dos vereditos e fila do pipeline."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlmodel import select

from licitabot.db.models import Oportunidade
from licitabot.db.session import db_session
from licitabot.llm.schemas import PreTriagemItem, PreTriagemLote
from licitabot.pipeline import pretriagem
from licitabot.pipeline.states import Status


class ClienteFalso:
    """Devolve um veredito por ref conforme o dicionário; refs ausentes ficam sem resposta."""

    def __init__(self, vereditos: dict[int, str], falhar: bool = False):
        self.vereditos = vereditos
        self.falhar = falhar
        self.prompts: list[str] = []

    def structured(self, model, schema, blocks, **kw):
        self.prompts.append(blocks[0]["text"])
        if self.falhar:
            raise RuntimeError("modelo indisponível")
        from licitabot.llm.claude_code import LLMResult

        itens = [PreTriagemItem(ref=r, veredito=v, motivo=f"motivo {v}") for r, v in self.vereditos.items()]
        return PreTriagemLote(itens=itens), LLMResult(tokens_in=10, tokens_out=5, model=model)


@pytest.fixture()
def perfil_ligado(monkeypatch):
    from licitabot.config import load_triagem

    tri = load_triagem()
    tri.perfil.pre_triagem_ia = True
    tri.perfil.buscamos = "software sob demanda, remoto"
    tri.perfil.evitamos = "equipe presencial"
    tri.perfil.lote_pre_triagem = 2
    monkeypatch.setattr(pretriagem, "load_triagem", lambda: tri)
    return tri


def _nova(controle: str, objeto: str) -> int:
    with db_session() as s:
        op = Oportunidade(
            numero_controle_pncp=controle, orgao_cnpj="1", ano=2026, sequencial=1, objeto=objeto,
            data_encerramento_proposta=datetime.now() + timedelta(days=10),
        )
        s.add(op)
        s.commit()
        return op.id


def _ler(oid: int) -> Oportunidade:
    with db_session() as s:
        op = s.get(Oportunidade, oid)
        s.expunge(op)
        return op


def test_desligada_nao_faz_nada(monkeypatch):
    from licitabot.config import load_triagem

    tri = load_triagem()
    tri.perfil.pre_triagem_ia = False
    monkeypatch.setattr(pretriagem, "load_triagem", lambda: tri)
    oid = _nova("pt-off", "qualquer coisa")
    assert pretriagem.pretriar_pendentes(client=ClienteFalso({oid: "irrelevante"}))["avaliadas"] == 0
    assert _ler(oid).status == Status.DESCOBERTA


def test_aplica_vereditos_e_descarta_o_irrelevante(perfil_ligado):
    a = _nova("pt-a", "desenvolvimento de sistema web")
    b = _nova("pt-b", "aquisição de 40 toneladas de asfalto")
    c = _nova("pt-c", "serviços de tecnologia")
    cliente = ClienteFalso({a: "relevante", b: "irrelevante", c: "incerto"})
    stats = pretriagem.pretriar_pendentes(client=cliente)
    assert (stats["relevantes"], stats["descartadas"], stats["incertas"]) == (1, 1, 1)
    assert _ler(a).status == Status.DESCOBERTA and _ler(a).pre_triagem == "relevante"
    assert _ler(b).status == Status.DESCARTADA and "pré-triagem IA" in _ler(b).status_motivo
    assert _ler(c).status == Status.DESCOBERTA and _ler(c).pre_triagem == "incerto"


def test_o_prompt_leva_o_perfil_escrito_pelo_dono(perfil_ligado):
    oid = _nova("pt-prompt", "sistema de gestão")
    cliente = ClienteFalso({oid: "relevante"})
    pretriagem.pretriar_pendentes(client=cliente)
    # o banco é compartilhado entre os testes, então o item pode cair em qualquer lote
    com_o_item = [pr for pr in cliente.prompts if f"ref {oid}\n" in pr]
    assert com_o_item, "o item não entrou em nenhum lote"
    assert "software sob demanda, remoto" in com_o_item[0]
    assert "equipe presencial" in com_o_item[0]


def test_respeita_o_tamanho_do_lote(perfil_ligado):
    import math

    ids = [_nova(f"pt-lote-{i}", f"sistema {i}") for i in range(5)]
    with db_session() as s:
        pendentes = len(pretriagem.pendentes(s, 10_000))
    cliente = ClienteFalso({i: "relevante" for i in ids})
    pretriagem.pretriar_pendentes(client=cliente)
    assert len(cliente.prompts) == math.ceil(pendentes / 2)  # lotes de 2


def test_item_sem_resposta_fica_pendente_para_a_proxima_rodada(perfil_ligado):
    a = _nova("pt-resp-a", "sistema a")
    b = _nova("pt-resp-b", "sistema b")
    stats = pretriagem.pretriar_pendentes(client=ClienteFalso({a: "relevante"}))
    assert stats["sem_resposta"] >= 1
    assert _ler(b).pre_triagem == "" and _ler(b).status == Status.DESCOBERTA


def test_lote_com_erro_nao_derruba_a_rodada_nem_marca_erro(perfil_ligado):
    oid = _nova("pt-erro", "sistema x")
    stats = pretriagem.pretriar_pendentes(client=ClienteFalso({}, falhar=True))
    assert stats["lotes_com_erro"] >= 1
    op = _ler(oid)
    assert op.status == Status.DESCOBERTA and op.tentativas == 0


def test_ja_avaliada_nao_entra_de_novo(perfil_ligado):
    oid = _nova("pt-idem", "sistema y")
    pretriagem.pretriar_pendentes(client=ClienteFalso({oid: "relevante"}))
    cliente = ClienteFalso({oid: "irrelevante"})
    pretriagem.pretriar_pendentes(client=cliente)
    with db_session() as s:
        pend = [o.id for o in s.exec(select(Oportunidade).where(Oportunidade.pre_triagem == "", Oportunidade.id == oid))]
    assert pend == [] and _ler(oid).pre_triagem == "relevante"


def test_migracao_acrescenta_colunas_em_banco_antigo(tmp_path):
    import sqlite3

    from sqlalchemy import create_engine

    from licitabot.db.session import _acrescentar_colunas_novas

    caminho = tmp_path / "antigo.db"
    con = sqlite3.connect(caminho)
    con.execute("CREATE TABLE oportunidade (id INTEGER PRIMARY KEY, numero_controle_pncp TEXT, orgao_cnpj TEXT, ano INTEGER, sequencial INTEGER, status TEXT)")
    con.commit()
    con.close()
    engine = create_engine(f"sqlite:///{caminho}")
    adicionadas = _acrescentar_colunas_novas(engine)
    assert "oportunidade.pre_triagem" in adicionadas and "oportunidade.pre_triagem_motivo" in adicionadas
    cols = {r[1] for r in sqlite3.connect(caminho).execute("PRAGMA table_info(oportunidade)")}
    assert {"pre_triagem", "pre_triagem_motivo", "objeto", "portal"} <= cols
