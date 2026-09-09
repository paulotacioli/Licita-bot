"""Busca paginada de editais no PNCP."""

from __future__ import annotations

import logging
from typing import Iterator

from licitabot.pncp.client import PNCPClient
from licitabot.pncp.schemas import SearchItem, SearchResponse

log = logging.getLogger(__name__)


def iter_editais(
    client: PNCPClient,
    termo: str,
    max_paginas: int = 50,
    tam_pagina: int = 100,
    status: str = "recebendo_proposta",
) -> Iterator[SearchItem]:
    """Itera todos os editais que casam com o termo, página a página."""
    pagina = 1
    vistos: set[str] = set()
    while pagina <= max_paginas:
        raw = client.search(termo, pagina=pagina, tam_pagina=tam_pagina, status=status)
        resp = SearchResponse.model_validate(raw)
        if not resp.items:
            break
        novos = 0
        for it in resp.items:
            key = it.numero_controle_pncp or it.item_url
            if key in vistos:
                continue
            vistos.add(key)
            novos += 1
            yield it
        log.info("PNCP '%s' página %s: %s itens (total informado %s)", termo, pagina, len(resp.items), resp.total)
        if novos == 0 or len(resp.items) < tam_pagina or pagina * tam_pagina >= resp.total:
            break
        pagina += 1
