import pytest

from licitabot.approval.tokens import gerar_token, snapshot_hash, validar_token


def test_token_roundtrip():
    h = snapshot_hash({"a": 1})
    t = gerar_token(10, 5, "aprovar", h)
    p = validar_token(t, max_age_s=60)
    assert p == {"o": 10, "a": 5, "acao": "aprovar", "h": h}


def test_token_adulterado():
    t = gerar_token(1, 1, "aprovar", "x")
    with pytest.raises(ValueError):
        validar_token(t[:-3] + "abc", max_age_s=60)


def test_snapshot_hash_estavel():
    assert snapshot_hash({"b": 2, "a": 1}) == snapshot_hash({"a": 1, "b": 2})
    assert snapshot_hash({"a": 1}) != snapshot_hash({"a": 2})
