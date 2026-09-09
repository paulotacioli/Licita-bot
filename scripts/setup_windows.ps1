# Setup do Licita-Bot no Windows. Execute no PowerShell (como usuário normal) a partir da pasta do projeto:
#   powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

function Have($cmd) { return $null -ne (Get-Command $cmd -ErrorAction SilentlyContinue) }

# 1) Python 3.12 (python.org via winget)
$py = Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe", "C:\Program Files\Python312\python.exe" -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty FullName
if (-not $py) {
    Write-Host "Instalando Python 3.12..."
    winget install --id Python.Python.3.12 --exact --silent --accept-package-agreements --accept-source-agreements
    $py = Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe", "C:\Program Files\Python312\python.exe" -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty FullName
}
Write-Host "Python: $py"

# 2) venv + dependências
if (-not (Test-Path ".venv")) { & $py -m venv .venv }
.\.venv\Scripts\python.exe -m pip install --upgrade pip --quiet
.\.venv\Scripts\python.exe -m pip install -e ".[dev,word]" --quiet
.\.venv\Scripts\python.exe -m playwright install chromium

# 3) LibreOffice (opcional: só se não houver Word)
$word = Test-Path "HKLM:\SOFTWARE\Microsoft\Office\ClickToRun"
$lo = Test-Path "C:\Program Files\LibreOffice\program\soffice.exe"
if (-not $word -and -not $lo) {
    Write-Host "Nem Word nem LibreOffice encontrados: instalando LibreOffice para converter docx->pdf..."
    winget install --id TheDocumentFoundation.LibreOffice --silent --accept-package-agreements --accept-source-agreements
}

# 4) cloudflared (túnel para o link de aprovação por e-mail). Opcional: sem ele, a aprovação é por resposta de e-mail.
if (-not (Have "cloudflared")) {
    Write-Host "Instalando cloudflared (opcional)..."
    try { winget install --id Cloudflare.cloudflared --silent --accept-package-agreements --accept-source-agreements } catch { Write-Warning "cloudflared não instalado: $_" }
}

# 5) Política do Chrome: seleciona automaticamente o certificado e-CNPJ para *.gov.br (evita o diálogo de escolha)
$pol = "HKCU:\Software\Policies\Google\Chrome\AutoSelectCertificateForUrls"
New-Item -Path $pol -Force | Out-Null
New-ItemProperty -Path $pol -Name "1" -Value '{"pattern":"https://[*.]gov.br","filter":{}}' -PropertyType String -Force | Out-Null

# 6) Arquivos iniciais
if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env"; Write-Host "Criado .env — preencha as chaves." }
.\.venv\Scripts\licitabot.exe init-db
.\.venv\Scripts\python.exe -m licitabot.docs.build_templates | Out-Null

Write-Host ""
Write-Host "Pronto. Próximos passos:"
Write-Host "  1. Edite .env (ANTHROPIC_API_KEY, SMTP_*, OWNER_EMAIL, APPROVAL_SECRET)."
Write-Host "  2. Edite config/empresa.yaml (ou rode: .\.venv\Scripts\licitabot.exe onboarding cnpj <CNPJ>)."
Write-Host "  3. Rode: .\.venv\Scripts\licitabot.exe onboarding checklist"
Write-Host "  4. Rode: .\.venv\Scripts\licitabot.exe discover ; process"
