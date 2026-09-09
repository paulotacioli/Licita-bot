"""Cliente HTTP do PNCP: TLS 1.2+, timeouts longos, retry com backoff.

Endpoints validados em 2026-09-08 (ver plano). O PNCP é lento (30-120 s) e devolve 504 com frequência.
"""

from __future__ import annotations

import logging
import ssl
from pathlib import Path
from typing import Any

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential_jitter

log = logging.getLogger(__name__)

SEARCH_URL = "https://pncp.gov.br/api/search/"
API_V1 = "https://pncp.gov.br/api/pncp/v1"
PNCP_API_V1 = "https://pncp.gov.br/pncp-api/v1"
CONSULTA_V1 = "https://pncp.gov.br/api/consulta/v1"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) LicitaBot/0.1 (+contato: fornecedor)",
    "Accept": "application/json, */*",
    "Accept-Language": "pt-BR,pt;q=0.9",
}


def _ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 500, 502, 503, 504)
    return False


class PNCPClient:
    def __init__(self, timeout: float = 150.0):
        self._client = httpx.Client(
            headers=HEADERS,
            timeout=httpx.Timeout(timeout, connect=30.0),
            verify=_ssl_context(),
            follow_redirects=True,
            http2=False,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "PNCPClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @retry(
        retry=retry_if_exception(_retryable),
        stop=stop_after_attempt(4),
        wait=wait_exponential_jitter(initial=5, max=90),
        reraise=True,
    )
    def _get(self, url: str, params: dict[str, Any] | None = None) -> httpx.Response:
        log.debug("GET %s %s", url, params or "")
        r = self._client.get(url, params=params)
        r.raise_for_status()
        return r

    def get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        return self._get(url, params).json()

    # ---------- busca ----------

    def search(
        self,
        q: str,
        pagina: int = 1,
        tam_pagina: int = 100,
        status: str = "recebendo_proposta",
        tipos_documento: str = "edital",
        ordenacao: str = "-data",
        **extra: Any,
    ) -> dict[str, Any]:
        params = {
            "q": q,
            "tipos_documento": tipos_documento,
            "ordenacao": ordenacao,
            "pagina": pagina,
            "tam_pagina": tam_pagina,
            "status": status,
        }
        params.update({k: v for k, v in extra.items() if v not in (None, "", [])})
        return self.get_json(SEARCH_URL, params)

    # ---------- compra ----------

    def compra_detalhe(self, cnpj: str, ano: int, seq: int) -> dict[str, Any]:
        # Movido em 2026 para a API de consulta (o caminho /pncp-api devolve 301 sem Location)
        return self.get_json(f"{CONSULTA_V1}/orgaos/{cnpj}/compras/{ano}/{seq}")

    def compra_itens(self, cnpj: str, ano: int, seq: int) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        pagina = 1
        while True:
            data = self.get_json(
                f"{API_V1}/orgaos/{cnpj}/compras/{ano}/{seq}/itens",
                {"pagina": pagina, "tamanhoPagina": 50},
            )
            if isinstance(data, dict):
                data = data.get("data") or data.get("itens") or []
            if not data:
                break
            out.extend(data)
            if len(data) < 50:
                break
            pagina += 1
        return out

    def compra_arquivos(self, cnpj: str, ano: int, seq: int) -> list[dict[str, Any]]:
        data = self.get_json(f"{API_V1}/orgaos/{cnpj}/compras/{ano}/{seq}/arquivos")
        return data if isinstance(data, list) else data.get("data", [])

    def download(self, url: str, destino: Path, max_bytes: int = 200 * 1024 * 1024) -> tuple[Path, str]:
        """Baixa um arquivo em streaming. Retorna (caminho, content-type)."""
        destino.parent.mkdir(parents=True, exist_ok=True)
        with self._client.stream("GET", url) as r:
            r.raise_for_status()
            ctype = r.headers.get("content-type", "")
            total = 0
            with destino.open("wb") as f:
                for chunk in r.iter_bytes(1024 * 256):
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValueError(f"Arquivo maior que {max_bytes} bytes: {url}")
                    f.write(chunk)
        return destino, ctype

    # ---------- API oficial de consulta (secundária, instável) ----------

    def consulta_proposta(self, data_final_yyyymmdd: str, modalidade: int, pagina: int = 1, tamanho: int = 50) -> dict:
        return self.get_json(
            f"{CONSULTA_V1}/contratacoes/proposta",
            {
                "dataFinal": data_final_yyyymmdd,
                "codigoModalidadeContratacao": modalidade,
                "pagina": pagina,
                "tamanhoPagina": max(10, min(tamanho, 500)),
            },
        )
