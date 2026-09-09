from licitabot.config import load_selectors
from licitabot.portal.comprasgov.selectors import _parse


def test_parse_specs():
    assert _parse("role=button;name=Enviar proposta") == ("role", {"role": "button", "name": "Enviar proposta"})
    assert _parse("label=Valor unitário") == ("label", {"value": "Valor unitário"})
    assert _parse("css=input[type='file']") == ("css", {"value": "input[type='file']"})


def test_yaml_tem_grupos_criticos():
    cfg = load_selectors("comprasgov")
    for grupo, chave in [("session", "logged_in_marker"), ("proposta", "valor_unitario"), ("envio", "botao_enviar_proposta")]:
        assert "primary" in cfg[grupo][chave]
