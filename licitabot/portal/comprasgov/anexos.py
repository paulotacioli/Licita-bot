"""Upload de anexos (proposta/habilitação) na tela do pregão, com verificação de que o nome apareceu na lista."""

from __future__ import annotations

import logging
from pathlib import Path

from licitabot.portal.comprasgov.navigator import Navigator

log = logging.getLogger(__name__)

MAX_ANEXO_BYTES = 25 * 1024 * 1024


def upload_anexos(nav: Navigator, arquivos: list[Path]) -> list[dict]:
    page = nav.page
    sel = nav.sel
    resultado: list[dict] = []
    if not arquivos:
        return resultado
    with nav.step("anexos_abrir"):
        botao = sel.find(page, "proposta", "anexos_botao", timeout_ms=8000, must=False)
        if botao:
            botao.click()
            page.wait_for_timeout(1500)
    for arq in arquivos:
        if arq.stat().st_size > MAX_ANEXO_BYTES:
            resultado.append({"arquivo": arq.name, "ok": False, "motivo": "maior que 25 MB"})
            continue
        with nav.step(f"anexo_{arq.stem[:30]}"):
            inp = sel.find(page, "proposta", "anexos_input", timeout_ms=8000, must=False)
            if not inp:
                resultado.append({"arquivo": arq.name, "ok": False, "motivo": "campo de upload não encontrado"})
                continue
            inp.set_input_files(str(arq))
            page.wait_for_timeout(2500)
            nav.fechar_modais_inesperados()
            apareceu = page.get_by_text(arq.name[:40], exact=False).count() > 0
            resultado.append({"arquivo": arq.name, "ok": apareceu, "motivo": "" if apareceu else "nome não apareceu na lista"})
    return resultado
