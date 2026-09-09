"""CLI do Licita-Bot (Typer)."""

from __future__ import annotations

import logging
from datetime import datetime

import typer
from rich import print as rprint
from rich.console import Console
from rich.table import Table
from sqlmodel import select

from licitabot.logging_setup import setup_logging

app = typer.Typer(help="Licita-Bot: automação de licitações de software (PNCP + Compras.gov.br)", no_args_is_help=True)
portal_app = typer.Typer(help="Automação do Compras.gov.br", no_args_is_help=True)
onboarding_app = typer.Typer(help="Cadastro SICAF / gov.br / cofre", no_args_is_help=True)
cofre_app = typer.Typer(help="Cofre de documentos", no_args_is_help=True)
app.add_typer(portal_app, name="portal")
app.add_typer(onboarding_app, name="onboarding")
app.add_typer(cofre_app, name="cofre")
console = Console()


@app.callback()
def _init(verbose: bool = typer.Option(False, "--verbose", "-v")):
    setup_logging(logging.DEBUG if verbose else logging.INFO)


@app.command("init-db")
def init_db_cmd():
    """Cria o banco SQLite e as pastas de dados."""
    from licitabot.db.session import init_db

    init_db()
    rprint("[green]Banco e pastas criados.[/green]")


@app.command()
def discover(
    termo: list[str] = typer.Option(None, "--termo", "-t", help="Termos de busca (padrão: config/triagem.yaml)"),
    max_paginas: int = typer.Option(None, help="Máximo de páginas por termo"),
    refiltrar: bool = typer.Option(False, "--refiltrar", help="Só reaplica o filtro local às oportunidades em DESCOBERTA"),
):
    """Busca editais abertos no PNCP e grava oportunidades novas."""
    from licitabot.db.session import init_db
    from licitabot.pipeline.discovery import reaplicar_filtro, run_discovery

    init_db()
    if refiltrar:
        rprint({"descartadas_pelo_filtro": reaplicar_filtro()})
        return
    stats = run_discovery(termos=termo or None, max_paginas=max_paginas)
    rprint(stats)


@app.command()
def ingest(ids: list[str] = typer.Argument(..., help="IDs ou números de controle PNCP")):
    """Baixa arquivos, itens e detalhe de uma ou mais oportunidades."""
    from licitabot.db.session import db_session, get_oportunidade
    from licitabot.pipeline.ingest import ingest as _ingest

    for ref in ids:
        with db_session() as session:
            op = get_oportunidade(session, ref)
            if not op:
                rprint(f"[red]{ref} não encontrada[/red]")
                continue
            oid = op.id
        _ingest(oid)
        rprint(f"[green]{ref}: ingerida[/green]")


@app.command()
def run(
    ref: str = typer.Argument(..., help="ID ou número de controle PNCP"),
    step: str = typer.Option(None, "--step", help="ingest|triage|analysis|pricing|docgen|prepare|notify|submit|monitor"),
    ate: str = typer.Option("notify", "--ate", help="Executa etapas em sequência até esta (padrão: notify)"),
):
    """Executa uma etapa específica ou a sequência de etapas de uma oportunidade."""
    from licitabot.pipeline.runner import run_step, run_until

    if step:
        run_step(ref, step)
    else:
        run_until(ref, ate)


@app.command()
def pretriar(limite: int = typer.Option(None, help="Máximo de oportunidades nesta rodada (padrão: perfil.maximo_por_rodada)")):
    """Pré-triagem por IA: classifica pelo objeto as oportunidades em DESCOBERTA, sem baixar edital."""
    from licitabot.db.session import init_db
    from licitabot.pipeline.pretriagem import ativa, pretriar_pendentes

    init_db()
    if not ativa():
        rprint("[yellow]Pré-triagem desligada ou perfil vazio. Preencha em Configurações > Perfil de interesse.[/yellow]")
        raise typer.Exit(1)
    rprint(pretriar_pendentes(limite=limite))


@app.command()
def process(
    limite: int = typer.Option(20, help="Máximo de oportunidades por execução"),
    ate: str = typer.Option("notify", "--ate", help="Etapa final automática"),
):
    """Avança todas as oportunidades pendentes pelo pipeline (o que o worker faz periodicamente)."""
    from licitabot.pipeline.runner import process_pending

    stats = process_pending(limite=limite, ate=ate)
    rprint(stats)


@app.command("list")
def list_cmd(
    status: str = typer.Option(None, help="Filtra por status"),
    limite: int = typer.Option(50),
):
    """Lista oportunidades."""
    from licitabot.db.models import Oportunidade
    from licitabot.db.session import db_session

    with db_session() as session:
        q = select(Oportunidade).order_by(Oportunidade.data_encerramento_proposta)
        if status:
            q = q.where(Oportunidade.status == status.upper())
        ops = session.exec(q.limit(limite)).all()
        t = Table(title=f"Oportunidades ({len(ops)})")
        for c in ("ID", "Status", "Prazo", "UF", "Órgão", "Portal", "Valor est.", "Objeto"):
            t.add_column(c, overflow="fold")
        for o in ops:
            t.add_row(
                str(o.id),
                o.status,
                o.data_encerramento_proposta.strftime("%d/%m %H:%M") if o.data_encerramento_proposta else "-",
                o.uf,
                o.orgao_nome[:40],
                o.portal,
                f"{o.valor_estimado:,.0f}" if o.valor_estimado else "-",
                o.objeto[:90],
            )
        console.print(t)


@app.command()
def show(ref: str):
    """Mostra detalhes de uma oportunidade (status, itens, análise, documentos)."""
    from licitabot.db.models import DocumentoGerado, Evento, Item, Triagem
    from licitabot.db.session import db_session, get_oportunidade
    from licitabot.pipeline.analysis import get_requisitos

    with db_session() as session:
        op = get_oportunidade(session, ref)
        if not op:
            rprint("[red]não encontrada[/red]")
            raise typer.Exit(1)
        rprint(f"[bold]{op.numero_controle_pncp}[/bold] — {op.orgao_nome} ({op.uf}) — {op.modalidade_nome}")
        rprint(f"Status: [cyan]{op.status}[/cyan] {op.status_motivo}")
        rprint(f"Prazo: {op.data_encerramento_proposta} | Portal: {op.portal} | Link: {op.link_sistema_origem}")
        rprint(f"Objeto: {op.objeto}")
        tri = session.exec(select(Triagem).where(Triagem.oportunidade_id == op.id).order_by(Triagem.id.desc())).first()
        if tri:
            rprint(f"Triagem: score {tri.score} / {tri.categoria} — {tri.justificativa}")
        itens = session.exec(select(Item).where(Item.oportunidade_id == op.id).order_by(Item.numero_item)).all()
        if itens:
            t = Table(title=f"Itens ({len(itens)}{', mostrando 30' if len(itens) > 30 else ''})")
            for c in ("Nº", "Descrição", "Qtd", "Un", "Est. unit", "Proposto unit", "Participar", "Justificativa"):
                t.add_column(c, overflow="fold")
            for i in itens[:30]:
                t.add_row(
                    str(i.numero_item), i.descricao[:70], f"{i.quantidade:g}", i.unidade,
                    f"{i.valor_unit_estimado or 0:,.2f}", f"{i.valor_unit_proposto or 0:,.2f}",
                    "sim" if i.participar else "não", i.justificativa_preco[:60],
                )
            console.print(t)
        req = get_requisitos(session, op)
        if req:
            rprint(f"Análise: critério={req.criterio_julgamento} por={req.julgamento_por} ME/EPP={req.beneficio_me_epp} confiança={req.confianca}")
            for d in req.documentos_exigidos:
                rprint(f"  - [{d.fase}] {d.tipo}: {d.descricao_literal[:100]} ({d.referencia})")
            for a in req.alertas:
                rprint(f"  [yellow]! {a}[/yellow]")
        docs = session.exec(select(DocumentoGerado).where(DocumentoGerado.oportunidade_id == op.id)).all()
        for d in docs:
            rprint(f"  doc {d.tipo}: {d.status} {d.caminho_pdf or d.caminho_docx} {d.observacao}")
        evs = session.exec(select(Evento).where(Evento.oportunidade_id == op.id).order_by(Evento.id.desc()).limit(8)).all()
        for e in reversed(evs):
            rprint(f"  {e.criado_em:%d/%m %H:%M} {e.tipo}: {e.mensagem[:120]}")


@app.command()
def approve(ref: str, rejeitar: bool = typer.Option(False, "--rejeitar")):
    """Aprova (ou rejeita) manualmente pela CLI uma oportunidade AGUARDANDO_APROVACAO."""
    from licitabot.approval.service import decidir_por_cli

    ok, msg = decidir_por_cli(ref, aprovar=not rejeitar)
    rprint(("[green]" if ok else "[red]") + msg)


@app.command("resumo-diario")
def resumo_diario():
    """Envia agora o e-mail de resumo diário (novas licitações compatíveis, fila, bloqueios)."""
    from licitabot.pipeline.notify import send_daily_digest

    send_daily_digest()
    rprint("[green]Resumo diário enviado.[/green]")


@app.command("llm-teste")
def llm_teste():
    """Faz uma chamada mínima à IA (Claude Code ou API) para validar a configuração."""
    from pydantic import BaseModel

    from licitabot.config import get_settings
    from licitabot.llm.client import get_llm_client, text_block

    class Ping(BaseModel):
        ok: bool
        mensagem: str

    s = get_settings()
    cl = get_llm_client()
    rprint(f"Backend: {s.llm_backend} · modelo de triagem: {s.llm_model_triagem}")
    obj, meta = cl.structured(s.llm_model_triagem, Ping, [text_block("Responda ok=true e uma saudação curta em português.")], effort="low", max_tokens=200)
    rprint(f"[green]OK[/green] {obj.mensagem} (modelo {meta.model}, {meta.tokens_in}+{meta.tokens_out} tokens)")


@app.command("email-teste")
def email_teste():
    """Envia um e-mail de teste para validar SMTP/OWNER_EMAIL do .env."""
    from licitabot.pipeline.notify import enviar_email

    msg_id = enviar_email("[LICITABOT] Teste de e-mail", "<p>Configuração de e-mail do Licita-Bot funcionando.</p>")
    rprint(f"[green]Enviado ({msg_id}).[/green]")


@app.command()
def worker():
    """Inicia o scheduler (descoberta, processamento, envio, monitoramento)."""
    from licitabot.scheduler import start_worker

    start_worker()


@app.command()
def web(host: str = typer.Option(None), port: int = typer.Option(None)):
    """Inicia o dashboard + endpoint de aprovação."""
    import uvicorn

    from licitabot.config import get_settings

    s = get_settings()
    uvicorn.run("licitabot.web.app:app", host=host or s.web_host, port=port or s.web_port, reload=False)


@app.command()
def status():
    """Resumo por status + saúde do onboarding."""
    from sqlalchemy import func

    from licitabot.db.models import Oportunidade
    from licitabot.db.session import db_session
    from licitabot.sicaf.checklist import resumo_onboarding

    with db_session() as session:
        rows = session.exec(select(Oportunidade.status, func.count()).group_by(Oportunidade.status)).all()
        t = Table(title=f"Status em {datetime.now():%d/%m/%Y %H:%M}")
        t.add_column("Status")
        t.add_column("Qtd")
        for st, n in sorted(rows):
            t.add_row(st, str(n))
        console.print(t)
    rprint(resumo_onboarding())


# ---------- portal ----------


@portal_app.command("login")
def portal_login():
    """Abre o navegador com o perfil persistente para você fazer login no gov.br/Compras.gov.br."""
    from licitabot.portal.comprasgov.session import login_interativo

    login_interativo()


@portal_app.command("smoke")
def portal_smoke():
    """Valida sessão e seletores críticos do Compras.gov.br (sem alterar nada)."""
    from licitabot.portal.comprasgov.monitor import smoke_test

    smoke_test()


@portal_app.command("prepare")
def portal_prepare(ref: str):
    """Prepara a proposta no portal (preenche e anexa, NÃO envia)."""
    from licitabot.pipeline.runner import run_step

    run_step(ref, "prepare")


# ---------- onboarding ----------


@onboarding_app.command("checklist")
def onboarding_checklist():
    """Mostra o checklist de cadastro (e-CNPJ, gov.br, SICAF, login, cofre, configs)."""
    from licitabot.sicaf.checklist import imprimir_checklist

    imprimir_checklist()


@onboarding_app.command("cnpj")
def onboarding_cnpj(cnpj: str = typer.Argument(None, help="CNPJ (padrão: config/empresa.yaml)")):
    """Consulta o CNPJ na Receita (BrasilAPI) e sugere dados para config/empresa.yaml."""
    from licitabot.sicaf.checklist import consultar_cnpj

    consultar_cnpj(cnpj)


@onboarding_app.command("concluir")
def onboarding_concluir(chave: str):
    """Marca um passo do checklist como concluído (ex.: e_cnpj, govbr, sicaf_nivel1)."""
    from licitabot.sicaf.checklist import concluir_passo

    concluir_passo(chave)


@onboarding_app.command("kit-sicaf")
def onboarding_kit():
    """Gera a pasta 'kit SICAF' com os documentos do cofre organizados por nível + guia."""
    from licitabot.sicaf.checklist import gerar_kit_sicaf

    gerar_kit_sicaf()


# ---------- cofre ----------


@cofre_app.command("scan")
def cofre_scan():
    """Indexa os documentos do cofre e mostra validade."""
    from licitabot.cofre.index import scan_cofre, imprimir_cofre

    scan_cofre()
    imprimir_cofre()


if __name__ == "__main__":
    app()


@app.command("painel-senha")
def painel_senha(
    usuario: str = typer.Option(None, "--usuario", "-u", help="Usuário (padrão: o primeiro cadastrado)"),
):
    """Define ou redefine a senha de acesso ao painel web (uso no servidor, quando esquecer a senha)."""
    from sqlmodel import select

    from licitabot.db.models import UsuarioPainel
    from licitabot.db.session import db_session, init_db
    from licitabot.web.auth import forca_da_senha, gerar_hash

    init_db()
    senha = typer.prompt("Nova senha", hide_input=True, confirmation_prompt=True)
    motivo = forca_da_senha(senha)
    if motivo:
        rprint(f"[red]{motivo}[/red]")
        raise typer.Exit(1)
    with db_session() as session:
        if usuario:
            u = session.exec(select(UsuarioPainel).where(UsuarioPainel.usuario == usuario.strip().lower())).first()
        else:
            u = session.exec(select(UsuarioPainel).order_by(UsuarioPainel.id)).first()
        if u:
            u.senha_hash = gerar_hash(senha)
            session.add(u)
            rprint(f"[green]Senha de '{u.usuario}' redefinida.[/green]")
        else:
            nome = (usuario or "admin").strip().lower()
            session.add(UsuarioPainel(usuario=nome, senha_hash=gerar_hash(senha)))
            rprint(f"[green]Usuário '{nome}' criado.[/green]")
        session.commit()
