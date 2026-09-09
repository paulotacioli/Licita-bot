"""Roteamento por prefixo de modelo e backend OpenAI com SDK simulado (sem rede)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import BaseModel, Field

from licitabot.llm import client as llm_client
from licitabot.llm import openai_client


class Ping(BaseModel):
    ok: bool
    mensagem: str


class ComLimites(BaseModel):
    score: int = Field(ge=0, le=100)


@pytest.mark.parametrize(
    "modelo,esperado",
    [("openai:gpt-5-mini", ("openai", "gpt-5-mini")), ("haiku", ("claude", "haiku")),
     ("claude:opus", ("claude", "opus")), ("OpenAI: gpt-4o-mini", ("openai", "gpt-4o-mini")),
     ("desconhecido:x", ("claude", "desconhecido:x"))],
)
def test_provedor_do_modelo(modelo, esperado):
    assert llm_client.provedor_do_modelo(modelo) == esperado


class BackendFalso:
    supports_pdf = False

    def __init__(self, nome):
        self.nome = nome
        self.chamadas = []

    def structured(self, model, *a, **k):
        self.chamadas.append(model)
        return Ping(ok=True, mensagem=self.nome), None


def test_router_despacha_pelo_prefixo_e_reusa_o_cliente(monkeypatch):
    openai_falso, claude_falso = BackendFalso("openai"), BackendFalso("claude")
    monkeypatch.setattr(llm_client, "_cliente_openai", lambda: openai_falso)
    monkeypatch.setattr(llm_client, "_cliente_claude", lambda: claude_falso)
    r = llm_client.get_llm_client()
    assert r.structured("openai:gpt-5-mini", Ping, [])[0].mensagem == "openai"
    assert r.structured("haiku", Ping, [])[0].mensagem == "claude"
    assert r.structured("openai:gpt-5-mini", Ping, [])[0].mensagem == "openai"
    assert openai_falso.chamadas == ["gpt-5-mini", "gpt-5-mini"]  # prefixo removido
    assert claude_falso.chamadas == ["haiku"]


# ---------- OpenAIClient com SDK simulado ----------


def _resposta(parsed=None, content=None, refusal=None):
    msg = SimpleNamespace(parsed=parsed, content=content, refusal=refusal)
    usage = SimpleNamespace(prompt_tokens=12, completion_tokens=5, prompt_tokens_details=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=usage)


class CompletionsFalso:
    def __init__(self, parse_lanca=None, create_conteudos=None):
        self.parse_lanca = parse_lanca
        self.create_conteudos = list(create_conteudos or [])
        self.kwargs_parse = []
        self.kwargs_create = []

    def parse(self, **kw):
        self.kwargs_parse.append(kw)
        if self.parse_lanca:
            raise self.parse_lanca
        return _resposta(parsed=kw["response_format"](ok=True, mensagem="olá"))

    def create(self, **kw):
        self.kwargs_create.append(kw)
        return _resposta(content=self.create_conteudos.pop(0))


@pytest.fixture()
def cliente(monkeypatch):
    monkeypatch.setattr(openai_client.get_settings(), "openai_api_key", "sk-teste")

    def fabrica(comp):
        c = openai_client.OpenAIClient(api_key="sk-teste")
        c.client = SimpleNamespace(chat=SimpleNamespace(completions=comp))
        return c, comp

    return fabrica


def test_sem_chave_da_erro_claro(monkeypatch):
    monkeypatch.setattr(openai_client.get_settings(), "openai_api_key", "")
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        openai_client.OpenAIClient()


def test_modo_estrito_funciona_e_remove_o_prefixo(cliente):
    c, comp = cliente(CompletionsFalso())
    obj, meta = c.structured("openai:gpt-5-mini", Ping, [{"type": "text", "text": "oi"}], effort="low", max_tokens=50)
    assert obj.mensagem == "olá" and meta.model == "gpt-5-mini" and meta.tokens_in == 12
    kw = comp.kwargs_parse[0]
    assert kw["model"] == "gpt-5-mini" and kw["max_completion_tokens"] == 50 and kw["reasoning_effort"] == "low"


def test_modelo_sem_raciocinio_nao_recebe_reasoning_effort(cliente):
    c, comp = cliente(CompletionsFalso())
    c.structured("gpt-4o-mini", Ping, [{"type": "text", "text": "oi"}])
    assert "reasoning_effort" not in comp.kwargs_parse[0]


def test_cai_para_json_object_quando_o_esquema_nao_e_aceito(cliente):
    c, comp = cliente(CompletionsFalso(parse_lanca=RuntimeError("Invalid schema: 'minimum' is not permitted"),
                                       create_conteudos=['{"score": 42}']))
    obj, _ = c.structured("gpt-5-mini", ComLimites, [{"type": "text", "text": "oi"}])
    assert obj.score == 42
    assert comp.kwargs_create[0]["response_format"] == {"type": "json_object"}
    assert "JSON Schema" in comp.kwargs_create[0]["messages"][1]["content"]


def test_pede_correcao_uma_vez_quando_o_json_nao_valida(cliente):
    c, comp = cliente(CompletionsFalso(parse_lanca=RuntimeError("Invalid schema"),
                                       create_conteudos=['{"score": "muito"}', '{"score": 7}']))
    obj, _ = c.structured("gpt-5-mini", ComLimites, [{"type": "text", "text": "oi"}])
    assert obj.score == 7 and len(comp.kwargs_create) == 2
    assert "não validou" in comp.kwargs_create[1]["messages"][-1]["content"]


def test_recusa_do_modelo_vira_erro(cliente):
    class Recusa(CompletionsFalso):
        def parse(self, **kw):
            return _resposta(refusal="não posso")

    c, _ = cliente(Recusa())
    with pytest.raises(RuntimeError, match="recusou"):
        c.structured("gpt-5-mini", Ping, [{"type": "text", "text": "oi"}])


def test_documento_vira_aviso_no_prompt(cliente):
    c, comp = cliente(CompletionsFalso())
    c.structured("gpt-5-mini", Ping, [{"type": "document", "title": "edital.pdf"}, {"type": "text", "text": "x"}])
    assert "edital.pdf" in comp.kwargs_parse[0]["messages"][1]["content"]
