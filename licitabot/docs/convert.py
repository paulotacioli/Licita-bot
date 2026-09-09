"""Conversão docx -> pdf em lote: uma única sessão do Word (COM) para todos os arquivos; LibreOffice headless como alternativa."""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

_LO_CANDIDATES = [
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
]
WD_FORMAT_PDF = 17


def _soffice() -> str | None:
    for c in _LO_CANDIDATES:
        if Path(c).exists():
            return c
    return shutil.which("soffice")


def _word_batch(pares: list[tuple[Path, Path]]) -> dict[Path, str]:
    """Converte com o Word via COM numa única instância. Retorna {docx: erro} para os que falharam."""
    import pythoncom
    import win32com.client

    erros: dict[Path, str] = {}
    pythoncom.CoInitialize()
    word = None
    try:
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0
        for docx, pdf in pares:
            doc = None
            try:
                doc = word.Documents.Open(str(docx.resolve()), ReadOnly=True, AddToRecentFiles=False, Visible=False)
                doc.ExportAsFixedFormat(OutputFileName=str(pdf.resolve()), ExportFormat=WD_FORMAT_PDF)
            except Exception as e:  # noqa: BLE001
                erros[docx] = f"word: {e}"
            finally:
                if doc is not None:
                    try:
                        doc.Close(False)
                    except Exception:  # noqa: BLE001
                        pass
    finally:
        if word is not None:
            try:
                word.Quit()
            except Exception:  # noqa: BLE001
                pass
        pythoncom.CoUninitialize()
    return erros


def _libreoffice_batch(pares: list[tuple[Path, Path]]) -> dict[Path, str]:
    so = _soffice()
    erros: dict[Path, str] = {}
    if not so:
        return {d: "libreoffice ausente" for d, _ in pares}
    for docx, pdf in pares:
        try:
            subprocess.run([so, "--headless", "--convert-to", "pdf", "--outdir", str(pdf.parent), str(docx)], check=True, timeout=180, capture_output=True)
            gerado = pdf.parent / (docx.stem + ".pdf")
            if gerado.exists() and gerado != pdf:
                gerado.replace(pdf)
            if not pdf.exists():
                erros[docx] = "libreoffice: pdf não gerado"
        except Exception as e:  # noqa: BLE001
            erros[docx] = f"libreoffice: {e}"
    return erros


def docx_to_pdf_many(docxs: list[Path]) -> dict[Path, str]:
    """Converte todos; retorna {docx: mensagem de erro} apenas para os que falharam."""
    pares = [(d, d.with_suffix(".pdf")) for d in docxs]
    if not pares:
        return {}
    try:
        erros = _word_batch(pares)
    except Exception as e:  # noqa: BLE001
        log.warning("Word indisponível (%s); tentando LibreOffice", e)
        erros = {d: f"word: {e}" for d, _ in pares}
    pendentes = [(d, p) for d, p in pares if d in erros]
    if pendentes:
        erros_lo = _libreoffice_batch(pendentes)
        for d, _ in pendentes:
            if d in erros_lo:
                erros[d] = erros[d] + "; " + erros_lo[d]
            else:
                erros.pop(d, None)
    return erros


def docx_to_pdf(docx: Path) -> Path:
    erros = docx_to_pdf_many([docx])
    if docx in erros:
        raise RuntimeError("Nenhum conversor docx->pdf disponível (instale o Word ou o LibreOffice): " + erros[docx])
    return docx.with_suffix(".pdf")
