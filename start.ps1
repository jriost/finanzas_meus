$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$host_ = if ($env:FINANZAS_HOST) { $env:FINANZAS_HOST } else { "127.0.0.1" }

if (-not (Test-Path "$root\.env")) {
  Copy-Item "$root\.env.example" "$root\.env"
  Write-Host "Cree un .env desde la plantilla: ponle tu usuario y contrasena antes de entrar." -ForegroundColor Yellow
}

Set-Location "$root\web"
if (-not (Test-Path node_modules)) { npm install --no-audit --no-fund }
npm run build

Set-Location "$root\backend"
Write-Host "Vida Nueva en http://$host_`:8000" -ForegroundColor Green
python -m uvicorn app:app --host $host_ --port 8000
