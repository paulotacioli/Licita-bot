"""Worker: APScheduler com os jobs recorrentes. Um único processo; jobs nunca concorrem entre si (max_instances=1)."""

from __future__ import annotations

import logging
import time
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from licitabot.config import get_settings, hora_resumo
from licitabot.db.session import init_db
from licitabot.logging_setup import setup_logging

log = logging.getLogger(__name__)


def job_discover() -> None:
    from licitabot.pipeline.discovery import run_discovery

    log.info("job_discover: %s", run_discovery())


def job_process() -> None:
    from licitabot.pipeline.pretriagem import pretriar_pendentes
    from licitabot.pipeline.runner import process_pending, retomar_bloqueadas

    pre = pretriar_pendentes()
    if pre.get("avaliadas"):
        log.info("job_process: pré-triagem %s", pre)
    n = retomar_bloqueadas()
    if n:
        log.info("job_process: %s bloqueadas retomadas", n)
    log.info("job_process: %s", process_pending(limite=10, ate="notify"))


def job_approvals() -> None:
    from licitabot.pipeline.notify import lembretes_pendentes, poll_replies
    from licitabot.pipeline.submit import enviar_aprovadas

    s = get_settings()
    if not s.public_base_url:
        poll_replies()
    lembretes_pendentes()
    enviar_aprovadas()


def job_monitor() -> None:
    from licitabot.pipeline.postsubmit import monitorar_enviadas

    monitorar_enviadas()


def job_certidoes() -> None:
    from licitabot.cofre.index import scan_cofre, vencendo
    from licitabot.pipeline.notify import enviar_alerta

    scan_cofre()
    docs = vencendo(7)
    if docs:
        enviar_alerta(
            "Certidões vencendo",
            "Documentos do cofre vencendo em 7 dias",
            [f"{d.tipo}: válido até {d.valido_ate:%d/%m/%Y} ({d.titulo})" for d in docs] + ["Emita a nova versão e salve no cofre (ver cofre/README.md)."],
        )


def job_relatorio_semanal() -> None:
    from sqlalchemy import func
    from sqlmodel import select

    from licitabot.db.models import Oportunidade
    from licitabot.db.session import db_session
    from licitabot.pipeline.notify import enviar_alerta

    with db_session() as session:
        rows = session.exec(select(Oportunidade.status, func.count()).group_by(Oportunidade.status)).all()
    enviar_alerta("Relatório semanal", f"Situação em {datetime.now():%d/%m/%Y}", [f"{st}: {n}" for st, n in sorted(rows)])


def job_resumo_diario() -> None:
    from licitabot.pipeline.notify import send_daily_digest

    send_daily_digest()


def build_scheduler() -> BackgroundScheduler:
    s = get_settings()
    sched = BackgroundScheduler(timezone="America/Sao_Paulo", job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 600})
    sched.add_job(job_discover, CronTrigger(hour="6-20/2", minute=5), id="discover", name="Descoberta PNCP")
    sched.add_job(job_discover, CronTrigger(hour=23, minute=30), id="discover_noite")
    sched.add_job(job_resumo_diario, CronTrigger(hour=hora_resumo(), minute=15), id="resumo_diario", name="Resumo diário por e-mail")
    sched.add_job(job_process, IntervalTrigger(minutes=15), id="process", name="Pipeline")
    sched.add_job(job_approvals, IntervalTrigger(minutes=5), id="approvals", name="Aprovações/envio")
    sched.add_job(job_monitor, IntervalTrigger(minutes=5), id="monitor", name="Monitor pós-envio")
    sched.add_job(job_certidoes, CronTrigger(hour=7, minute=0), id="certidoes", name="Validade de certidões")
    sched.add_job(job_relatorio_semanal, CronTrigger(day_of_week="mon", hour=8, minute=0), id="relatorio")
    return sched


def start_worker() -> None:
    setup_logging()
    init_db()
    sched = build_scheduler()
    sched.start()
    log.info("Worker iniciado. Jobs: %s", [j.id for j in sched.get_jobs()])
    try:
        while True:
            time.sleep(30)
    except (KeyboardInterrupt, SystemExit):
        sched.shutdown(wait=False)
