"""Criação de conta por convite e gestão de usuários do painel."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlmodel import select

from licitabot.db.models import ConvitePainel
from licitabot.db.session import db_session
from licitabot.web import auth


def test_criar_conta_e_rota_publica_mas_usuarios_nao():
    assert auth.rota_publica("/criar-conta")
    assert not auth.rota_publica("/usuarios")


def test_convite_valido_uma_unica_vez():
    codigo, expira = auth.gerar_convite("admin")
    assert expira > datetime.now() + timedelta(hours=47)
    assert auth.validar_convite(codigo) is None
    auth.consumir_convite(codigo, "novo")
    assert "já foi usado" in auth.validar_convite(codigo)


def test_codigo_nao_fica_em_texto_no_banco():
    codigo, _ = auth.gerar_convite("admin")
    with db_session() as s:
        hashes = [c.codigo_hash for c in s.exec(select(ConvitePainel)).all()]
    assert codigo not in hashes and len(hashes[-1]) == 64


def test_convite_invalido_vazio_e_expirado():
    assert "inválido" in auth.validar_convite("nao-existe")
    assert "Informe" in auth.validar_convite("")
    codigo, _ = auth.gerar_convite("admin")
    with db_session() as s:
        c = s.exec(select(ConvitePainel).where(ConvitePainel.codigo_hash == auth._hash_codigo(codigo))).first()
        c.expira_em = datetime.now() - timedelta(minutes=1)
        s.add(c)
        s.commit()
    assert "expirou" in auth.validar_convite(codigo)


def test_validacao_de_novo_usuario():
    auth.criar_usuario("existente.x", "senhaForte123")
    assert "Já existe" in auth.validar_novo_usuario("existente.x", "senhaForte123", "senhaForte123")
    assert "3 caracteres" in auth.validar_novo_usuario("a!", "senhaForte123", "senhaForte123")
    assert "não são iguais" in auth.validar_novo_usuario("novo.y", "senhaForte123", "outra")
    assert auth.validar_novo_usuario("novo.y", "senhaForte123", "senhaForte123") is None


def test_remover_usuario_protege_a_propria_conta_e_a_ultima():
    auth.criar_usuario("dono.z", "senhaForte123")
    auth.criar_usuario("colega.z", "senhaForte123")
    dono = auth.autenticar("dono.z", "senhaForte123")
    colega = auth.autenticar("colega.z", "senhaForte123")
    assert "própria conta" in auth.remover_usuario(dono.id, dono.id)
    assert auth.remover_usuario(colega.id, dono.id) is None
    assert auth.autenticar("colega.z", "senhaForte123") is None
