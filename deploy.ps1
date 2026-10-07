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
# --format=gnu: el tar de Windows mete cabeceras que el del servidor no entiende
# y avisa por stderr, lo que PowerShell toma como si el despliegue hubiera fallado.
tar czf $paquete --format=gnutar backend/app.py backend/requirements.txt backend/semilla.example.json -C web dist
scp @sshArgs $paquete "${remoto}:/tmp/vidanueva.tgz"
Remove-Item $paquete

# Dos cosas que PowerShell le hace a este texto al mandarlo por ssh: los saltos
# de linea salen como \r y bash falla en la primera linea, y las comillas dobles
# se pierden por el camino. Por eso se convierte a LF y el script no usa ninguna.
$enElServidor = @'
set -e
cd /opt/vidanueva
cp finanzas.db finanzas.db.bak 2>/dev/null || true
rm -rf web/dist
tar xzf /tmp/vidanueva.tgz && mv dist web/dist && rm /tmp/vidanueva.tgz
./venv/bin/pip -q install -r backend/requirements.txt
./venv/bin/python backend/app.py --test
systemctl restart vidanueva
for i in $(seq 1 25); do curl -sf -o /dev/null http://127.0.0.1:8100/ && break; sleep 1; done
curl -sf -o /dev/null http://127.0.0.1:8100/ && echo pagina-OK
systemctl is-active vidanueva
'@ -replace "`r`n", "`n"

ssh @sshArgs $remoto $enElServidor
if ($LASTEXITCODE -ne 0) { throw "El despliegue fallo en el servidor." }

Write-Host "Desplegado en $remoto" -ForegroundColor Green
