"""Índice do cofre de documentos reais (contrato social, certidões, atestados, balanço).

Convenção de nomes (cofre/README.md): `<tipo>_<AAAA-MM-DD>[_descricao].pdf`, onde a data é a de EMISSÃO.
Validade padrão por tipo aplicada quando não houver `cofre/index.yaml` com override.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from pathlib import Path

import yaml
from rich import print as rprint
from rich.table import Table
from sqlmodel import select

from licitabot.config import get_settings
from licitabot.db.models import DocumentoCofre
from licitabot.db.session import db_session
from licitabot.pncp.files import sha256_of

log = logging.getLogger(__name__)

# tipo -> (dias de validade padrão, descrição, pasta)
TIPOS: dict[str, tuple[int | None, str, str]] = {
    "contrato_social": (None, "Contrato social consolidado / última alteração", ""),
    "cartao_cnpj": (90, "Comprovante de inscrição CNPJ (Receita)", ""),
    "documento_representante": (None, "RG/CNH do representante legal", ""),
    "procuracao": (365, "Procuração (se o signatário não for sócio)", ""),
    "cnd_federal": (180, "Certidão negativa de débitos federais e dívida ativa (RFB/PGFN)", "certidoes"),
    "crf_fgts": (30, "Certificado de regularidade do FGTS (Caixa)", "certidoes"),
    "cndt": (180, "Certidão negativa de débitos trabalhistas (TST)", "certidoes"),
    "cnd_estadual": (90, "Certidão de regularidade fiscal estadual (SEFAZ)", "certidoes"),
    "cnd_municipal": (90, "Certidão de regularidade fiscal municipal", "certidoes"),
    "certidao_falencia": (90, "Certidão negativa de falência e recuperação judicial", "certidoes"),
    "simples_nacional": (90, "Comprovante de opção pelo Simples Nacional", "certidoes"),
    "balanco_patrimonial": (None, "Balanço patrimonial do último exercício registrado", "balanco"),
    "indices_financeiros": (None, "Demonstrativo de índices LG/SG/LC", "balanco"),
    "atestado_capacidade_tecnica": (None, "Atestado de capacidade técnica", "atestados"),
    "registro_conselho": (365, "Registro em conselho de classe (se houver)", ""),
    "certificacao": (None, "Certificações (ISO etc.)", ""),
    "sicaf": (30, "Declaração SICAF / situação do fornecedor", "certidoes"),
    "alvara": (365, "Alvará de funcionamento", ""),
    "assinatura": (None, "Imagem da assinatura do representante (PNG)", ""),
    "logo": (None, "Logotipo da empresa (PNG)", ""),
}

NOME_RX = re.compile(r"^(?P<tipo>[a-z_]+?)_(?P<data>\d{4}-\d{2}-\d{2})(?:_(?P<desc>.+))?\.(?P<ext>pdf|png|jpg|jpeg)$", re.I)


def _overrides() -> dict[str, dict]:
    p = get_settings().cofre_path / "index.yaml"
    if not p.exists():
        return {}
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return {str(k): v for k, v in (data.get("documentos") or {}).items()}


def scan_cofre() -> list[DocumentoCofre]:
    s = get_settings()
    root = s.cofre_path
    root.mkdir(parents=True, exist_ok=True)
    for sub in ("certidoes", "atestados", "balanco"):
        (root / sub).mkdir(exist_ok=True)
    overrides = _overrides()
    encontrados: list[DocumentoCofre] = []
    with db_session() as session:
        atuais = {d.caminho: d for d in session.exec(select(DocumentoCofre))}
        vistos: set[str] = set()
        for f in root.rglob("*"):
            if not f.is_file() or f.name in ("README.md", "index.yaml", ".gitkeep"):
                continue
            m = NOME_RX.match(f.name)
            tipo = ""
            emitido = None
            desc = ""
            if m:
                tipo = m.group("tipo").lower()
                emitido = datetime.strptime(m.group("data"), "%Y-%m-%d")
                desc = (m.group("desc") or "").replace("_", " ")
            else:
                # Tenta inferir só pelo prefixo
                for t in TIPOS:
                    if f.stem.lower().startswith(t):
                        tipo = t
                        break
            ov = overrides.get(f.name, {})
            tipo = ov.get("tipo", tipo)
            if not tipo or tipo not in TIPOS:
                log.warning("Cofre: arquivo ignorado (nome fora do padrão): %s", f.name)
                continue
            if ov.get("emitido_em"):
                emitido = datetime.fromisoformat(str(ov["emitido_em"]))
            dias, descricao_tipo, _ = TIPOS[tipo]
            valido_ate = None
            if ov.get("valido_ate"):
                valido_ate = datetime.fromisoformat(str(ov["valido_ate"]))
            elif dias and emitido:
                valido_ate = emitido + timedelta(days=dias)
            caminho = str(f)
            vistos.add(caminho)
            doc = atuais.get(caminho) or DocumentoCofre(tipo=tipo, caminho=caminho)
            doc.tipo = tipo
            doc.titulo = ov.get("titulo") or desc or descricao_tipo
            doc.hash_sha256 = sha256_of(f)
            doc.emitido_em = emitido
            doc.valido_ate = valido_ate
            doc.orgao_emissor = ov.get("orgao_emissor", "")
            doc.descricao = ov.get("descricao", "")
            doc.ativo = True
            session.add(doc)
            encontrados.append(doc)
        for caminho, d in atuais.items():
            if caminho not in vistos and d.ativo:
                d.ativo = False
                session.add(d)
        session.commit()
        for d in encontrados:
            session.refresh(d)
    return encontrados


def documentos_validos(session, tipo: str, em: datetime | None = None) -> list[DocumentoCofre]:
    em = em or datetime.now()
    docs = session.exec(select(DocumentoCofre).where(DocumentoCofre.tipo == tipo, DocumentoCofre.ativo == True)).all()  # noqa: E712
    return sorted([d for d in docs if not d.valido_ate or d.valido_ate >= em], key=lambda d: d.emitido_em or datetime.min, reverse=True)


def vencendo(dias: int = 7) -> list[DocumentoCofre]:
    lim = datetime.now() + timedelta(days=dias)
    with db_session() as session:
        docs = session.exec(select(DocumentoCofre).where(DocumentoCofre.ativo == True)).all()  # noqa: E712
        return [d for d in docs if d.valido_ate and d.valido_ate <= lim]


def imprimir_cofre() -> None:
    with db_session() as session:
        docs = session.exec(select(DocumentoCofre).where(DocumentoCofre.ativo == True).order_by(DocumentoCofre.tipo)).all()  # noqa: E712
        t = Table(title=f"Cofre ({len(docs)} documentos)")
        for c in ("Tipo", "Título", "Emitido", "Válido até", "Situação", "Arquivo"):
            t.add_column(c, overflow="fold")
        agora = datetime.now()
        for d in docs:
            if d.valido_ate and d.valido_ate < agora:
                sit = "[red]VENCIDO[/red]"
            elif d.valido_ate and d.valido_ate < agora + timedelta(days=7):
                sit = "[yellow]vence em breve[/yellow]"
            else:
                sit = "[green]ok[/green]"
            t.add_row(d.tipo, d.titulo, f"{d.emitido_em:%d/%m/%Y}" if d.emitido_em else "-", f"{d.valido_ate:%d/%m/%Y}" if d.valido_ate else "-", sit, Path(d.caminho).name)
        rprint(t)
        faltando = [t for t, (_, _, _) in TIPOS.items() if t not in {d.tipo for d in docs} and t in ("contrato_social", "cnd_federal", "crf_fgts", "cndt", "cnd_estadual", "cnd_municipal")]
        if faltando:
            rprint("[yellow]Faltando no cofre (mínimo para habilitação):[/yellow] " + ", ".join(faltando))
