# Licita-Bot

Automação de licitações públicas de **software** no Brasil: descobre editais no PNCP, analisa o edital com IA,
precifica por regras suas, gera todos os documentos (proposta, declarações, habilitação), prepara a proposta no
**Compras.gov.br**, envia um e-mail com botões **Aprovar/Rejeitar** e, se aprovado, submete automaticamente.

```
PNCP → descoberta → ingestão (PDFs) → triagem (IA) → análise do edital (IA) → precificação (regras)
     → documentos (docx→pdf) → preparação no portal (navegador) → e-mail de aprovação → envio → monitoramento
```

## Instalação (Windows)

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1
```

Instala Python 3.12, dependências, Chromium do Playwright, LibreOffice (se não houver Word), cloudflared (opcional),
cria `.env` e o banco. Depois:

1. **`.env`**:
   - **IA pela assinatura do Claude Code** (padrão, sem API): rode `claude setup-token` no terminal (abre o navegador para
     autorizar, gera um token válido por ~1 ano) e cole em `CLAUDE_CODE_OAUTH_TOKEN`. Valide com `licitabot llm-teste`.
     Alternativa: `LLM_BACKEND=api` + `ANTHROPIC_API_KEY`.
   - **E-mail**: `OWNER_EMAIL` (quem recebe), `SMTP_USER` (Gmail remetente) e `SMTP_PASSWORD` = **senha de app** do Google:
     com a verificação em 2 etapas ativa, gere em https://myaccount.google.com/apppasswords uma senha de 16 letras específica
     para programas (a senha normal da conta não funciona em SMTP). Teste com `licitabot email-teste`.
   - `APPROVAL_SECRET` (32+ caracteres aleatórios) e `PUBLIC_BASE_URL` (URL do Cloudflare Tunnel; vazio = aprovação por resposta de e-mail).
2. **`config/empresa.yaml`**: dados da empresa. Atalho: `licitabot onboarding cnpj 00.000.000/0001-00`.
3. **`config/precos.yaml`**: valor/hora, margem, desconto máximo, teto. A IA só sugere horas; estas regras mandam.
4. **`cofre/`**: documentos reais (contrato social, certidões, atestados, balanço). Ver `cofre/README.md`.
5. **Onboarding**: `licitabot onboarding checklist` (e-CNPJ, gov.br, SICAF, login no navegador).

Todos os comandos abaixo pressupõem `.\.venv\Scripts\licitabot.exe` (ou ative o venv).

## Uso

| Comando | O que faz |
|---|---|
| `licitabot discover` | Busca editais abertos no PNCP (termos em `config/triagem.yaml`) e aplica filtros locais |
| `licitabot process` | Avança todas as oportunidades pendentes até o e-mail de aprovação |
| `licitabot run <id> --step analysis` | Executa uma etapa específica (`ingest`, `triage`, `analysis`, `pricing`, `docgen`, `prepare`, `notify`, `submit`, `monitor`) |
| `licitabot list --status ANALISADA` / `licitabot show <id>` | Consulta |
| `licitabot approve <id>` | Aprova pela CLI (equivale ao botão do e-mail) |
| `licitabot portal login` | Abre o navegador para você logar no gov.br (sessão fica salva) |
| `licitabot portal smoke` | Valida sessão e seletores do Compras.gov.br |
| `licitabot cofre scan` | Indexa o cofre e mostra validade das certidões |
| `licitabot onboarding kit-sicaf` | Organiza os documentos do cofre por nível do SICAF + guia |
| `licitabot worker` | Scheduler (descoberta 2/2h, pipeline 15 min, aprovações/envio 5 min, monitor, certidões) |
| `licitabot web` | Dashboard em http://127.0.0.1:8765 + endpoints `/aprovar/{token}` e `/rejeitar/{token}` |

Operação contínua: `scripts\install_task_scheduler.ps1` registra worker e web no logon; `scripts\start_tunnel.ps1` expõe o endpoint de aprovação.

## Cadência diária

- O worker busca novos editais no PNCP a cada 2 horas e processa a fila a cada 15 minutos, sempre pelo **prazo mais próximo** (as já abertas entram retroativamente).
- Cada licitação compatível gera **um e-mail**: de aprovação (com botões) quando o portal está disponível, ou **informativo** (análise, preços e documentos prontos) enquanto o cadastro SICAF/gov.br não estiver completo.
- Limite de **`LIMITE_DIARIO_ENVIOS`** (padrão 50) licitações por dia; o excedente fica na fila para o dia seguinte. Ingestão e triagem continuam rodando para a fila estar pronta.
- Às `HORA_RESUMO_DIARIO` (padrão 8h) sai o **resumo diário**: novas compatíveis nas últimas 24h, enviadas ontem, fila, erros e pendências de onboarding. Para enviar agora: `licitabot resumo-diario`. Teste de SMTP: `licitabot email-teste`.

## Fluxo de aprovação

O e-mail traz órgão, objeto, prazo, itens (proposto × estimado), alertas da análise, checklist de documentos e
capturas de tela do portal. Os botões abrem uma página de confirmação (GET) e só o clique final (POST) decide.
O token é assinado, expira (48h ou 2h antes do prazo) e é de uso único; se preços ou documentos mudarem depois,
a aprovação é invalidada e um novo e-mail é enviado. Sem `PUBLIC_BASE_URL`, responda o e-mail com `APROVAR` ou `REJEITAR`.

## Salvaguardas

- `DRY_RUN=true` (padrão): tudo roda, inclusive preenchimento no portal, mas o clique de envio nunca acontece.
- Preços: piso = estimado × (1 − desconto máximo), teto = estimado, teto absoluto, itens ME/EPP só se o porte permitir.
- Prazos em horário de Brasília: não prepara com < 24 h nem envia com < 30 min para o encerramento.
- Antes de enviar: revalida prazo, hashes dos documentos, validade das certidões e compara os valores exibidos na tela com os aprovados.
- Sem aprovação não há envio. Só `portal/comprasgov/envio.py` conhece o botão de envio.
- Trilha de auditoria: screenshots antes/depois de cada passo, trace do Playwright, tabela `evento`.

## O que continua sendo humano

Emitir o e-CNPJ, criar/elevar a conta gov.br, cadastrar no SICAF, logar no navegador quando a sessão expirar,
depositar documentos reais no cofre, assinar PDFs quando o edital exigir assinatura ICP, aprovar cada proposta,
e **dar lances na sessão pública** (a v1 só monitora e avisa).

## Estrutura

- `licitabot/pncp` cliente PNCP · `pipeline/` etapas · `llm/` Claude (saída estruturada) · `docs/` templates e declarações
- `portal/comprasgov/` Playwright (seletores em `config/selectors/comprasgov.yaml`) · `approval/` tokens · `web/` FastAPI
- `sicaf/` onboarding · `cofre/` índice de documentos · `scheduler.py` jobs · `cli.py`
- `data/oportunidades/<id>/` edital, `analise.json`, `proposta/` (docx+pdf+checklist), `screenshots/`, `comprovante/`

## Testes

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```
