"""Geração de documentos: cruza exigências do edital com templates e cofre, renderiza docx→pdf, valida e monta o checklist."""

from __future__ import annotations

import json
import logging
import re
import shutil
from datetime import datetime
from pathlib import Path

from docxtpl import DocxTemplate
from sqlmodel import select

from licitabot.cofre.index import documentos_validos, scan_cofre
from licitabot.config import get_settings, load_empresa, load_precos
from licitabot.db.models import ArquivoEdital, DocumentoCofre, DocumentoGerado, Item, Oportunidade
from licitabot.db.session import db_session, log_evento, set_status
from licitabot.docs.build_templates import build_all
from licitabot.docs.convert import docx_to_pdf_many
from licitabot.docs.declaracoes import DECLARACOES, PADRAO_SEMPRE, PORTE_EXTENSO
from licitabot.llm.schemas import CompatAtestado, DeclaracaoAjustada, EditalRequisitos, PropostaTecnicaRedigida
from licitabot.pipeline.analysis import get_requisitos
from licitabot.pipeline.states import Status
from licitabot.pncp.files import sha256_of

log = logging.getLogger(__name__)

MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"]
TIPOS_COFRE = {
    "contrato_social", "cartao_cnpj", "documento_representante", "procuracao", "cnd_federal", "crf_fgts", "cndt",
    "cnd_estadual", "cnd_municipal", "certidao_falencia", "balanco_patrimonial", "indices_financeiros",
    "atestado_capacidade_tecnica", "registro_conselho", "certificacao", "sicaf",
}
TITULOS = {
    "proposta_comercial": "Proposta comercial",
    "proposta_tecnica": "Proposta técnica",
    "planilha_custos": "Planilha de composição de custos",
    "indice": "Relação de documentos",
    "contrato_social": "Contrato social",
    "cartao_cnpj": "Comprovante de inscrição CNPJ",
    "documento_representante": "Documento do representante legal",
    "procuracao": "Procuração",
    "cnd_federal": "Certidão negativa federal (RFB/PGFN)",
    "crf_fgts": "Certificado de regularidade do FGTS",
    "cndt": "Certidão negativa de débitos trabalhistas",
    "cnd_estadual": "Certidão negativa estadual",
    "cnd_municipal": "Certidão negativa municipal",
    "certidao_falencia": "Certidão negativa de falência",
    "balanco_patrimonial": "Balanço patrimonial",
    "indices_financeiros": "Demonstrativo de índices financeiros",
    "atestado_capacidade_tecnica": "Atestado de capacidade técnica",
    "registro_conselho": "Registro em conselho de classe",
    "certificacao": "Certificação",
    "sicaf": "Declaração SICAF",
}


def fmt_brl(v: float | None) -> str:
    if v is None:
        return "-"
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def por_extenso(valor: float) -> str:
    """Valor monetário por extenso (pt-BR), suficiente para propostas (até bilhões)."""
    unidades = ["", "um", "dois", "três", "quatro", "cinco", "seis", "sete", "oito", "nove", "dez", "onze", "doze", "treze", "quatorze", "quinze", "dezesseis", "dezessete", "dezoito", "dezenove"]
    dezenas = ["", "", "vinte", "trinta", "quarenta", "cinquenta", "sessenta", "setenta", "oitenta", "noventa"]
    centenas = ["", "cento", "duzentos", "trezentos", "quatrocentos", "quinhentos", "seiscentos", "setecentos", "oitocentos", "novecentos"]

    def ate_999(n: int) -> str:
        if n == 0:
            return ""
        if n == 100:
            return "cem"
        c, r = divmod(n, 100)
        d, u = divmod(r, 10)
        partes = []
        if c:
            partes.append(centenas[c])
        if r < 20 and r > 0:
            partes.append(unidades[r])
        else:
            if d:
                partes.append(dezenas[d])
            if u:
                partes.append(unidades[u])
        return " e ".join(partes)

    def inteiro(n: int) -> str:
        if n == 0:
            return "zero"
        grupos = [("bilhão", "bilhões"), ("milhão", "milhões"), ("mil", "mil"), ("", "")]
        divs = [10**9, 10**6, 10**3, 1]
        partes: list[tuple[str, int]] = []
        for (sing, plur), dv in zip(grupos, divs):
            q, n = divmod(n, dv)
            if q:
                if dv == 1000 and q == 1:
                    partes.append(("mil", q))
                else:
                    partes.append(((ate_999(q) + " " + (sing if q == 1 else plur)).strip(), q))
        # "e" antes do último grupo só se ele for < 100 ou centena redonda; senão separa por vírgula/espaço
        txt = partes[0][0]
        for i in range(1, len(partes)):
            nome, val = partes[i]
            ultimo = i == len(partes) - 1
            if ultimo and (val < 100 or val % 100 == 0):
                txt += " e " + nome
            elif i == len(partes) - 1 and partes[i - 1][0] == "mil":
                txt += " " + nome
            else:
                txt += ", " + nome if not (partes[i - 1][0] == "mil") else " " + nome
        return txt

    reais = int(valor)
    cent = int(round((valor - reais) * 100))
    txt = inteiro(reais) + (" real" if reais == 1 else " reais")
    if cent:
        txt += " e " + inteiro(cent) + (" centavo" if cent == 1 else " centavos")
    return txt


def _data_extenso(d: datetime) -> str:
    return f"{d.day} de {MESES[d.month - 1]} de {d.year}"


def _numero_edital(op: Oportunidade, req: EditalRequisitos | None) -> str:
    if req and req.numero_edital:
        n = req.numero_edital
        return n if re.search(r"preg|conc|edital|dispensa", n, re.I) else f"{op.modalidade_nome} nº {n}"
    if op.numero_compra:
        return f"{op.modalidade_nome} nº {op.numero_compra}"
    return f"{op.modalidade_nome} — PNCP {op.numero_controle_pncp}"


def _ctx_base(op: Oportunidade, req: EditalRequisitos | None) -> dict:
    emp = load_empresa()
    return {
        "empresa": emp.model_dump(),
        "op": op,
        "numero_edital": _numero_edital(op, req),
        "objeto": (req.objeto_resumido if req else op.objeto)[:600],
        "data_extenso": _data_extenso(datetime.now()),
    }


def _texto_decl(chave: str, op: Oportunidade, req: EditalRequisitos | None) -> tuple[str, str]:
    emp = load_empresa()
    d = DECLARACOES[chave]
    campos = {
        "razao_social": emp.razao_social,
        "cnpj": emp.cnpj,
        "endereco": f"{emp.endereco}, {emp.cidade}/{emp.uf}, CEP {emp.cep}",
        "representante": emp.representante.nome,
        "cpf": emp.representante.cpf,
        "cargo": emp.representante.cargo,
        "orgao": op.orgao_nome,
        "numero_edital": _numero_edital(op, req),
        "objeto": (req.objeto_resumido if req else op.objeto)[:300],
        "cidade": emp.cidade,
        "data_extenso": _data_extenso(datetime.now()),
        "porte_extenso": PORTE_EXTENSO.get(emp.porte, emp.porte),
    }
    return d["titulo"], d["texto"].format(**campos)


def _render(template: str, ctx: dict, dest_docx: Path) -> Path:
    s = get_settings()
    tpl = DocxTemplate(str(s.templates_path / "docs" / template))
    tpl.render(ctx, autoescape=True)
    dest_docx.parent.mkdir(parents=True, exist_ok=True)
    tpl.save(str(dest_docx))
    return dest_docx


def _to_pdf(docx: Path) -> tuple[Path | None, str]:
    """Adia a conversão: o PDF é gerado em lote no fim (uma única sessão do Word). Retorna o caminho previsto."""
    return docx.with_suffix(".pdf"), ""


def _converter_todos(registros: list[DocumentoGerado]) -> None:
    docxs = [Path(d.caminho_docx) for d in registros if d.origem == "gerado" and d.caminho_docx]
    erros = docx_to_pdf_many(docxs)
    for d in registros:
        if d.origem != "gerado" or not d.caminho_docx:
            continue
        p = Path(d.caminho_docx)
        if p in erros or not p.with_suffix(".pdf").exists():
            d.caminho_pdf = ""
            d.observacao = (d.observacao + "; " if d.observacao else "") + f"sem conversor PDF: {erros.get(p, 'pdf ausente')}"[:200]


def _modelo_no_edital(session, op: Oportunidade, tipo: str) -> str:
    """Procura no texto dos anexos um modelo de declaração compatível (retorna trecho ou '')."""
    chaves = {
        "decl_habilitacao": r"requisitos de habilita",
        "decl_me_epp": r"microempresa|pequeno porte",
        "decl_menor": r"menor de dezoito|trabalho noturno",
        "decl_fato_impeditivo": r"fato[s]? impeditivo",
        "decl_elaboracao_independente": r"elabora[çc][ãa]o independente",
        "decl_pcd": r"reserva de cargos|pessoa com defici",
        "decl_lgpd": r"prote[çc][ãa]o de dados",
        "decl_nepotismo": r"nepotismo|parentesco",
        "decl_trabalho_degradante": r"degradante|escravo",
    }
    rx = chaves.get(tipo)
    if not rx:
        return ""
    regs = session.exec(select(ArquivoEdital).where(ArquivoEdital.oportunidade_id == op.id, ArquivoEdital.texto_extraido == True)).all()  # noqa: E712
    for r in sorted(regs, key=lambda r: 0 if r.papel == "modelo_declaracao" else 1):
        t = Path(r.caminho_texto).read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(r"(?i)(ANEXO[^\n]{0,80}\n)?[^\n]{0,80}DECLARA[ÇC][ÃA]O[^\n]{0,120}\n((?:.|\n){0,2500})", t):
            bloco = m.group(0)
            if re.search(rx, bloco, re.I) and "modelo" in t[max(0, m.start() - 400): m.start()].lower() + bloco.lower():
                return bloco[:3000]
    return ""


def generate(oportunidade_id: int, client=None) -> None:
    s = get_settings()
    emp = load_empresa()
    precos = load_precos()
    build_all()
    scan_cofre()
    with db_session() as session:
        op = session.get(Oportunidade, oportunidade_id)
        if not op:
            raise ValueError("oportunidade inexistente")
        req = get_requisitos(session, op)
        itens = session.exec(select(Item).where(Item.oportunidade_id == op.id, Item.participar == True).order_by(Item.numero_item)).all()  # noqa: E712
        if not itens:
            set_status(session, op, Status.DESCARTADA, "sem itens para propor")
            session.commit()
            return
        pasta = Path(op.pasta) / "proposta"
        if pasta.exists():
            shutil.rmtree(pasta)
        pasta.mkdir(parents=True)
        for old in session.exec(select(DocumentoGerado).where(DocumentoGerado.oportunidade_id == op.id)):
            session.delete(old)
        session.flush()

        ordem = 0
        registros: list[DocumentoGerado] = []

        def add(tipo: str, titulo: str, **kw) -> DocumentoGerado:
            nonlocal ordem
            ordem += 1
            d = DocumentoGerado(oportunidade_id=op.id, tipo=tipo, titulo=titulo, ordem=ordem, **kw)
            registros.append(d)
            return d

        base = _ctx_base(op, req)
        exigidos = {d.tipo: d for d in (req.documentos_exigidos if req else [])}
        assinatura_icp = bool(req and req.proposta_formato.exige_assinatura_digital_icp)
        total = sum(i.valor_total_proposto or 0 for i in itens)

        # ---- 1. Proposta comercial (sempre) ----
        ctx = dict(base)
        ctx.update(
            itens=[
                {
                    "numero_item": i.numero_item,
                    "descricao": i.descricao_detalhada_proposta or i.descricao,
                    "unidade": i.unidade,
                    "quantidade_fmt": f"{i.quantidade:g}",
                    "marca_modelo": i.marca_modelo or "Serviço",
                    "valor_unit_fmt": fmt_brl(i.valor_unit_proposto),
                    "valor_total_fmt": fmt_brl(i.valor_total_proposto),
                }
                for i in itens
            ],
            total_fmt=fmt_brl(total),
            total_extenso=por_extenso(total),
            validade_dias=por_extenso(float(max(emp.validade_proposta_dias, (req.prazos.validade_minima_proposta_dias if req else 0) or 0))).replace(" reais", "").replace(" real", ""),
            validade_dias_num=max(emp.validade_proposta_dias, (req.prazos.validade_minima_proposta_dias if req else 0) or 0),
            prazo_execucao=(req.prazos.prazo_execucao if req and req.prazos.prazo_execucao else "conforme Termo de Referência"),
            observacoes="",
        )
        docx = _render("proposta_comercial.docx", ctx, pasta / "01_proposta_comercial.docx")
        pdf, obs = _to_pdf(docx)
        add("proposta_comercial", TITULOS["proposta_comercial"], caminho_docx=str(docx), caminho_pdf=str(pdf or ""), observacao=obs, precisa_assinatura_icp=assinatura_icp)

        # ---- 2. Planilha de custos (se exigida) ----
        if (req and (req.proposta_formato.exige_planilha_custos or "planilha_custos" in exigidos)):
            ctx2 = dict(ctx)
            ctx2.update(
                itens=[dict(x, justificativa_preco=i.justificativa_preco) for x, i in zip(ctx["itens"], itens)],
                margem_pct=f"{precos.margem_minima * 100:.0f}",
                impostos_pct=f"{precos.fator_impostos * 100:.0f}",
                valor_hora_fmt=fmt_brl(precos.valor_hora_padrao),
            )
            docx = _render("planilha_custos.docx", ctx2, pasta / "02_planilha_custos.docx")
            pdf, obs = _to_pdf(docx)
            add("planilha_custos", TITULOS["planilha_custos"], caminho_docx=str(docx), caminho_pdf=str(pdf or ""), observacao=obs)

        # ---- 3. Proposta técnica (técnica e preço, ou exigida) ----
        if req and (req.criterio_julgamento in ("tecnica_e_preco", "melhor_tecnica") or "proposta_tecnica" in exigidos):
            try:
                from licitabot.llm.client import get_llm_client, text_block
                from licitabot.llm.prompts import render_prompt

                cl = client or get_llm_client()
                prompt = render_prompt("redacao_proposta_tecnica", empresa_json=emp.model_dump_json(indent=2), op=op, req=req)
                red, _ = cl.structured(s.llm_model_redacao, PropostaTecnicaRedigida, [text_block(prompt)], effort="high", max_tokens=16000)
                ctx3 = dict(base)
                ctx3.update(resumo_executivo=red.resumo_executivo, secoes=[sec.model_dump() for sec in red.secoes], premissas=red.premissas)
                docx = _render("proposta_tecnica.docx", ctx3, pasta / "03_proposta_tecnica.docx")
                pdf, obs = _to_pdf(docx)
                add("proposta_tecnica", TITULOS["proposta_tecnica"], caminho_docx=str(docx), caminho_pdf=str(pdf or ""), observacao=obs or "revise antes de aprovar", precisa_assinatura_icp=assinatura_icp)
            except Exception as e:  # noqa: BLE001
                log.exception("proposta técnica falhou")
                add("proposta_tecnica", TITULOS["proposta_tecnica"], status="FALTANDO", observacao=f"redação falhou: {e}"[:300])

        # ---- 4. Declarações ----
        decl_tipos = list(PADRAO_SEMPRE)
        if emp.porte in ("ME", "EPP"):
            decl_tipos.append("decl_me_epp")
        for t in exigidos:
            if t.startswith("decl_") and t in DECLARACOES and t not in decl_tipos:
                decl_tipos.append(t)
        n = 10
        for t in decl_tipos:
            titulo, texto = _texto_decl(t, op, req)
            modelo = _modelo_no_edital(session, op, t) if (t in exigidos and exigidos[t].modelo_no_anexo) or t in exigidos else ""
            obs = ""
            if modelo:
                try:
                    from licitabot.llm.client import get_llm_client, text_block
                    from licitabot.llm.prompts import render_prompt

                    cl = client or get_llm_client()
                    prompt = render_prompt("ajuste_declaracao", empresa=emp, op=op, numero_edital=base["numero_edital"], data_extenso=base["data_extenso"], tipo=titulo, modelo=modelo)
                    adj, _ = cl.structured(s.llm_model_redacao, DeclaracaoAjustada, [text_block(prompt)], effort="medium", max_tokens=4000)
                    titulo, texto = adj.titulo or titulo, adj.texto
                    obs = "ajustada ao modelo do edital" + (": " + "; ".join(adj.diferencas_vs_modelo_padrao)[:150] if adj.diferencas_vs_modelo_padrao else "")
                except Exception as e:  # noqa: BLE001
                    obs = f"modelo do edital encontrado, ajuste automático falhou ({e})"[:200]
            n += 1
            ctxd = dict(base, titulo=titulo, texto=texto)
            docx = _render("declaracao.docx", ctxd, pasta / f"{n:02d}_{t}.docx")
            pdf, obs2 = _to_pdf(docx)
            add(t, titulo.title(), caminho_docx=str(docx), caminho_pdf=str(pdf or ""), observacao=obs or obs2, exigido_pelo_edital=t in exigidos, precisa_assinatura_icp=assinatura_icp)

        # ---- 5. Documentos do cofre (habilitação) ----
        n = 30
        # SICAF é pré-requisito de cadastro (checklist de onboarding), não um anexo do cofre
        tipos_cofre = [t for t in exigidos if t in TIPOS_COFRE and t != "sicaf"]
        for t in ("cnd_federal", "crf_fgts", "cndt", "contrato_social"):  # sempre úteis
            if t not in tipos_cofre:
                tipos_cofre.append(t)
        prazo_ref = op.data_sessao or op.data_encerramento_proposta or datetime.now()
        for t in tipos_cofre:
            ex = exigidos.get(t)
            fase = ex.fase if ex else "habilitacao"
            docs = documentos_validos(session, t, em=prazo_ref)
            if t == "atestado_capacidade_tecnica":
                docs = _selecionar_atestados(session, docs, req, op, client)
            if docs:
                for d in docs if t == "atestado_capacidade_tecnica" else docs[:1]:
                    n += 1
                    dest = pasta / f"{n:02d}_{t}{Path(d.caminho).suffix}"
                    shutil.copy2(d.caminho, dest)
                    venc = f"válido até {d.valido_ate:%d/%m/%Y}" if d.valido_ate else ""
                    add(t, TITULOS.get(t, t), origem="cofre", caminho_pdf=str(dest), status="do_cofre", exigido_pelo_edital=bool(ex), observacao=venc)
            else:
                vencidos = session.exec(select(DocumentoCofre).where(DocumentoCofre.tipo == t, DocumentoCofre.ativo == True)).all()  # noqa: E712
                st = "VENCIDO" if vencidos else "FALTANDO"
                obs = (ex.descricao_literal[:200] if ex else "") or f"colocar no cofre como {t}_<data>.pdf"
                if fase == "proposta" and ex and ex.obrigatorio:
                    obs = "BLOQUEANTE (exigido junto com a proposta): " + obs
                add(t, TITULOS.get(t, t), origem="cofre", status=st, exigido_pelo_edital=bool(ex), observacao=obs)

        # ---- 6. Índice ----
        for d in registros:
            session.add(d)
        session.flush()
        ctx6 = dict(base, documentos=[{"ordem": d.ordem, "titulo": d.titulo, "fase": "proposta" if d.tipo.startswith(("proposta", "decl", "planilha")) else "habilitação", "arquivo": Path(d.caminho_pdf or d.caminho_docx).name if (d.caminho_pdf or d.caminho_docx) else d.status} for d in registros])
        docx = _render("indice_documentos.docx", ctx6, pasta / "00_indice_documentos.docx")
        pdf, obs = _to_pdf(docx)
        add("indice", TITULOS["indice"], caminho_docx=str(docx), caminho_pdf=str(pdf or ""), observacao=obs, exigido_pelo_edital=False)
        session.add(registros[-1])

        # ---- 7. Conversão em lote, hash, validação, checklist ----
        _converter_todos(registros)
        problemas = validar(registros, op, emp, itens)
        for d in registros:
            p = Path(d.caminho_pdf or d.caminho_docx) if (d.caminho_pdf or d.caminho_docx) else None
            if p and p.exists():
                d.hash_sha256 = sha256_of(p)
            if d.precisa_assinatura_icp and d.status == "gerado":
                d.status = "PRECISA_ASSINATURA"
                d.observacao = (d.observacao + "; " if d.observacao else "") + "assinar com e-CNPJ e salvar em proposta/assinados/"
            session.add(d)
        checklist = {
            "gerado_em": datetime.now().isoformat(),
            "documentos": [{"ordem": d.ordem, "tipo": d.tipo, "titulo": d.titulo, "status": d.status, "arquivo": d.caminho_pdf or d.caminho_docx, "obs": d.observacao} for d in registros],
            "problemas": problemas,
        }
        (pasta / "checklist.json").write_text(json.dumps(checklist, ensure_ascii=False, indent=2), encoding="utf-8")

        bloqueantes = [d for d in registros if d.status in ("FALTANDO", "VENCIDO") and d.observacao.startswith("BLOQUEANTE")]
        faltando_hab = [d for d in registros if d.status in ("FALTANDO", "VENCIDO") and d.exigido_pelo_edital and d not in bloqueantes]
        sem_pdf = [d for d in registros if d.status == "gerado" and not d.caminho_pdf]
        if problemas:
            set_status(session, op, Status.ERRO, "validação de documentos: " + "; ".join(problemas)[:800])
        elif bloqueantes:
            set_status(session, op, Status.BLOQUEADA, "documentos obrigatórios com a proposta faltando no cofre: " + ", ".join(d.tipo for d in bloqueantes))
            _avisar_bloqueio(op, bloqueantes + faltando_hab)
        elif sem_pdf:
            set_status(session, op, Status.ERRO, "sem conversor docx→pdf (instale Word ou LibreOffice)")
        else:
            motivo = f"{len(registros)} documentos"
            if faltando_hab:
                motivo += f"; habilitação pendente no cofre: {', '.join(d.tipo for d in faltando_hab)}"
            set_status(session, op, Status.DOCS_GERADOS, motivo)
        log_evento(session, "docgen", json.dumps(checklist["documentos"], ensure_ascii=False)[:3000], op.id)
        session.commit()


def _selecionar_atestados(session, docs, req: EditalRequisitos | None, op: Oportunidade, client) -> list:
    if not docs or not req or not req.qualificacao_tecnica.atestados:
        return docs[:3]
    exig = req.qualificacao_tecnica.atestados
    try:
        from licitabot.llm.client import get_llm_client, text_block
        from licitabot.llm.prompts import render_prompt
        from licitabot.pipeline.ingest import extrair_texto_pdf

        cl = client or get_llm_client()
        s = get_settings()
        escolhidos = []
        for d in docs:
            txt_path = Path(d.caminho).with_suffix(".txt")
            if not txt_path.exists():
                extrair_texto_pdf(Path(d.caminho), txt_path)
            texto = txt_path.read_text(encoding="utf-8", errors="ignore")[:12000]
            prompt = render_prompt("compat_atestado", exigencia=exig.model_dump_json(), objeto=req.objeto_resumido, atestado=texto)
            comp, _ = cl.structured(s.llm_model_triagem, CompatAtestado, [text_block(prompt)], effort="low", max_tokens=1500)
            if comp.compativel:
                escolhidos.append(d)
        return escolhidos or docs[:1]
    except Exception as e:  # noqa: BLE001
        log.warning("compatibilidade de atestados não verificada: %s", e)
        return docs[:3]


def validar(registros: list[DocumentoGerado], op: Oportunidade, emp, itens) -> list[str]:
    problemas: list[str] = []
    cnpj_digits = re.sub(r"\D", "", emp.cnpj)
    for d in registros:
        p = Path(d.caminho_pdf) if d.caminho_pdf else None
        if not p or not p.exists():
            continue
        try:
            from pypdf import PdfReader

            reader = PdfReader(str(p))
            if len(reader.pages) == 0:
                problemas.append(f"{d.tipo}: PDF vazio")
                continue
            if d.origem == "gerado":
                texto = "\n".join((pg.extract_text() or "") for pg in reader.pages)
                if "{{" in texto or "{%" in texto:
                    problemas.append(f"{d.tipo}: placeholder não substituído")
                if cnpj_digits and re.sub(r"\D", "", texto).find(cnpj_digits) < 0:
                    problemas.append(f"{d.tipo}: CNPJ da empresa não aparece no PDF")
                if d.tipo == "proposta_comercial":
                    for i in itens:
                        if fmt_brl(i.valor_unit_proposto) not in texto:
                            problemas.append(f"proposta_comercial: valor do item {i.numero_item} não encontrado no PDF")
                            break
            if p.stat().st_size > 25 * 1024 * 1024:
                problemas.append(f"{d.tipo}: arquivo maior que 25 MB")
        except Exception as e:  # noqa: BLE001
            problemas.append(f"{d.tipo}: PDF ilegível ({e})")
        d.validado = not any(pr.startswith(d.tipo) for pr in problemas)
    return problemas


def _avisar_bloqueio(op: Oportunidade, docs: list[DocumentoGerado]) -> None:
    try:
        from licitabot.pipeline.notify import enviar_alerta

        enviar_alerta(
            "Documentos faltando no cofre",
            "Oportunidade bloqueada: faltam documentos",
            [f"{d.titulo} ({d.tipo}): {d.observacao}" for d in docs] + ["Coloque os arquivos em cofre/ (ver cofre/README.md). O sistema retoma sozinho."],
            op,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("aviso de bloqueio não enviado: %s", e)

