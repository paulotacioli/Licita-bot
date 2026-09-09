# Expõe o endpoint de aprovação (FastAPI local) via Cloudflare Tunnel.
# Modo rápido (URL aleatória, muda a cada execução): cloudflared tunnel --url http://127.0.0.1:8765
# Copie a URL https://xxxx.trycloudflare.com impressa para PUBLIC_BASE_URL no .env e reinicie o worker.
# Modo estável (URL fixa): crie um túnel nomeado em https://one.dash.cloudflare.com (Zero Trust > Networks > Tunnels),
#   instale o conector e aponte para http://127.0.0.1:8765; use o hostname fixo em PUBLIC_BASE_URL.
$port = 8765
if (Test-Path ".env") { $m = Select-String -Path ".env" -Pattern "^WEB_PORT=(\d+)"; if ($m) { $port = [int]$m.Matches[0].Groups[1].Value } }
cloudflared tunnel --url "http://127.0.0.1:$port"
