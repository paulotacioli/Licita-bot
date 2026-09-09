"""Onboarding guiado: e-CNPJ, gov.br, SICAF, login no perfil do navegador, cofre, configs.

O sistema NÃO faz o cadastro no SICAF (exige certificado, termos e dados sensíveis). Ele diagnostica,
organiza os documentos ("kit SICAF"), verifica o que dá para verificar e mantém um checklist persistido.
"""

from __future__ import annotations

import logging
import re
import shutil
from datetime import datetime
from pathlib import Path

import httpx
import yaml
from rich import print as rprint
from rich.table import Table
from sqlmodel import select

from licitabot.config import get_settings, load_empresa, load_precos
from licitabot.db.models import OnboardingPasso
from licitabot.db.session import db_session, init_db

log = logging.getLogger(__name__)

CNAES_TI = {"6201-5/01", "6201-5/02", "6202-3/00", "6203-1/00", "6204-0/00", "6209-1/00", "6311-9/00", "6319-4/00"}

PASSOS: list[tuple[str, str, str, bool]] = [
    # chave, título, descrição, verificação automática
    (
        "empresa_yaml",
        "Preencher config/empresa.yaml",
        "Razão social, CNPJ, endereço, porte, representante legal, serviços, produtos SaaS. "
        "Use `licitabot onboarding cnpj` para pré-preencher a partir da Receita Federal.",
        True,
    ),
    (
        "precos_yaml",
        "Revisar config/precos.yaml",
        "Valor/hora por perfil, margem mínima, desconto máximo vs. estimado, teto absoluto. "
        "Estas regras decidem os preços; a IA só sugere horas.",
        True,
    ),
    (
        "claude_code",
        "Conectar a IA à sua assinatura do Claude Code",
        "No terminal, rode `claude setup-token` (abre o navegador para autorizar) e cole o token em CLAUDE_CODE_OAUTH_TOKEN no .env. "
        "Depois valide com `licitabot llm-teste`. Sem isso, triagem, análise e redação não rodam.",
        True,
    ),
    (
        "e_cnpj",
        "Emitir certificado digital e-CNPJ (ICP-Brasil), tipo A1",
        "Emitido por uma Autoridade Certificadora (Serasa, Certisign, Soluti, Valid…) com validação presencial ou por vídeo "
        "do representante legal. Prefira A1 (arquivo .pfx, ~R$ 150-250/ano): funciona com o navegador automatizado sem token. "
        "Depois de emitido, instale-o no Windows (duplo clique no .pfx → Usuário Atual → marcar 'chave exportável').",
        False,
    ),
    (
        "govbr",
        "Conta gov.br nível Ouro (ou Prata) do representante e vínculo com o CNPJ",
        "Acesse https://acesso.gov.br, eleve o nível (certificado digital ou biometria pelo app gov.br). "
        "Em 'Vincular empresas via e-CNPJ' vincule o CNPJ à sua conta.",
        False,
    ),
    (
        "cofre",
        "Depositar documentos no cofre (cofre/)",
        "Mínimo: contrato social, cartão CNPJ, documento do representante, CND federal, CRF FGTS, CNDT, "
        "CND estadual e municipal, certidão de falência, balanço patrimonial e atestados de capacidade técnica. "
        "Nomes conforme cofre/README.md. Rode `licitabot cofre scan`.",
        True,
    ),
    (
        "sicaf_nivel1",
        "SICAF Nível I — Credenciamento",
        "Em https://www.gov.br/compras → Acesso ao sistema → Fornecedor Brasileiro → login gov.br → Cadastro → Nível I. "
        "Instantâneo e gratuito se o CNPJ estiver regular na Receita.",
        False,
    ),
    (
        "sicaf_nivel2_3",
        "SICAF Níveis II (habilitação jurídica) e III (regularidade fiscal/trabalhista)",
        "Upload do contrato social e documento do representante (II); as certidões federais são consultadas automaticamente, "
        "estaduais/municipais precisam de upload (III). Use a pasta gerada por `licitabot onboarding kit-sicaf`.",
        False,
    ),
    (
        "sicaf_nivel5_6",
        "SICAF Níveis V (qualificação técnica) e VI (econômico-financeira) — recomendados",
        "Atestados de capacidade técnica (V) e balanço patrimonial com índices LG/SG/LC (VI). "
        "Sem o VI muitos editais exigem apresentação avulsa do balanço.",
        False,
    ),
    (
        "login_navegador",
        "Login no navegador automatizado (`licitabot portal login`)",
        "Abre o Chromium com perfil persistente em data/browser_profile. Faça login no gov.br com o certificado. "
        "O sistema reaproveita a sessão; quando expirar, você receberá um e-mail para repetir este passo.",
        True,
    ),
    (
        "email_config",
        "Configurar e-mail e aprovação no .env",
        "SMTP (Gmail: senha de app), OWNER_EMAIL, APPROVAL_SECRET (32+ caracteres) e PUBLIC_BASE_URL (Cloudflare Tunnel) "
        "ou deixe PUBLIC_BASE_URL vazio para aprovar respondendo o e-mail.",
        True,
    ),
    (
        "dry_run_off",
        "Desligar o DRY_RUN quando estiver pronto para envios reais",
        "No .env, DRY_RUN=false. Antes disso, faça pelo menos uma preparação completa em dry-run e confira as capturas de tela.",
        True,
    ),
]

OBRIGATORIOS_PARA_PORTAL = ["empresa_yaml", "claude_code", "e_cnpj", "govbr", "sicaf_nivel1", "sicaf_nivel2_3", "login_navegador", "email_config"]


def _seed(session) -> None:
    existentes = {p.chave for p in session.exec(select(OnboardingPasso))}
    for ordem, (chave, titulo, desc, auto) in enumerate(PASSOS, start=1):
        if chave not in existentes:
            session.add(OnboardingPasso(chave=chave, titulo=titulo, descricao=desc, ordem=ordem, verificacao_automatica=auto))
    session.commit()


def _verificar_automaticos(session) -> None:
    s = get_settings()
    emp = load_empresa()
    checks: dict[str, tuple[bool, str]] = {}
    ok_emp = bool(emp.razao_social and re.sub(r"\D", "", emp.cnpj) and emp.representante.nome and emp.cidade)
    checks["empresa_yaml"] = (ok_emp, "" if ok_emp else "faltam razão social/CNPJ/representante/cidade")
    p = load_precos()
    checks["precos_yaml"] = (p.valor_hora_padrao > 0 and 0 < p.margem_minima < 1, "")
    from licitabot.cofre.index import scan_cofre

    docs = scan_cofre()
    tipos = {d.tipo for d in docs}
    minimo = {"contrato_social", "cnd_federal", "crf_fgts", "cndt"}
    falt = sorted(minimo - tipos)
    checks["cofre"] = (not falt, "faltam: " + ", ".join(falt) if falt else "")
    from licitabot.portal.comprasgov.session import sessao_valida_cache

    ok_login, msg_login = sessao_valida_cache()
    checks["login_navegador"] = (ok_login, msg_login)
    if s.llm_backend.lower() == "api":
        checks["claude_code"] = (bool(s.anthropic_api_key), "" if s.anthropic_api_key else "ANTHROPIC_API_KEY ausente")
    else:
        from licitabot.llm.claude_code import cli_verificado

        checks["claude_code"] = cli_verificado()
    ok_mail = bool(s.smtp_user and s.smtp_password and s.owner_email and len(s.approval_secret) >= 32)
    checks["email_config"] = (ok_mail, "" if ok_mail else "SMTP/OWNER_EMAIL/APPROVAL_SECRET incompletos")
    checks["dry_run_off"] = (not s.dry_run, "DRY_RUN=true (modo seguro)" if s.dry_run else "")
    for chave, (ok, obs) in checks.items():
        passo = session.exec(select(OnboardingPasso).where(OnboardingPasso.chave == chave)).first()
        if passo:
            if ok and not passo.concluido:
                passo.concluido_em = datetime.now()
            passo.concluido = ok
            passo.observacao = obs
            session.add(passo)
    session.commit()


def onboarding_completo() -> tuple[bool, list[str]]:
    """Gate do pipeline para etapas de portal: tudo em OBRIGATORIOS_PARA_PORTAL concluído."""
    init_db()
    with db_session() as session:
        _seed(session)
        try:
            _verificar_automaticos(session)
        except Exception as e:  # noqa: BLE001
            log.warning("verificação automática do onboarding falhou: %s", e)
        passos = {p.chave: p for p in session.exec(select(OnboardingPasso))}
        faltando = [passos[k].titulo for k in OBRIGATORIOS_PARA_PORTAL if k in passos and not passos[k].concluido]
        return (not faltando, faltando)


def resumo_onboarding() -> str:
    ok, faltando = onboarding_completo()
    if ok:
        return "[green]Onboarding completo: etapas de portal liberadas.[/green]"
    return "[yellow]Onboarding incompleto (portal bloqueado):[/yellow]\n  - " + "\n  - ".join(faltando)


def imprimir_checklist() -> None:
    onboarding_completo()
    with db_session() as session:
        passos = session.exec(select(OnboardingPasso).order_by(OnboardingPasso.ordem)).all()
        t = Table(title="Checklist de onboarding")
        t.add_column("#")
        t.add_column("Chave")
        t.add_column("Passo", overflow="fold")
        t.add_column("Status")
        t.add_column("Obs.", overflow="fold")
        for p in passos:
            st = "[green]✔ feito[/green]" if p.concluido else ("[yellow]auto[/yellow]" if p.verificacao_automatica else "[red]pendente[/red]")
            t.add_row(str(p.ordem), p.chave, f"[bold]{p.titulo}[/bold]\n{p.descricao}", st, p.observacao)
        rprint(t)
        rprint("Marque passos manuais com: licitabot onboarding concluir <chave>")


def concluir_passo(chave: str) -> None:
    with db_session() as session:
        _seed(session)
        p = session.exec(select(OnboardingPasso).where(OnboardingPasso.chave == chave)).first()
        if not p:
            rprint(f"[red]passo '{chave}' não existe[/red]")
            return
        p.concluido = True
        p.concluido_em = datetime.now()
        session.add(p)
        session.commit()
        rprint(f"[green]{p.titulo}: concluído[/green]")


def consultar_cnpj(cnpj: str | None = None) -> dict | None:
    emp = load_empresa()
    cnpj = re.sub(r"\D", "", cnpj or emp.cnpj)
    if len(cnpj) != 14:
        rprint("[red]CNPJ inválido[/red]")
        return None
    try:
        r = httpx.get(f"https://brasilapi.com.br/api/cnpj/v1/{cnpj}", timeout=60)
        r.raise_for_status()
        d = r.json()
    except Exception as e:  # noqa: BLE001
        rprint(f"[red]Consulta falhou: {e}[/red]")
        return None
    cnaes = [f"{str(d.get('cnae_fiscal', ''))[:4]}-{str(d.get('cnae_fiscal', ''))[4]}/{str(d.get('cnae_fiscal', ''))[5:]}"] if d.get("cnae_fiscal") else []
    for c in d.get("cnaes_secundarios") or []:
        code = str(c.get("codigo", ""))
        if len(code) == 7:
            cnaes.append(f"{code[:4]}-{code[4]}/{code[5:]}")
    sugestao = {
        "razao_social": d.get("razao_social", ""),
        "nome_fantasia": d.get("nome_fantasia", ""),
        "cnpj": f"{cnpj[:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:]}",
        "porte": {"MICRO EMPRESA": "ME", "EMPRESA DE PEQUENO PORTE": "EPP"}.get((d.get("porte") or "").upper(), "DEMAIS"),
        "optante_simples": bool(d.get("opcao_pelo_simples")),
        "endereco": f"{d.get('descricao_tipo_de_logradouro', '')} {d.get('logradouro', '')}, {d.get('numero', '')} {d.get('complemento', '') or ''} - {d.get('bairro', '')}".strip(),
        "cidade": d.get("municipio", ""),
        "uf": d.get("uf", ""),
        "cep": str(d.get("cep", "")),
        "telefone": d.get("ddd_telefone_1", ""),
        "email": d.get("email", "") or "",
        "cnaes": cnaes,
        "situacao_cadastral": d.get("descricao_situacao_cadastral", ""),
        "qsa": [q.get("nome_socio") for q in d.get("qsa") or []],
    }
    rprint(yaml.safe_dump(sugestao, allow_unicode=True, sort_keys=False))
    faltam_ti = sorted(CNAES_TI - set(cnaes))
    if not (set(cnaes) & CNAES_TI):
        rprint("[yellow]Atenção: nenhum CNAE de TI (62xx) no cadastro. Editais podem exigir objeto social compatível. Sugestão: 6201-5/01, 6202-3/00, 6204-0/00.[/yellow]")
    else:
        rprint(f"[green]CNAEs de TI presentes.[/green] Outros CNAEs de TI possíveis: {', '.join(faltam_ti)}")
    if sugestao["situacao_cadastral"].upper() != "ATIVA":
        rprint(f"[red]Situação cadastral: {sugestao['situacao_cadastral']}[/red]")
    rprint("Copie os campos desejados para config/empresa.yaml.")
    return sugestao


def gerar_kit_sicaf() -> Path:
    """Copia documentos do cofre para data/kit_sicaf/<nível>/ e escreve um guia personalizado."""
    from licitabot.cofre.index import scan_cofre

    s = get_settings()
    emp = load_empresa()
    docs = scan_cofre()
    dest = s.data_path / "kit_sicaf"
    if dest.exists():
        shutil.rmtree(dest)
    niveis = {
        "nivel_I_credenciamento": ["cartao_cnpj", "documento_representante"],
        "nivel_II_habilitacao_juridica": ["contrato_social", "documento_representante", "procuracao"],
        "nivel_III_regularidade_fiscal": ["cnd_federal", "crf_fgts", "cndt", "cnd_estadual", "cnd_municipal", "simples_nacional"],
        "nivel_V_qualificacao_tecnica": ["atestado_capacidade_tecnica", "registro_conselho", "certificacao"],
        "nivel_VI_economico_financeira": ["balanco_patrimonial", "indices_financeiros", "certidao_falencia"],
    }
    linhas = [f"# Kit SICAF — {emp.razao_social or 'empresa'} ({emp.cnpj})", "", f"Gerado em {datetime.now():%d/%m/%Y %H:%M}.", ""]
    for nivel, tipos in niveis.items():
        pasta = dest / nivel
        pasta.mkdir(parents=True, exist_ok=True)
        linhas.append(f"## {nivel.replace('_', ' ')}")
        for t in tipos:
            encontrados = [d for d in docs if d.tipo == t]
            if encontrados:
                for d in encontrados:
                    shutil.copy2(d.caminho, pasta / Path(d.caminho).name)
                    venc = f" (válido até {d.valido_ate:%d/%m/%Y})" if d.valido_ate else ""
                    linhas.append(f"- [x] {t}: {Path(d.caminho).name}{venc}")
            else:
                linhas.append(f"- [ ] {t}: FALTANDO no cofre")
        linhas.append("")
    linhas += [
        "## Passo a passo",
        "1. https://www.gov.br/compras → 'Acesso ao sistema' → Fornecedor Brasileiro → Entrar com gov.br (certificado).",
        "2. Menu Cadastro → Nível I: confirme os dados da Receita; o credenciamento é imediato.",
        "3. Nível II: anexe contrato social e documento do representante; informe os sócios/dirigentes.",
        "4. Nível III: as certidões federais (RFB/PGFN, FGTS, CNDT) são consultadas automaticamente; anexe estadual/municipal.",
        "5. Níveis V e VI: anexe atestados e balanço; informe os índices LG/SG/LC.",
        "6. Ao final, emita a 'Declaração SICAF' e salve no cofre como certidoes/sicaf_<data>.pdf.",
        "7. Marque no checklist: `licitabot onboarding concluir sicaf_nivel1` (e os demais).",
    ]
    (dest / "GUIA_SICAF.md").write_text("\n".join(linhas), encoding="utf-8")
    rprint(f"[green]Kit SICAF gerado em {dest}[/green]")
    return dest
