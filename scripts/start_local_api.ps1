[CmdletBinding()]
param([int]$Port = 8000)
$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Error "No existe .venv. Ejecuta primero: .\scripts\setup_local_windows.ps1"
    exit 1
}
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
& $Python -c "import fastapi, uvicorn, albra_seed"
if ($LASTEXITCODE -ne 0) { Write-Error "Faltan dependencias. Ejecuta .\scripts\setup_local_windows.ps1"; exit 1 }
Write-Host "Iniciando ALBRA API solo en loopback: http://127.0.0.1:$Port"
Write-Host "No queda expuesta a la red local. Ctrl+C para detener."
& $Python -m uvicorn albra_seed.local_api:app --host 127.0.0.1 --port $Port
exit $LASTEXITCODE
