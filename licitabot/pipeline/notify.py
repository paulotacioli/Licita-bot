"""E-mails: aprovação (com botões), alertas, bloqueios, confirmação de envio; e polling IMAP de respostas."""

from __future__ import annotations

import email
import imaplib
import logging
import re
import smtplib
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import make_msgid
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlmodel import select

from licitabot.approval.service import decidir_por_email, montar_snapshot
from licitabot.approval.tokens import expiracao_para, gerar_token, snapshot_hash, token_hash
from licitabot.config import get_settings, load_empresa
from licitabot.db.models import Aprovacao, DocumentoGerado, ExecucaoPortal, Item, Oportunidade, utcnow
from licitabot.db.session import db_session, log_evento, set_status
from licitabot.pipeline.analysis import get_requisitos
from licitabot.pipeline.states import Status

log = logging.getLogger(__name__)

ASSUNTO_RX = re.compile(r"\[LICITABOT#(\d+)\]")
MAX_ANEXOS_BYTES = 18 * 1024 * 1024


def _env() -> Environment:
    s = get_settings()
    return Environment(loader=FileSystemLoader(str(s.templates_path / "email")), autoescape=select_autoescape(["html", "j2"]))


def _fmt_brl(v: float | None) -> str:
    if v is None:
        return "-"
    return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def enviar_email(assunto: str, html: str, anexos: list[Path] | None = None, inline_png: list[Path] | None = None, headers: dict | None = None) -> str:
    s = get_settings()
    if not (s.smtp_user and s.smtp_password and s.owner_email):
        raise RuntimeError("SMTP_USER/SMTP_PASSWORD/OWNER_EMAIL não configurados no .env")
    msg = EmailMessage()
    msg["From"] = s.smtp_user
    msg["To"] = s.owner_email
    msg["Subject"] = assunto
    msg_id = make_msgid(domain="licitabot.local")
    msg["Message-ID"] = msg_id
    for k, v in (headers or {}).items():
        msg[k] = v
    msg.set_content("Este e-mail requer um cliente com suporte a HTML.")
    msg.add_alternative(html, subtype="html")
    total = 0
    for i, png in enumerate(inline_png or []):
        data = png.read_bytes()
        msg.get_payload()[1].add_related(data, "image", "png", cid=f"<shot{i}@licitabot>")
    for p in anexos or []:
        data = p.read_bytes()
        total += len(data)
        if total > MAX_ANEXOS_BYTES:
            log.warning("Anexos excedem o limite; %s omitido", p.name)
            continue
        msg.add_attachment(data, maintype="application", subtype="pdf" if p.suffix.lower() == ".pdf" else "octet-stream", filename=p.name)
    with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=60) as smtp:
        smtp.starttls()
        smtp.login(s.smtp_user, s.smtp_password)
        smtp.send_message(msg)
    return msg_id


def _dia_hoje() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def envios_hoje(session=None) -> int:
    """Quantas licitações já foram enviadas ao dono hoje (informativas + aprovação)."""
    from licitabot.db.models import Evento

    def _count(s) -> int:
        evs = s.exec(select(Evento).where(Evento.tipo == "email_oportunidade", Evento.criado_em >= utcnow() - timedelta(days=2))).all()
        return sum(1 for e in evs if e.dados_json.get("dia") == _dia_hoje())

    if session is not None:
        return _count(session)
    with db_session() as s:
        return _count(s)


def limite_diario_atingido(session=None) -> bool:
    return envios_hoje(session) >= get_settings().limite_diario_envios


def _registrar_envio(session, op: Oportunidade, canal: str, msg_id: str) -> None:
    log_evento(session, "email_oportunidade", f"{canal} ({msg_id})", op.id, dia=_dia_hoje(), canal=canal)


def _screenshots_recentes(session, op: Oportunidade, n: int = 3) -> list[Path]:
    ex = session.exec(select(ExecucaoPortal).where(ExecucaoPortal.oportunidade_id == op.id, ExecucaoPortal.etapa == "preparar").order_by(ExecucaoPortal.id.desc())).first()
    if not ex:
        return []
    shots = [Path(p) for p in ex.screenshots if Path(p).exists()]
    return shots[-n:]


def send_approval_email(oportunidade_id: int) -> None:
    """Cria a Aprovacao (token + snapshot), envia o e-mail e muda o status para AGUARDANDO_APROVACAO."""
    s = get_settings()
    emp = load_empresa()
    with db_session() as session:
        op = session.get(Oportunidade, oportunidade_id)
        if not op:
            raise ValueError("oportunidade inexistente")
        # invalida pendentes anteriores
        for ap_old in session.exec(select(Aprovacao).where(Aprovacao.oportunidade_id == op.id, Aprovacao.decisao == "pendente")):
            ap_old.decisao = "expirada"
            session.add(ap_old)
        snap = montar_snapshot(session, op)
        shash = snapshot_hash(snap)
        ap = Aprovacao(oportunidade_id=op.id, token_hash="", expira_em=expiracao_para(op.data_encerramento_proposta), snapshot_json=snap, snapshot_hash=shash)
        session.add(ap)
        session.flush()
        tok_aprovar = gerar_token(op.id, ap.id, "aprovar", shash)
        tok_rejeitar = gerar_token(op.id, ap.id, "rejeitar", shash)
        ap.token_hash = token_hash(tok_aprovar)
        session.add(ap)

        itens = session.exec(select(Item).where(Item.oportunidade_id == op.id).order_by(Item.numero_item)).all()
        docs = session.exec(select(DocumentoGerado).where(DocumentoGerado.oportunidade_id == op.id).order_by(DocumentoGerado.ordem)).all()
        req = get_requisitos(session, op)
        shots = _screenshots_recentes(session, op)
        base = s.public_base_url.rstrip("/")
        prazo = op.data_encerramento_proposta
        horas = (prazo - datetime.now()).total_seconds() / 3600 if prazo else None
        total = sum((i.valor_total_proposto or 0) for i in itens if i.participar)
        est = sum((i.valor_total_estimado or 0) for i in itens if i.participar) or op.valor_estimado or 0
        html = _env().get_template("aprovacao.html.j2").render(
            op=op,
            emp=emp,
            itens=itens,
            docs=docs,
            req=req,
            total=_fmt_brl(total),
            estimado=_fmt_brl(est) if est else "-",
            desconto=f"{(1 - total / est) * 100:.1f}%" if est and total else "-",
            horas_restantes=f"{horas:.0f} h" if horas is not None else "?",
            prazo=prazo.strftime("%d/%m/%Y %H:%M") if prazo else "?",
            expira=ap.expira_em.strftime("%d/%m/%Y %H:%M"),
            link_aprovar=f"{base}/aprovar/{tok_aprovar}" if base else "",
            link_rejeitar=f"{base}/rejeitar/{tok_rejeitar}" if base else "",
            dashboard=f"http://{s.web_host}:{s.web_port}/oportunidades/{op.id}",
            shots=list(range(len(shots))),
            fmt=_fmt_brl,
            dry_run=s.dry_run,
        )
        anexos = [Path(d.caminho_pdf) for d in docs if d.caminho_pdf and Path(d.caminho_pdf).exists() and d.tipo in ("proposta_comercial", "proposta_tecnica", "checklist")]
        assunto = f"[LICITABOT#{op.id}] Aprovar proposta? {op.orgao_nome[:40]} — {_fmt_brl(total)} — prazo {prazo:%d/%m %H:%M}" if prazo else f"[LICITABOT#{op.id}] Aprovar proposta? {op.orgao_nome[:40]}"
        msg_id = enviar_email(assunto, html, anexos=anexos, inline_png=shots)
        _registrar_envio(session, op, "aprovacao", msg_id)
        set_status(session, op, Status.AGUARDANDO_APROVACAO, f"e-mail enviado; expira {ap.expira_em:%d/%m %H:%M}")
        session.commit()


def send_info_email(oportunidade_id: int, motivo_portal: str) -> None:
    """E-mail informativo (sem botões): licitação analisada, precificada e com documentos prontos, mas o envio pelo
    portal ainda não é possível (onboarding incompleto ou portal não suportado). Conta no limite diário."""
    s = get_settings()
    emp = load_empresa()
    with db_session() as session:
        op = session.get(Oportunidade, oportunidade_id)
        itens = session.exec(select(Item).where(Item.oportunidade_id == op.id).order_by(Item.numero_item)).all()
        docs = session.exec(select(DocumentoGerado).where(DocumentoGerado.oportunidade_id == op.id).order_by(DocumentoGerado.ordem)).all()
        req = get_requisitos(session, op)
        prazo = op.data_encerramento_proposta
        horas = (prazo - datetime.now()).total_seconds() / 3600 if prazo else None
        total = sum((i.valor_total_proposto or 0) for i in itens if i.participar)
        est = sum((i.valor_total_estimado or 0) for i in itens if i.participar) or op.valor_estimado or 0
        html = _env().get_template("aprovacao.html.j2").render(
            op=op, emp=emp, itens=itens, docs=docs, req=req,
            total=_fmt_brl(total), estimado=_fmt_brl(est) if est else "-",
            desconto=f"{(1 - total / est) * 100:.1f}%" if est and total else "-",
            horas_restantes=f"{horas:.0f} h" if horas is not None else "?",
            prazo=prazo.strftime("%d/%m/%Y %H:%M") if prazo else "?",
            expira="", link_aprovar="", link_rejeitar="",
            dashboard=f"http://{s.web_host}:{s.web_port}/oportunidades/{op.id}",
            shots=[], fmt=_fmt_brl, dry_run=s.dry_run,
            informativo=True, motivo_portal=motivo_portal,
            link_pncp=f"https://pncp.gov.br/app/editais/{op.orgao_cnpj}/{op.ano}/{op.sequencial}",
        )
        anexos = [Path(d.caminho_pdf) for d in docs if d.caminho_pdf and Path(d.caminho_pdf).exists() and d.tipo in ("proposta_comercial", "proposta_tecnica", "indice")]
        assunto = f"[LICITABOT#{op.id}] Licitação compatível: {op.orgao_nome[:40]} — {_fmt_brl(total)}" + (f" — prazo {prazo:%d/%m %H:%M}" if prazo else "")
        msg_id = enviar_email(assunto, html, anexos=anexos)
        _registrar_envio(session, op, "informativo", msg_id)
        session.commit()


def send_daily_digest() -> None:
    """Resumo diário: novas licitações compatíveis (últimas 24h), e-mails enviados, fila, bloqueios e onboarding."""
    from licitabot.db.models import Evento

    s = get_settings()
    agora = datetime.now()
    with db_session() as session:
        desde = utcnow() - timedelta(hours=24)
        novas = session.exec(
            select(Oportunidade).where(Oportunidade.criado_em >= desde, Oportunidade.status != Status.DESCARTADA).order_by(Oportunidade.data_encerramento_proposta)
        ).all()
        relevantes = session.exec(
            select(Oportunidade).where(Oportunidade.status.in_([Status.TRIADA_RELEVANTE, Status.ANALISADA, Status.PRECIFICADA, Status.DOCS_GERADOS, Status.PREPARADA_PORTAL, Status.AGUARDANDO_APROVACAO])).order_by(Oportunidade.data_encerramento_proposta)
        ).all()
        fila = session.exec(select(Oportunidade).where(Oportunidade.status.in_([Status.DESCOBERTA, Status.BAIXADA, Status.TRIADA_RELEVANTE, Status.ANALISADA, Status.PRECIFICADA, Status.DOCS_GERADOS]))).all()
        erros = session.exec(select(Oportunidade).where(Oportunidade.status == Status.ERRO)).all()
        ontem = (agora - timedelta(days=1)).strftime("%Y-%m-%d")
        evs = session.exec(select(Evento).where(Evento.tipo == "email_oportunidade", Evento.criado_em >= utcnow() - timedelta(days=2))).all()
        enviados_ontem = sum(1 for e in evs if e.dados_json.get("dia") == ontem)
        from licitabot.sicaf.checklist import onboarding_completo

        ok_onb, faltando = onboarding_completo()
        html = _env().get_template("resumo_diario.html.j2").render(
            data=agora.strftime("%d/%m/%Y"), novas=novas, relevantes=relevantes, fila=len(fila), erros=len(erros),
            enviados_ontem=enviados_ontem, limite=s.limite_diario_envios, ok_onb=ok_onb, faltando=faltando,
            fmt=_fmt_brl, dashboard=f"http://{s.web_host}:{s.web_port}/",
            pncp=lambda o: f"https://pncp.gov.br/app/editais/{o.orgao_cnpj}/{o.ano}/{o.sequencial}",
        )
        enviar_email(f"[LICITABOT] Resumo diário {agora:%d/%m}: {len(novas)} novas, {len(relevantes)} em análise, fila {len(fila)}", html)
        log_evento(session, "email", f"resumo diário: {len(novas)} novas", None)
        session.commit()


def enviar_alerta(assunto: str, titulo: str, linhas: list[str], op: Oportunidade | None = None, anexos: list[Path] | None = None) -> None:
    html = _env().get_template("alerta.html.j2").render(titulo=titulo, linhas=linhas, op=op)
    tag = f"[LICITABOT#{op.id}] " if op else "[LICITABOT] "
    try:
        enviar_email(tag + assunto, html, anexos=anexos)
    except Exception as e:  # noqa: BLE001
        log.error("Falha ao enviar alerta '%s': %s", assunto, e)


def lembretes_pendentes() -> int:
    """Lembretes a 24h e 6h da expiração; expira aprovações vencidas."""
    n = 0
    with db_session() as session:
        pend = session.exec(select(Aprovacao).where(Aprovacao.decisao == "pendente")).all()
        agora = datetime.now()
        for ap in pend:
            op = session.get(Oportunidade, ap.oportunidade_id)
            if agora > ap.expira_em:
                ap.decisao = "expirada"
                session.add(ap)
                set_status(session, op, Status.EXPIRADA, "sem decisão até a expiração")
                enviar_alerta("Aprovação expirou", "Aprovação expirada sem decisão", [f"{op.orgao_nome}: {op.objeto[:200]}"], op)
                n += 1
                continue
            restante = ap.expira_em - agora
            if restante <= timedelta(hours=6) and not ap.lembrete_6h_enviado:
                enviar_alerta("Lembrete: 6h para decidir", "Faltam 6 horas", [f"{op.orgao_nome}: {op.objeto[:200]}", "Use os links do e-mail anterior ou o dashboard."], op)
                ap.lembrete_6h_enviado = True
                session.add(ap)
                n += 1
            elif restante <= timedelta(hours=24) and not ap.lembrete_24h_enviado:
                enviar_alerta("Lembrete: 24h para decidir", "Faltam 24 horas", [f"{op.orgao_nome}: {op.objeto[:200]}"], op)
                ap.lembrete_24h_enviado = True
                session.add(ap)
                n += 1
        session.commit()
    return n


def poll_replies() -> int:
    """Fallback: lê respostas 'APROVAR'/'REJEITAR' na caixa de entrada (IMAP) do próprio remetente."""
    s = get_settings()
    if not (s.imap_host and s.smtp_user and s.smtp_password):
        return 0
    n = 0
    try:
        M = imaplib.IMAP4_SSL(s.imap_host, s.imap_port)
        M.login(s.smtp_user, s.smtp_password)
        M.select("INBOX")
        _, data = M.search(None, '(UNSEEN SUBJECT "LICITABOT#")')
        for num in data[0].split():
            _, raw = M.fetch(num, "(RFC822)")
            msg = email.message_from_bytes(raw[0][1])
            remetente = email.utils.parseaddr(msg.get("From", ""))[1].lower()
            if remetente != s.owner_email.lower():
                continue
            m = ASSUNTO_RX.search(msg.get("Subject", ""))
            if not m:
                continue
            corpo = ""
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    corpo = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", errors="ignore")
                    break
            primeira = next((ln.strip().upper() for ln in corpo.splitlines() if ln.strip()), "")
            if primeira.startswith("APROV"):
                ok, txt = decidir_por_email(int(m.group(1)), True, remetente)
            elif primeira.startswith("REJEIT") or primeira.startswith("NAO") or primeira.startswith("NÃO"):
                ok, txt = decidir_por_email(int(m.group(1)), False, remetente)
            else:
                continue
            log.info("Decisão por e-mail #%s: %s (%s)", m.group(1), txt, ok)
            M.store(num, "+FLAGS", "\\Seen")
            n += 1
        M.logout()
    except Exception as e:  # noqa: BLE001
        log.warning("IMAP indisponível: %s", e)
    return n
