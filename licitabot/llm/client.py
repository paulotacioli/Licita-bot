"""Cliente Claude com saída estruturada (messages.parse + Pydantic) e suporte a PDF como documento.

Notas de API (2026): modelos Opus 5 / Sonnet 5 não aceitam `temperature`; pensamento adaptativo é o padrão;
`output_format`/`output_config` garante JSON válido. Citações são incompatíveis com saída estruturada,
por isso pedimos páginas/trechos dentro do próprio JSON.
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any, TypeVar

import anthropic
from pydantic import BaseModel

from licitabot.config import get_settings
from licitabot.llm.prompts import SYSTEM_BASE

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

MAX_PDF_BYTES = 30 * 1024 * 1024
MAX_PDF_PAGES = 100


class LLMResult(BaseModel):
    tokens_in: int = 0
    tokens_out: int = 0
    cache_read: int = 0
    model: str = ""


ALIAS_PARA_ID = {"sonnet": "claude-sonnet-5", "opus": "claude-opus-5", "haiku": "claude-haiku-4-5"}


def get_llm_client():
    """Fábrica: backend configurado em LLM_BACKEND (claude_code = assinatura do Claude Code; api = API Anthropic)."""
    s = get_settings()
    if s.llm_backend.lower() == "api":
        return ClaudeClient()
    from licitabot.llm.claude_code import ClaudeCodeClient

    return ClaudeCodeClient()


class ClaudeClient:
    supports_pdf = True

    def __init__(self, api_key: str | None = None):
        s = get_settings()
        key = api_key or s.anthropic_api_key
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY não configurada no .env (ou use LLM_BACKEND=claude_code)")
        self.client = anthropic.Anthropic(api_key=key, max_retries=3)

    @staticmethod
    def _system(system_extra: str) -> list[dict[str, Any]]:
        return [{"type": "text", "text": f"{SYSTEM_BASE}\n\n{system_extra}".strip(), "cache_control": {"type": "ephemeral"}}]

    def structured(
        self,
        model: str,
        schema: type[T],
        user_blocks: list[dict[str, Any]],
        system_extra: str = "",
        max_tokens: int = 16000,
        effort: str = "high",
    ) -> tuple[T, LLMResult]:
        """Chama o modelo com saída estruturada validada contra `schema`."""
        model = ALIAS_PARA_ID.get(model, model)
        resp = self.client.messages.parse(
            model=model,
            max_tokens=max_tokens,
            system=self._system(system_extra),
            messages=[{"role": "user", "content": user_blocks}],
            output_format=schema,
            output_config={"effort": effort},
        )
        if resp.stop_reason == "refusal":
            det = getattr(resp, "stop_details", None)
            raise RuntimeError(f"Modelo recusou a solicitação: {getattr(det, 'explanation', '')}")
        if resp.stop_reason == "max_tokens":
            raise RuntimeError("Resposta truncada (max_tokens); aumente o limite ou reduza a entrada")
        obj = resp.parsed_output
        if obj is None:
            raise RuntimeError("Modelo não retornou JSON estruturado")
        u = resp.usage
        res = LLMResult(
            tokens_in=(u.input_tokens or 0) + (getattr(u, "cache_read_input_tokens", 0) or 0),
            tokens_out=u.output_tokens or 0,
            cache_read=getattr(u, "cache_read_input_tokens", 0) or 0,
            model=model,
        )
        return obj, res

    def text(self, model: str, user_blocks: list[dict[str, Any]], system_extra: str = "", max_tokens: int = 16000) -> str:
        resp = self.client.messages.create(
            model=ALIAS_PARA_ID.get(model, model),
            max_tokens=max_tokens,
            system=self._system(system_extra),
            messages=[{"role": "user", "content": user_blocks}],
        )
        if resp.stop_reason == "refusal":
            raise RuntimeError("Modelo recusou a solicitação")
        return "".join(b.text for b in resp.content if b.type == "text")


# ---------- helpers de blocos ----------


def text_block(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def pdf_block(path: Path, cache: bool = False) -> dict[str, Any] | None:
    """Bloco `document` com o PDF em base64, ou None se exceder limites (usar texto extraído)."""
    size = path.stat().st_size
    if size > MAX_PDF_BYTES:
        return None
    try:
        from pypdf import PdfReader

        if len(PdfReader(str(path)).pages) > MAX_PDF_PAGES:
            return None
    except Exception:  # noqa: BLE001
        return None
    data = base64.standard_b64encode(path.read_bytes()).decode("ascii")
    block: dict[str, Any] = {
        "type": "document",
        "source": {"type": "base64", "media_type": "application/pdf", "data": data},
        "title": path.name,
    }
    if cache:
        block["cache_control"] = {"type": "ephemeral"}
    return block
