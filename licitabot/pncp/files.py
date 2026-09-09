"""Download seguro de arquivos do PNCP e extração de ZIPs (proteção contra zip-bomb e path traversal)."""

from __future__ import annotations

import hashlib
import logging
import re
import zipfile
from pathlib import Path

from licitabot.pncp.client import PNCPClient

log = logging.getLogger(__name__)

MAX_ZIP_TOTAL = 500 * 1024 * 1024
MAX_ZIP_RATIO = 200
SAFE_NAME = re.compile(r"[^A-Za-z0-9._\- ]+")


def safe_filename(name: str, default: str = "arquivo") -> str:
    name = Path(name).name
    name = SAFE_NAME.sub("_", name).strip(" ._")
    return name or default


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def extract_zip(zip_path: Path, dest: Path) -> list[Path]:
    """Extrai com verificação de tamanho e de caminhos. Retorna os arquivos extraídos."""
    out: list[Path] = []
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        total = 0
        for info in z.infolist():
            if info.is_dir():
                continue
            total += info.file_size
            if total > MAX_ZIP_TOTAL:
                raise ValueError(f"ZIP excede {MAX_ZIP_TOTAL} bytes descompactado")
            if info.compress_size and info.file_size / max(info.compress_size, 1) > MAX_ZIP_RATIO:
                raise ValueError("Razão de compressão suspeita (zip bomb?)")
            name = safe_filename(info.filename)
            target = dest / name
            i = 1
            while target.exists():
                target = dest / f"{Path(name).stem}_{i}{Path(name).suffix}"
                i += 1
            with z.open(info) as src, target.open("wb") as dst:
                dst.write(src.read())
            out.append(target)
            # zip dentro de zip (1 nível)
            if target.suffix.lower() == ".zip":
                try:
                    out.extend(extract_zip(target, dest / f"{target.stem}_zip"))
                except Exception as e:  # noqa: BLE001
                    log.warning("Falha ao extrair zip aninhado %s: %s", target, e)
    return out


def download_arquivo(client: PNCPClient, url: str, titulo: str, dest_dir: Path, seq: int) -> Path:
    name = safe_filename(titulo, default=f"arquivo_{seq}")
    if "." not in name:
        name = f"{name}.bin"
    path = dest_dir / f"{seq:02d}_{name}"
    path, ctype = client.download(url, path)
    # Corrige extensão pelo content-type quando o título não ajuda
    if path.suffix.lower() == ".bin":
        if "pdf" in ctype:
            path = path.rename(path.with_suffix(".pdf"))
        elif "zip" in ctype:
            path = path.rename(path.with_suffix(".zip"))
    # Detecta pelo cabeçalho (o PNCP às vezes rotula .pdf um zip e vice-versa)
    with path.open("rb") as f:
        head = f.read(4)
    if head.startswith(b"PK") and path.suffix.lower() != ".zip":
        path = path.rename(path.with_suffix(".zip"))
    elif head.startswith(b"%PDF") and path.suffix.lower() != ".pdf":
        path = path.rename(path.with_suffix(".pdf"))
    return path
