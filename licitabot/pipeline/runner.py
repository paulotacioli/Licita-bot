"""Orquestração: executa etapas do pipeline por oportunidade, com gates de prazo/onboarding e tratamento de erro."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Callable

from sqlmodel import select

from licitabot.config import get_settings, limite_diario
from licitabot.db.models import Oportunidade
from licitabot.db.session import db_session, get_oportunidade, set_status
from licitabot.pipeline.states import STEPS, Status, next_step_for

log = logging.getLogger(__name__)

ORDEM_ETAPAS = ["ingest", "triage", "analysis", "pricing", "docgen", "prepare", "notify", "submit", "monitor"]
MAX_TENTATIVAS = 3


def _fn(step: str) -> Callable[[int], object]:
    if step == "ingest":
        from licitabot.pipeline.ingest import ingest

        return ingest
    if step == "triage":
        from licitabot.pipeline.triage import triage

        return triage
    if step == "analysis":
        from licitabot.pipeline.analysis import analyze

        return analyze
    if step == "pricing":
        from licitabot.pipeline.pricing import price

        return price
    if step == "docgen":
        from licitabot.pipeline.docgen import generate

        return generate
    if step == "prepare":
        from licitabot.portal.comprasgov.proposta import prepare_proposal

        return prepare_proposal
    if step == "notify":
        from licitabot.pipeline.notify import send_approval_email

        return send_approval_email
    if step == "submit":
        from licitabot.pipeline.submit import submit

        return submit
    if step == "monitor":
        from licitabot.pipeline.postsubmit import monitor

        return monitor
    raise ValueError(f"etapa desconhecida: {step}")


class Adiar(Exception):
    """A etapa não pode rodar agora, mas não é bloqueio: fica na fila sem mudar o status (ex.: limite diário)."""


def _gate(session, op: Oportunidade, step: str) -> str | None:
    """Retorna motivo de bloqueio ou None. Aplica prazo, portal, onboarding e limite diário de envios."""
    s = get_settings()
    agora = datetime.now()
    if op.data_encerramento_proposta and op.data_encerramento_proposta < agora and step not in ("monitor",):
        return "prazo de proposta já encerrado"
    if step in ("prepare", "notify"):
        from licitabot.pipeline.notify import limite_diario_atingido

        if limite_diario_atingido(session):
            raise Adiar(f"limite diário de {limite_diario()} envios atingido; fica na fila para amanhã")
    if step in ("prepare", "submit"):
        motivo_portal = None
        if op.portal != "comprasgov":
            motivo_portal = f"portal '{op.portal}' não suportado na v1"
        else:
            from licitabot.sicaf.checklist import onboarding_completo

            ok, faltando = onboarding_completo()
            if not ok:
                motivo_portal = "onboarding incompleto: " + ", ".join(faltando)
        if motivo_portal and step == "prepare":
            # Não dá para preparar no portal, mas a licitação já está analisada/precificada/documentada:
            # envia o e-mail informativo (conta no limite diário) e bloqueia até o humano resolver.
            from licitabot.pipeline.notify import send_info_email

            try:
                send_info_email(op.id, motivo_portal)
                return f"e-mail informativo enviado; {motivo_portal} (documentos em {op.pasta})"
            except Exception as e:  # noqa: BLE001
                log.warning("e-mail informativo falhou para %s: %s", op.id, e)
                return f"{motivo_portal} (documentos em {op.pasta}; e-mail falhou: {str(e)[:120]})"
        if motivo_portal:
            return motivo_portal
        margem = s.prazo_min_preparar_min if step == "prepare" else s.prazo_min_enviar_min
        if op.data_encerramento_proposta and op.data_encerramento_proposta < agora + timedelta(minutes=margem):
            return f"menos de {margem} min até o encerramento"
    return None


def run_step(ref: int | str, step: str, force: bool = False) -> bool:
    """Executa uma etapa. Retorna True se avançou."""
    with db_session() as session:
        op = get_oportunidade(session, ref)
        if not op:
            raise ValueError(f"Oportunidade {ref} não encontrada")
        entrada, _ = STEPS[step]
        if not force and op.status != entrada:
            log.warning("%s: etapa %s exige status %s (atual %s)", op.id, step, entrada, op.status)
            return False
        try:
            motivo = _gate(session, op, step)
        except Adiar as a:
            log.info("%s adiada: %s", op.id, a)
            return False
        if motivo:
            set_status(session, op, Status.BLOQUEADA, motivo)
            session.commit()
            log.info("%s bloqueada: %s", op.id, motivo)
            return False
        oid = op.id
    try:
        _fn(step)(oid)
    except Exception as e:  # noqa: BLE001
        log.exception("Falha na etapa %s da oportunidade %s", step, oid)
        with db_session() as session:
            op = session.get(Oportunidade, oid)
            op.tentativas += 1
            if op.status not in (Status.DESCARTADA, Status.BLOQUEADA):
                set_status(session, op, Status.ERRO, f"{step}: {str(e)[:500]}", erro=str(e)[:2000])
            session.commit()
        return False
    with db_session() as session:
        op = session.get(Oportunidade, oid)
        return op.status == STEPS[step][1]


def run_until(ref: int | str, ate: str = "notify") -> None:
    """Executa etapas consecutivas a partir do status atual até `ate` (inclusive)."""
    limite = ORDEM_ETAPAS.index(ate)
    for _ in range(len(ORDEM_ETAPAS)):
        with db_session() as session:
            op = get_oportunidade(session, ref)
            step = next_step_for(Status(op.status))
        if not step or ORDEM_ETAPAS.index(step) > limite:
            break
        if not run_step(ref, step):
            break


def retomar_erro(ref: int | str) -> None:
    """Volta uma oportunidade em ERRO/BLOQUEADA para o último status 'bom' (inferido dos eventos)."""
    from licitabot.db.models import Evento

    with db_session() as session:
        op = get_oportunidade(session, ref)
        evs = session.exec(select(Evento).where(Evento.oportunidade_id == op.id, Evento.tipo == "status").order_by(Evento.id.desc())).all()
        for e in evs:
            de = e.dados_json.get("de")
            if de and de not in (Status.ERRO, Status.BLOQUEADA):
                set_status(session, op, Status(de), "retomada manual")
                session.commit()
                return


def process_pending(limite: int = 20, ate: str = "notify") -> dict[str, int]:
    """Varre oportunidades em status intermediário e avança cada uma até `ate`, priorizando o prazo mais próximo.

    Respeita o limite diário: quando os envios do dia atingem LIMITE_DIARIO_ENVIOS, só a ingestão/triagem (baratas)
    continuam; análise, documentos e e-mails ficam para o dia seguinte (fila retroativa ordenada por prazo).
    """
    from licitabot.pipeline.notify import envios_hoje

    stats = {"processadas": 0, "avancaram": 0, "envios_hoje": 0}
    limite_idx = ORDEM_ETAPAS.index(ate)
    ativos = [entrada for name, (entrada, _) in STEPS.items() if ORDEM_ETAPAS.index(name) <= limite_idx]
    with db_session() as session:
        enviados = envios_hoje(session)
        stats["envios_hoje"] = enviados
        restante_dia = max(0, limite_diario() - enviados)
        if restante_dia == 0:
            # só etapas baratas (ingestão e triagem) para manter a fila pronta para amanhã
            ativos = [st for st in ativos if st in (Status.DESCOBERTA, Status.BAIXADA)]
        from licitabot.pipeline.pretriagem import ativa as pre_triagem_ativa

        q = (
            select(Oportunidade.id)
            .where(Oportunidade.status.in_([str(s) for s in ativos]))
            .where(Oportunidade.tentativas < MAX_TENTATIVAS)
        )
        if pre_triagem_ativa():
            # o que está em DESCOBERTA só entra na ingestão depois de a IA olhar o objeto
            q = q.where((Oportunidade.status != Status.DESCOBERTA) | (Oportunidade.pre_triagem != ""))
        q = (
            q
            .order_by(Oportunidade.data_encerramento_proposta)
            .limit(limite)
        )
        ids = list(session.exec(q).all())
    for oid in ids:
        stats["processadas"] += 1
        with db_session() as session:
            antes = session.get(Oportunidade, oid).status
        run_until(oid, ate)
        with db_session() as session:
            depois = session.get(Oportunidade, oid).status
        if depois != antes:
            stats["avancaram"] += 1
    return stats


def retomar_bloqueadas() -> int:
    """Reavalia oportunidades BLOQUEADAS por onboarding/documentos (quando o humano resolveu)."""
    n = 0
    with db_session() as session:
        ops = session.exec(select(Oportunidade).where(Oportunidade.status == Status.BLOQUEADA)).all()
        for op in ops:
            if op.data_encerramento_proposta and op.data_encerramento_proposta < datetime.now():
                set_status(session, op, Status.EXPIRADA, "prazo encerrado enquanto bloqueada")
                continue
            if "onboarding" in op.status_motivo or "documentos" in op.status_motivo.lower() or "cofre" in op.status_motivo.lower():
                from licitabot.db.models import Evento

                evs = session.exec(select(Evento).where(Evento.oportunidade_id == op.id, Evento.tipo == "status").order_by(Evento.id.desc())).all()
                for e in evs:
                    de = e.dados_json.get("de")
                    if de and de not in (Status.ERRO, Status.BLOQUEADA):
                        set_status(session, op, Status(de), "reavaliação automática de bloqueio")
                        n += 1
                        break
        session.commit()
    return n
