"""Backend de IA via Claude Code (CLI `claude -p`), usando a assinatura do usuário em vez da API.

Autenticação: token de `claude setup-token` em CLAUDE_CODE_OAUTH_TOKEN (.env) ou o login feito com `claude login`
no terminal. Saída estruturada via `--json-schema` (campo `structured_output`). PDFs não são anexados: o texto
extraído dos editais vai no próprio prompt (limite de 10 MB no stdin).
"""

from __future__ import annotations

import glob
import json
import logging
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from licitabot.config import get_settings
from licitabot.llm.prompts import SYSTEM_BASE

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

TIMEOUT_S = 25 * 60
MAX_STDIN_BYTES = 9 * 1024 * 1024
ALIAS_PARA_ID = {"sonnet": "claude-sonnet-5", "opus": "claude-opus-5", "haiku": "claude-haiku-4-5"}


class LLMResult(BaseModel):
    tokens_in: int = 0
    tokens_out: int = 0
    cache_read: int = 0
    model: str = ""
    custo_usd: float = 0.0


def localizar_cli() -> str | None:
    s = get_settings()
    if s.claude_cli_path and Path(s.claude_cli_path).exists():
        return s.claude_cli_path
    for nome in ("claude", "claude.exe", "claude.cmd"):
        p = shutil.which(nome)
        if p:
            return p
    for cand in (r"%USERPROFILE%\.local\bin\claude.exe", r"%LOCALAPPDATA%\Microsoft\WinGet\Links\claude.exe"):
        p = Path(os.path.expandvars(cand))
        if p.exists():
            return str(p)
    winget = sorted(glob.glob(os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Anthropic.ClaudeCode*\claude.exe")), key=os.path.getmtime)
    if winget:
        return winget[-1]
    bundled = sorted(glob.glob(os.path.expandvars(r"%APPDATA%\Claude\claude-code\*\claude.exe")), key=os.path.getmtime)
    return bundled[-1] if bundled else None


def _marker() -> Path:
    return get_settings().data_path / "claude_cli_ok.json"


def cli_verificado() -> tuple[bool, str]:
    """Para o checklist de onboarding: última chamada bem-sucedida conhecida."""
    s = get_settings()
    exe = localizar_cli()
    if not exe:
        return False, "CLI do Claude Code não encontrada (instale: winget install Anthropic.ClaudeCode)"
    if _marker().exists():
        d = json.loads(_marker().read_text(encoding="utf-8"))
        return True, f"ok em {d.get('quando', '?')[:16]} ({Path(exe).name})"
    if not s.claude_code_oauth_token:
        return False, "sem CLAUDE_CODE_OAUTH_TOKEN no .env (rode `claude setup-token`) — depois `licitabot llm-teste`"
    return False, "token presente; rode `licitabot llm-teste` para validar"


class ClaudeCodeClient:
    supports_pdf = False

    def __init__(self):
        self.exe = localizar_cli()
        if not self.exe:
            raise RuntimeError("CLI do Claude Code não encontrada. Instale com `winget install Anthropic.ClaudeCode`.")
        self.settings = get_settings()

    def _env(self) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")}
        if self.settings.claude_code_oauth_token:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = self.settings.claude_code_oauth_token
        env.pop("ANTHROPIC_API_KEY", None)  # garante uso da assinatura, não da API
        return env

    @staticmethod
    def _montar_prompt(system_extra: str, user_blocks: list[dict[str, Any]]) -> str:
        textos = []
        for b in user_blocks:
            if b.get("type") == "text":
                textos.append(b["text"])
            elif b.get("type") == "document":
                textos.append(f"[documento '{b.get('title', '')}' omitido: use o texto extraído fornecido]")
        corpo = "\n\n".join(textos)
        return f"<instrucoes_do_sistema>\n{SYSTEM_BASE}\n\n{system_extra}\n</instrucoes_do_sistema>\n\n{corpo}"

    def _run(self, prompt: str, model: str, schema: dict | None, max_turns: int = 2) -> dict[str, Any]:
        if len(prompt.encode("utf-8")) > MAX_STDIN_BYTES:
            prompt = prompt.encode("utf-8")[:MAX_STDIN_BYTES].decode("utf-8", errors="ignore") + "\n\n[TEXTO TRUNCADO POR TAMANHO]"
        args = [self.exe, "-p", "--output-format", "json", "--model", model, "--max-turns", str(max_turns), "--no-session-persistence"]
        if schema:
            args += ["--json-schema", json.dumps(schema, ensure_ascii=False)]
        # Pasta de trabalho neutra e vazia: evita que a CLI carregue CLAUDE.md, memória ou arquivos do projeto no prompt
        workdir = self.settings.data_path / "llm_workdir"
        workdir.mkdir(parents=True, exist_ok=True)
        log.debug("claude -p (%s, %d chars)", model, len(prompt))
        r = subprocess.run(args, input=prompt, capture_output=True, text=True, encoding="utf-8", errors="replace", env=self._env(), timeout=TIMEOUT_S, cwd=str(workdir))
        if not r.stdout.strip():
            raise RuntimeError(f"claude -p sem saída (exit {r.returncode}): {r.stderr[:500]}")
        try:
            d = json.loads(r.stdout)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"saída inválida do claude -p: {r.stdout[:500]}") from e
        if d.get("is_error"):
            msg = str(d.get("result", ""))[:500]
            if "login" in msg.lower() or "logged" in msg.lower():
                msg += " — gere um token com `claude setup-token` e coloque em CLAUDE_CODE_OAUTH_TOKEN no .env"
            raise RuntimeError(f"Claude Code: {msg}")
        return d

    @staticmethod
    def _usage(d: dict[str, Any], model: str) -> LLMResult:
        u = d.get("usage") or {}
        return LLMResult(
            tokens_in=int(u.get("input_tokens", 0) or 0) + int(u.get("cache_read_input_tokens", 0) or 0),
            tokens_out=int(u.get("output_tokens", 0) or 0),
            cache_read=int(u.get("cache_read_input_tokens", 0) or 0),
            model=ALIAS_PARA_ID.get(model, model),
            custo_usd=float(d.get("total_cost_usd") or 0),
        )

    def structured(self, model: str, schema: type[T], user_blocks: list[dict[str, Any]], system_extra: str = "", max_tokens: int = 16000, effort: str = "high") -> tuple[T, LLMResult]:
        prompt = self._montar_prompt(system_extra, user_blocks)
        js = schema.model_json_schema()
        d = self._run(prompt, model, js)
        data = d.get("structured_output")
        if data is None:
            # fallback: tenta extrair JSON do texto
            txt = str(d.get("result", ""))
            i, j = txt.find("{"), txt.rfind("}")
            data = json.loads(txt[i : j + 1]) if i >= 0 and j > i else None
        try:
            obj = schema.model_validate(data)
        except (ValidationError, TypeError) as e:
            log.warning("JSON não validou; pedindo correção: %s", str(e)[:300])
            prompt2 = prompt + f"\n\nSua resposta anterior não validou contra o schema ({str(e)[:1200]}). Responda de novo, apenas o JSON correto."
            d = self._run(prompt2, model, js)
            obj = schema.model_validate(d.get("structured_output"))
        _marker().write_text(json.dumps({"quando": datetime.now().isoformat(), "model": model}), encoding="utf-8")
        return obj, self._usage(d, model)

    def text(self, model: str, user_blocks: list[dict[str, Any]], system_extra: str = "", max_tokens: int = 16000) -> str:
        d = self._run(self._montar_prompt(system_extra, user_blocks), model, None)
        return str(d.get("result", ""))
