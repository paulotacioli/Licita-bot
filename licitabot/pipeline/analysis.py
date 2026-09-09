"""Análise do edital: extração estruturada de requisitos (EditalRequisitos) com o PDF completo."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from sqlmodel import select

from licitabot.config import get_settings
from licitabot.db.models import Analise, ArquivoEdital, Item, Oportunidade
from licitabot.db.session import db_session, log_evento, set_status
from licitabot.llm.client import get_llm_client, pdf_block, text_block
from licitabot.llm.prompts import prompt_version, render_prompt
from licitabot.llm.schemas import EditalRequisitos
from licitabot.pipeline.ingest import texto_completo
from licitabot.pipeline.states import Status
from licitabot.pncp.schemas import parse_dt

log = logging.getLogger(__name__)

MAX_PDF_BLOCKS = 6
MAX_TEXTO_CHARS = 600_000  # ~150k tokens de texto extraído como fallback


def _blocos_documentos(session, op: Oportunidade) -> tuple[list[dict], list[str]]:
    """PDFs relevantes como blocos `document`; retorna também os que ficaram de fora."""
    regs = list(session.exec(select(ArquivoEdital).where(ArquivoEdital.oportunidade_id == op.id)))
    ordem = {"edital": 0, "termo_referencia": 1, "modelo_declaracao": 2, "minuta_contrato": 3, "anexo": 4, "planilha": 5}
    regs = [r for r in regs if r.caminho_local.lower().endswith(".pdf")]
    regs.sort(key=lambda r: (ordem.get(r.papel, 9), -r.paginas))
    blocos: list[dict] = []
    fora: list[str] = []
    for r in regs:
        if len(blocos) >= MAX_PDF_BLOCKS:
            fora.append(r.titulo)
            continue
        b = pdf_block(Path(r.caminho_local))
        if b:
            blocos.append(b)
        else:
            fora.append(r.titulo)
    return blocos, fora


def analyze(oportunidade_id: int, client=None) -> EditalRequisitos:
    s = get_settings()
    client = client or get_llm_client()
    with db_session() as session:
        op = session.get(Oportunidade, oportunidade_id)
        if not op:
            raise ValueError(f"Oportunidade {oportunidade_id} não existe")
        itens = list(session.exec(select(Item).where(Item.oportunidade_id == op.id).order_by(Item.numero_item)))
        if getattr(client, "supports_pdf", False):
            blocos, fora = _blocos_documentos(session, op)
        else:
            blocos, fora = [], ["(backend sem suporte a PDF: usando texto extraído)"]
        texto_extra = ""
        if fora or not blocos:
            # Arquivos grandes/escaneados entram como texto extraído
            papeis = None
            texto_extra = texto_completo(session, op, max_chars=MAX_TEXTO_CHARS, papeis=papeis)
            if not blocos and not texto_extra.strip():
                set_status(session, op, Status.ERRO, "sem texto legível no edital (precisa OCR?)")
                session.commit()
                raise RuntimeError("Edital sem texto legível")
        prompt = render_prompt(
            "extracao_edital",
            op=op,
            itens=itens[:60],
            prazo=op.data_encerramento_proposta.strftime("%Y-%m-%dT%H:%M") if op.data_encerramento_proposta else "?",
            texto_extraido=texto_extra,
        )
        blocks = blocos + [text_block(prompt)]
        req, meta = client.structured(s.llm_model_analise, EditalRequisitos, blocks, effort="high", max_tokens=16000)

        # Persistência
        pasta = Path(op.pasta) if op.pasta else s.oportunidades_path / str(op.id)
        pasta.mkdir(parents=True, exist_ok=True)
        (pasta / "analise.json").write_text(req.model_dump_json(indent=2), encoding="utf-8")
        existente = session.exec(select(Analise).where(Analise.oportunidade_id == op.id)).first()
        an = existente or Analise(oportunidade_id=op.id)
        an.modelo_llm = meta.model
        an.versao_prompt = prompt_version("extracao_edital")
        an.json_requisitos = json.loads(req.model_dump_json())
        an.confianca = req.confianca
        an.alertas = list(req.alertas)
        an.tokens_in = meta.tokens_in
        an.tokens_out = meta.tokens_out
        an.criado_em = datetime.utcnow()
        session.add(an)

        # Enriquecimento da oportunidade
        if req.prazos.encerramento_propostas:
            dt = parse_dt(req.prazos.encerramento_propostas)
            if dt and op.data_encerramento_proposta and abs((dt - op.data_encerramento_proposta).total_seconds()) > 3600:
                log_evento(session, "alerta", f"prazo divergente edital={dt} pncp={op.data_encerramento_proposta}", op.id)
            if dt and not op.data_encerramento_proposta:
                op.data_encerramento_proposta = dt
        if req.prazos.sessao_publica:
            op.data_sessao = parse_dt(req.prazos.sessao_publica) or op.data_sessao
        if req.uasg and not op.uasg:
            op.uasg = req.uasg

        # Cruza itens da análise com os do PNCP (tipo + horas sugeridas)
        por_num = {i.numero_item: i for i in itens}
        for ir in req.itens:
            alvo = por_num.get(ir.numero_item) if ir.numero_item else None
            if alvo is None and len(itens) == 1:
                alvo = itens[0]
            if alvo is None:
                continue
            alvo.tipo_precificacao = {"saas": "saas", "desenvolvimento": "desenvolvimento", "manutencao": "manutencao"}.get(ir.tipo, "outro")
            alvo.horas_estimadas = ir.horas_estimadas_sugeridas
            session.add(alvo)

        criticos = []
        if not (req.prazos.encerramento_propostas or op.data_encerramento_proposta):
            criticos.append("prazo de encerramento")
        if req.criterio_julgamento == "outro":
            criticos.append("critério de julgamento")
        if criticos:
            set_status(session, op, Status.ERRO, "análise sem campos críticos: " + ", ".join(criticos))
        else:
            motivo = f"confiança {req.confianca:.2f}; {len(req.documentos_exigidos)} docs; {len(req.alertas)} alertas"
            set_status(session, op, Status.ANALISADA, motivo)
        session.commit()
        return req


def get_requisitos(session, op: Oportunidade) -> EditalRequisitos | None:
    an = session.exec(select(Analise).where(Analise.oportunidade_id == op.id)).first()
    if not an:
        return None
    return EditalRequisitos.model_validate(an.json_requisitos)
