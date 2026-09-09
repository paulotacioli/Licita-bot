"""Ingestão: baixa arquivos/itens/detalhe do PNCP, extrai texto dos PDFs e classifica os anexos."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path

from sqlmodel import select

from licitabot.config import get_settings
from licitabot.db.models import ArquivoEdital, Item, Oportunidade
from licitabot.db.session import db_session, log_evento, set_status
from licitabot.pipeline.states import Status
from licitabot.pncp.client import PNCPClient
from licitabot.pncp.files import download_arquivo, extract_zip, sha256_of
from licitabot.pncp.schemas import ArquivoPNCP, CompraDetalhe, ItemPNCP, parse_dt

log = logging.getLogger(__name__)

PORTAIS = [
    ("comprasgov", re.compile(r"compras\.gov\.br|comprasnet|serpro\.gov\.br", re.I)),
    ("licitanet", re.compile(r"licitanet", re.I)),
    ("bll", re.compile(r"bll\.org|bllcompras", re.I)),
    ("bnc", re.compile(r"bnc\.org|bnccompras", re.I)),
    ("pcp", re.compile(r"portaldecompraspublicas", re.I)),
    ("bec", re.compile(r"bec\.sp\.gov", re.I)),
    ("licitacoes-e", re.compile(r"licitacoes-e\.com|bb\.com\.br", re.I)),
    ("compras-br", re.compile(r"comprasbr", re.I)),
]


def detectar_portal(link: str | None) -> str:
    if not link:
        return "desconhecido"
    for nome, rx in PORTAIS:
        if rx.search(link):
            return nome
    return "outro"


def classificar_papel(nome: str) -> str:
    n = nome.lower()
    if re.search(r"termo.?de.?refer|\btr\b|_tr_|tr\.pdf", n):
        return "termo_referencia"
    if "minuta" in n or "contrato" in n:
        return "minuta_contrato"
    if "declara" in n or "modelo" in n:
        return "modelo_declaracao"
    if "planilha" in n or n.endswith((".xls", ".xlsx", ".csv")):
        return "planilha"
    if "edital" in n or "aviso" in n or "pregao" in n or "pregão" in n:
        return "edital"
    return "anexo"


def extrair_texto_pdf(pdf: Path, txt_out: Path) -> tuple[int, bool]:
    """Extrai texto com pdfplumber. Retorna (páginas, precisa_ocr)."""
    import pdfplumber

    partes: list[str] = []
    paginas = 0
    chars = 0
    with pdfplumber.open(pdf) as doc:
        for i, page in enumerate(doc.pages, start=1):
            paginas += 1
            try:
                t = page.extract_text() or ""
            except Exception:  # noqa: BLE001
                t = ""
            chars += len(t)
            partes.append(f"\n\n===== PÁGINA {i} =====\n{t}")
    txt_out.write_text("".join(partes), encoding="utf-8")
    precisa_ocr = paginas > 0 and (chars / paginas) < 200
    return paginas, precisa_ocr


def ingest(oportunidade_id: int) -> None:
    s = get_settings()
    with db_session() as session:
        op = session.get(Oportunidade, oportunidade_id)
        if not op:
            raise ValueError(f"Oportunidade {oportunidade_id} não existe")
        pasta = s.oportunidades_path / str(op.id)
        edital_dir = pasta / "edital"
        edital_dir.mkdir(parents=True, exist_ok=True)
        op.pasta = str(pasta)

        with PNCPClient() as client:
            # --- detalhe ---
            try:
                det = CompraDetalhe.model_validate(client.compra_detalhe(op.orgao_cnpj, op.ano, op.sequencial))
                (pasta / "detalhe.json").write_text(det.model_dump_json(indent=2), encoding="utf-8")
                op.objeto = det.objetoCompra or op.objeto
                op.informacao_complementar = det.informacaoComplementar or ""
                op.modo_disputa = det.modoDisputaNome or ""
                op.srp = bool(det.srp)
                op.link_sistema_origem = det.linkSistemaOrigem or ""
                op.portal = detectar_portal(det.linkSistemaOrigem)
                op.numero_compra = det.numeroCompra or ""
                op.valor_estimado = det.valorTotalEstimado or op.valor_estimado
                op.data_abertura_proposta = parse_dt(det.dataAberturaProposta) or op.data_abertura_proposta
                op.data_encerramento_proposta = parse_dt(det.dataEncerramentoProposta) or op.data_encerramento_proposta
                if det.unidadeOrgao:
                    op.uasg = str(det.unidadeOrgao.get("codigoUnidade") or "")
                    op.unidade_nome = det.unidadeOrgao.get("nomeUnidade") or op.unidade_nome
                if det.modalidadeNome and not op.modalidade_nome:
                    op.modalidade_nome = det.modalidadeNome
            except Exception as e:  # noqa: BLE001
                log.warning("Detalhe indisponível para %s: %s", op.numero_controle_pncp, e)
                log_evento(session, "aviso", f"detalhe indisponível: {e}", op.id)

            # --- itens ---
            try:
                itens_raw = client.compra_itens(op.orgao_cnpj, op.ano, op.sequencial)
                (pasta / "itens.json").write_text(json.dumps(itens_raw, ensure_ascii=False, indent=2), encoding="utf-8")
                existentes = {i.numero_item: i for i in session.exec(select(Item).where(Item.oportunidade_id == op.id))}
                for raw in itens_raw:
                    ip = ItemPNCP.model_validate(raw)
                    it = existentes.get(ip.numeroItem) or Item(oportunidade_id=op.id, numero_item=ip.numeroItem)
                    it.descricao = ip.descricao
                    it.quantidade = ip.quantidade or 0
                    it.unidade = ip.unidadeMedida
                    it.valor_unit_estimado = ip.valorUnitarioEstimado
                    it.valor_total_estimado = ip.valorTotal
                    it.tipo_beneficio = ip.tipoBeneficioNome
                    it.criterio_julgamento = ip.criterioJulgamentoNome
                    it.situacao = ip.situacaoCompraItemNome
                    it.raw_json = raw
                    session.add(it)
                if not op.valor_estimado and itens_raw:
                    op.valor_estimado = sum((r.get("valorTotal") or 0) for r in itens_raw) or None
            except Exception as e:  # noqa: BLE001
                log.warning("Itens indisponíveis para %s: %s", op.numero_controle_pncp, e)
                log_evento(session, "aviso", f"itens indisponíveis: {e}", op.id)

            # --- arquivos ---
            arquivos = [ArquivoPNCP.model_validate(a) for a in client.compra_arquivos(op.orgao_cnpj, op.ano, op.sequencial)]
            arquivos = [a for a in arquivos if a.statusAtivo]
            if not arquivos:
                set_status(session, op, Status.ERRO, "edital sem arquivos no PNCP")
                session.commit()
                return
            ja = {a.url: a for a in session.exec(select(ArquivoEdital).where(ArquivoEdital.oportunidade_id == op.id))}
            for a in arquivos:
                if a.url in ja and Path(ja[a.url].caminho_local).exists():
                    continue
                try:
                    path = download_arquivo(client, a.url, a.titulo, edital_dir, a.sequencialDocumento)
                except Exception as e:  # noqa: BLE001
                    log.warning("Falha no download %s: %s", a.url, e)
                    log_evento(session, "aviso", f"download falhou: {a.titulo}: {e}", op.id)
                    continue
                arquivos_finais = [path]
                if path.suffix.lower() == ".zip":
                    try:
                        arquivos_finais = extract_zip(path, edital_dir / f"{path.stem}_extraido")
                    except Exception as e:  # noqa: BLE001
                        log.warning("Falha ao extrair %s: %s", path, e)
                        arquivos_finais = []
                for f in arquivos_finais:
                    reg = ArquivoEdital(
                        oportunidade_id=op.id,
                        sequencial_doc=a.sequencialDocumento,
                        titulo=f.name,
                        tipo=a.tipoDocumentoNome,
                        url=a.url,
                        caminho_local=str(f),
                        hash_sha256=sha256_of(f),
                        tamanho_bytes=f.stat().st_size,
                        papel=classificar_papel(f.name) if a.tipoDocumentoNome.lower() != "edital" else classificar_papel(f.name),
                    )
                    if f.suffix.lower() == ".pdf":
                        txt = f.with_suffix(".txt")
                        try:
                            reg.paginas, reg.precisa_ocr = extrair_texto_pdf(f, txt)
                            reg.texto_extraido = True
                            reg.caminho_texto = str(txt)
                        except Exception as e:  # noqa: BLE001
                            log.warning("Falha ao extrair texto de %s: %s", f, e)
                    session.add(reg)

        # Se nenhum arquivo foi classificado como "edital", promove o maior PDF com texto
        regs = list(session.exec(select(ArquivoEdital).where(ArquivoEdital.oportunidade_id == op.id)))
        if regs and not any(r.papel == "edital" for r in regs):
            pdfs = [r for r in regs if r.caminho_local.lower().endswith(".pdf")]
            if pdfs:
                maior = max(pdfs, key=lambda r: r.paginas)
                maior.papel = "edital"
                session.add(maior)

        op.atualizado_em = datetime.utcnow()
        set_status(session, op, Status.BAIXADA, f"{len(regs)} arquivo(s); portal={op.portal}")
        session.commit()


def texto_completo(session, op: Oportunidade, max_chars: int | None = None, papeis: tuple[str, ...] | None = None) -> str:
    """Concatena o texto extraído dos arquivos (edital primeiro, depois TR, depois o resto)."""
    ordem = {"edital": 0, "termo_referencia": 1, "modelo_declaracao": 2, "minuta_contrato": 3, "planilha": 4, "anexo": 5}
    regs = list(session.exec(select(ArquivoEdital).where(ArquivoEdital.oportunidade_id == op.id)))
    regs = [r for r in regs if r.texto_extraido and r.caminho_texto and (not papeis or r.papel in papeis)]
    regs.sort(key=lambda r: (ordem.get(r.papel, 9), -r.paginas))
    partes: list[str] = []
    total = 0
    for r in regs:
        t = Path(r.caminho_texto).read_text(encoding="utf-8", errors="ignore")
        bloco = f"\n\n########## ARQUIVO: {r.titulo} (papel={r.papel}) ##########\n{t}"
        if max_chars and total + len(bloco) > max_chars:
            bloco = bloco[: max(0, max_chars - total)]
        partes.append(bloco)
        total += len(bloco)
        if max_chars and total >= max_chars:
            break
    return "".join(partes)
