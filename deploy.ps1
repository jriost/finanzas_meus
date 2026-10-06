# Compila el frontend y sube backend + dist al servidor. No toca el .env ni la
# base de datos remota: las credenciales y los datos viven alla.
#
# Necesita FINANZAS_DEPLOY_HOST y FINANZAS_DEPLOY_KEY, que salen del .env.
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

Get-Content "$root\.env" -ErrorAction SilentlyContinue | ForEach-Object {
  if ($_ -match '^\s*([A-Z_]+)\s*=\s*(.*?)\s*$') {
    [Environment]::SetEnvironmentVariable($matches[1], $matches[2].Trim('"', "'"))
  }
}
$remoto = $env:FINANZAS_DEPLOY_HOST
$llave = $env:FINANZAS_DEPLOY_KEY
if (-not $remoto -or -not $llave) {
  throw "Falta FINANZAS_DEPLOY_HOST o FINANZAS_DEPLOY_KEY en el .env (mira .env.example)."
}
$sshArgs = @("-i", $llave, "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=no")

Set-Location "$root\web"
if (-not (Test-Path node_modules)) { npm install --no-audit --no-fund }
npm run build

Set-Location $root
$paquete = Join-Path $env:TEMP "vidanueva.tgz"
tar czf $paquete backend/app.py backend/requirements.txt backend/semilla.example.json -C web dist
scp @sshArgs $paquete "${remoto}:/tmp/vidanueva.tgz"
Remove-Item $paquete

ssh @sshArgs $remoto @'
set -e
cd /opt/vidanueva
rm -rf web/dist
tar xzf /tmp/vidanueva.tgz && mv dist web/dist && rm /tmp/vidanueva.tgz
./venv/bin/pip -q install -r backend/requirements.txt
systemctl restart vidanueva
for i in $(seq 1 20); do curl -sf -o /dev/null http://127.0.0.1:8100/ && break; sleep 1; done
curl -s -o /dev/null -w "pagina -> %{http_code}\n" http://127.0.0.1:8100/
systemctl is-active vidanueva
'@

Write-Host "Desplegado en $remoto" -ForegroundColor Green
