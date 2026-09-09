"""Backend OpenAI (chat.completions) com saída estruturada validada por Pydantic.

Serve para rotear etapas baratas, como a pré-triagem, para um modelo "mini". Não anexa PDF:
o texto extraído dos editais vai no próprio prompt (analysis.py já trata isso quando
`supports_pdf` é False). Primeiro tenta o modo estrito de JSON Schema do próprio SDK; se o
esquema usar algo que o modo estrito não aceita (limites numéricos, por exemplo), cai para
`json_object` com o esquema descrito no prompt e validação local, com uma tentativa de correção.
"""

from __future__ import annotations

import json
import logging
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from licitabot.config import get_settings
from licitabot.llm.prompts import SYSTEM_BASE

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

PREFIXO = "openai:"
MODELO_PADRAO = "gpt-5-mini"
ESFORCO = {"low": "low", "medium": "medium", "high": "high"}


class LLMResult(BaseModel):
    tokens_in: int = 0
    tokens_out: int = 0
    cache_read: int = 0
    model: str = ""
    custo_usd: float = 0.0


def _aceita_esforco(model: str) -> bool:
    """Só a família de raciocínio aceita `reasoning_effort`; nos demais o parâmetro dá erro."""
    m = model.lower()
    return m.startswith(("gpt-5", "o1", "o3", "o4"))


class OpenAIClient:
    supports_pdf = False

    def __init__(self, api_key: str | None = None):
        s = get_settings()
        key = api_key or s.openai_api_key
        if not key:
            raise RuntimeError(
                "OPENAI_API_KEY não configurada no .env. Coloque a chave lá, ou aponte o modelo da etapa "
                "de volta para o Claude (ex.: LLM_MODEL_PRETRIAGEM=haiku)."
            )
        import openai

        kw: dict[str, Any] = {"api_key": key, "max_retries": 3, "timeout": 120}
        if s.openai_base_url:
            kw["base_url"] = s.openai_base_url
        self.client = openai.OpenAI(**kw)

    @staticmethod
    def _modelo(model: str) -> str:
        m = model[len(PREFIXO):] if model.startswith(PREFIXO) else model
        return m or MODELO_PADRAO

    @staticmethod
    def _mensagens(system_extra: str, user_blocks: list[dict[str, Any]]) -> list[dict[str, str]]:
        textos = []
        for b in user_blocks:
            if b.get("type") == "text":
                textos.append(b["text"])
            elif b.get("type") == "document":
                textos.append(f"[documento '{b.get('title', '')}' omitido: use o texto extraído fornecido]")
        return [
            {"role": "system", "content": f"{SYSTEM_BASE}\n\n{system_extra}".strip()},
            {"role": "user", "content": "\n\n".join(textos)},
        ]

    @staticmethod
    def _usage(resp: Any, model: str) -> LLMResult:
        u = getattr(resp, "usage", None)
        det = getattr(u, "prompt_tokens_details", None) if u else None
        return LLMResult(
            tokens_in=int(getattr(u, "prompt_tokens", 0) or 0),
            tokens_out=int(getattr(u, "completion_tokens", 0) or 0),
            cache_read=int(getattr(det, "cached_tokens", 0) or 0) if det else 0,
            model=model,
        )

    def _params(self, model: str, max_tokens: int, effort: str) -> dict[str, Any]:
        p: dict[str, Any] = {"model": model, "max_completion_tokens": max_tokens}
        if _aceita_esforco(model):
            p["reasoning_effort"] = ESFORCO.get(effort, "low")
        return p

    def structured(
        self, model: str, schema: type[T], user_blocks: list[dict[str, Any]], system_extra: str = "",
        max_tokens: int = 16000, effort: str = "high",
    ) -> tuple[T, LLMResult]:
        model = self._modelo(model)
        msgs = self._mensagens(system_extra, user_blocks)
        # 1) modo estrito, com o esquema imposto pela API
        try:
            resp = self.client.chat.completions.parse(messages=msgs, response_format=schema, **self._params(model, max_tokens, effort))
            escolha = resp.choices[0]
            if getattr(escolha.message, "refusal", None):
                raise RuntimeError(f"Modelo recusou a solicitação: {escolha.message.refusal}")
            if escolha.message.parsed is None:
                raise ValueError("sem parsed")
            return escolha.message.parsed, self._usage(resp, model)
        except (ValueError, TypeError) as e:
            log.info("openai: modo estrito indisponível para %s (%s); usando json_object", schema.__name__, str(e)[:120])
        except Exception as e:  # noqa: BLE001
            # BadRequestError por esquema não suportado cai aqui; qualquer outra coisa também tenta o modo tolerante
            if "refusal" in str(e).lower() or "recusou" in str(e):
                raise
            log.info("openai: parse estrito falhou para %s (%s); usando json_object", schema.__name__, str(e)[:160])

        # 2) json_object com o esquema no prompt e validação local (uma correção)
        instrucao = (
            "Responda SOMENTE com um objeto JSON válido que obedeça exatamente a este JSON Schema, sem texto fora dele:\n"
            + json.dumps(schema.model_json_schema(), ensure_ascii=False)
        )
        msgs2 = [msgs[0], {"role": "user", "content": msgs[1]["content"] + "\n\n" + instrucao}]
        resp = self.client.chat.completions.create(messages=msgs2, response_format={"type": "json_object"}, **self._params(model, max_tokens, effort))
        bruto = resp.choices[0].message.content or ""
        try:
            return schema.model_validate_json(bruto), self._usage(resp, model)
        except ValidationError as e:
            log.warning("openai: JSON não validou; pedindo correção: %s", str(e)[:300])
            msgs3 = msgs2 + [
                {"role": "assistant", "content": bruto},
                {"role": "user", "content": f"Sua resposta não validou contra o esquema: {str(e)[:1200]}. Responda de novo, apenas o JSON correto."},
            ]
            resp = self.client.chat.completions.create(messages=msgs3, response_format={"type": "json_object"}, **self._params(model, max_tokens, effort))
            return schema.model_validate_json(resp.choices[0].message.content or ""), self._usage(resp, model)

    def text(self, model: str, user_blocks: list[dict[str, Any]], system_extra: str = "", max_tokens: int = 16000) -> str:
        model = self._modelo(model)
        resp = self.client.chat.completions.create(messages=self._mensagens(system_extra, user_blocks), **self._params(model, max_tokens, "low"))
        return resp.choices[0].message.content or ""
