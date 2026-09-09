"""Autenticação do painel, edição de configuração pelo formulário e destinatários múltiplos."""

from __future__ import annotations

import pytest

from licitabot.web import auth
from licitabot.web import config_service as cfg


# ---------- senha ----------


def test_hash_nao_guarda_a_senha_em_texto():
    h = auth.gerar_hash("segredo-forte-123")
    assert "segredo-forte-123" not in h
    assert h.startswith("pbkdf2_sha256$")


def test_hash_confere_a_senha_certa_e_recusa_a_errada():
    h = auth.gerar_hash("segredo-forte-123")
    assert auth.conferir_senha("segredo-forte-123", h)
    assert not auth.conferir_senha("segredo-forte-124", h)


def test_sal_aleatorio_gera_hashes_diferentes_para_a_mesma_senha():
    assert auth.gerar_hash("mesma-senha-123") != auth.gerar_hash("mesma-senha-123")


def test_hash_corrompido_nao_derruba_a_verificacao():
    for ruim in ("", "sem-cifrao", "pbkdf2_sha256$abc$def", "outro$1$aa$bb"):
        assert not auth.conferir_senha("qualquer", ruim)


@pytest.mark.parametrize(
    "senha,recusa",
    [("curta1", True), ("1234567890123", True), ("apenasletrasaqui", True), ("boaSenha123", False)],
)
def test_forca_da_senha(senha, recusa):
    assert (auth.forca_da_senha(senha) is not None) == recusa


# ---------- rotas públicas ----------


@pytest.mark.parametrize("caminho", ["/aprovar/abc", "/rejeitar/abc", "/decidir", "/login", "/healthz"])
def test_rotas_de_aprovacao_e_login_dispensam_sessao(caminho):
    assert auth.rota_publica(caminho)


@pytest.mark.parametrize("caminho", ["/", "/configuracoes/precos", "/onboarding", "/oportunidades/1", "/conta"])
def test_paginas_do_painel_exigem_sessao(caminho):
    assert not auth.rota_publica(caminho)


# ---------- conversão dos campos do formulário ----------


def test_percentual_vai_e_volta_sem_perder_valor():
    campo = cfg.Campo("x", "X", "percentual")
    valor, erro = cfg._converter(campo, "20")
    assert erro is None and valor == 0.2
    assert cfg.valor_para_form(campo, 0.2) == "20"


def test_dinheiro_aceita_formato_brasileiro():
    campo = cfg.Campo("x", "X", "dinheiro")
    assert cfg._converter(campo, "1.234,56")[0] == 1234.56
    assert cfg._converter(campo, "R$ 3000000")[0] == 3000000


def test_numero_invalido_vira_erro_legivel():
    campo = cfg.Campo("x", "Valor da hora", "dinheiro")
    valor, erro = cfg._converter(campo, "abc")
    assert valor is None and "Valor da hora" in erro


def test_limites_do_campo_sao_aplicados():
    campo = cfg.Campo("x", "Nota", "inteiro", minimo=0, maximo=100)
    assert cfg._converter(campo, "101")[1] is not None
    assert cfg._converter(campo, "-1")[1] is not None
    assert cfg._converter(campo, "60")[0] == 60


def test_lista_aceita_uma_por_linha_e_ignora_vazias():
    campo = cfg.Campo("x", "X", "lista")
    assert cfg._converter(campo, "um\n\n  dois  \n")[0] == ["um", "dois"]


def test_uf_e_normalizada_para_maiuscula():
    campo = cfg.Campo("ufs", "UFs", "lista")
    assert cfg._converter(campo, "sp\nrj")[0] == ["SP", "RJ"]


def test_email_invalido_e_recusado():
    assert cfg._valida_emails(["ok@example.com"]) is None
    assert cfg._valida_emails(["sem-arroba"]) is not None


def test_cnpj_e_cpf_conferem_a_quantidade_de_digitos():
    assert cfg._valida_cnpj("61.136.140/0001-86") is None
    assert cfg._valida_cnpj("") is None  # vazio é permitido; obrigatoriedade é outro controle
    assert cfg._valida_cnpj("123") is not None
    assert cfg._valida_cpf("123.456.789-09") is None
    assert cfg._valida_cpf("123") is not None


# ---------- gravação preservando comentários ----------


@pytest.fixture()
def config_temporaria(tmp_path, monkeypatch):
    arquivo = tmp_path / "precos.yaml"
    arquivo.write_text(
        "# Regras de precificação. Este comentário precisa sobreviver.\n"
        "valor_hora_padrao: 160.0\n"
        "margem_minima: 0.20  # margem sobre o custo\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cfg, "caminho", lambda nome: tmp_path / nome)
    return arquivo


def test_salvar_preserva_comentarios_do_arquivo(config_temporaria):
    secao = cfg.Secao("precos", "Preços", "precos.yaml", campos=[
        cfg.Campo("valor_hora_padrao", "Valor da hora", "dinheiro"),
        cfg.Campo("margem_minima", "Margem", "percentual"),
    ])
    erros = cfg.salvar(secao, {"valor_hora_padrao": "180", "margem_minima": "25"})
    assert erros == []
    texto = config_temporaria.read_text(encoding="utf-8")
    assert "Este comentário precisa sobreviver" in texto
    assert "# margem sobre o custo" in texto
    assert "180" in texto and "0.25" in texto


def test_formulario_invalido_nao_grava_nada(config_temporaria):
    antes = config_temporaria.read_text(encoding="utf-8")
    secao = cfg.Secao("precos", "Preços", "precos.yaml", campos=[
        cfg.Campo("valor_hora_padrao", "Valor da hora", "dinheiro"),
        cfg.Campo("margem_minima", "Margem", "percentual"),
    ])
    erros = cfg.salvar(secao, {"valor_hora_padrao": "não é número", "margem_minima": "25"})
    assert erros and config_temporaria.read_text(encoding="utf-8") == antes


def test_campo_ausente_no_formulario_nao_apaga_o_valor_gravado(config_temporaria):
    secao = cfg.Secao("precos", "Preços", "precos.yaml", campos=[
        cfg.Campo("valor_hora_padrao", "Valor da hora", "dinheiro"),
        cfg.Campo("margem_minima", "Margem", "percentual"),
    ])
    assert cfg.salvar(secao, {"valor_hora_padrao": "200"}) == []
    texto = config_temporaria.read_text(encoding="utf-8")
    assert "0.2" in texto  # margem intacta


# ---------- destinatários ----------


def test_destinatarios_usam_o_yaml_e_caem_para_o_env(tmp_path, monkeypatch):
    from licitabot import config as c

    monkeypatch.setattr(c, "_load_yaml", lambda p: {"destinatarios": ["a@x.com", "b@x.com"]})
    assert c.destinatarios() == ["a@x.com", "b@x.com"]

    monkeypatch.setattr(c, "_load_yaml", lambda p: {})
    s = c.get_settings()
    monkeypatch.setattr(s, "owner_email", "unico@x.com")
    assert c.destinatarios(s) == ["unico@x.com"]

    monkeypatch.setattr(s, "owner_email", "")
    assert c.destinatarios(s) == []
