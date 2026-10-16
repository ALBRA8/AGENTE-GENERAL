[CmdletBinding()]
param([switch]$SkipDevDependencies)
$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot
$Py = Get-Command py -ErrorAction SilentlyContinue
if ($null -eq $Py) { Write-Error "No se encontro el launcher 'py'. Instala Python 3.10 o superior."; exit 1 }
& py -3 --version
if ($LASTEXITCODE -ne 0) { Write-Error "No se encontro Python compatible mediante 'py -3'."; exit 1 }
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    & py -3 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "No se pudo crear .venv." }
}
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
& $Python -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "No se pudo actualizar pip." }
if ($SkipDevDependencies) { & $Python -m pip install -e ".[a2a,api]" } else { & $Python -m pip install -e ".[dev,a2a,api]" }
if ($LASTEXITCODE -ne 0) { throw "La instalacion de dependencias fallo." }
& $Python scripts/local_deployment_preflight.py
if ($LASTEXITCODE -ne 0) { throw "El preflight detecto bloqueos obligatorios." }
& $Python -m albra_seed.cli.cli --version
if ($LASTEXITCODE -ne 0) { throw "La comprobacion del CLI fallo." }
Write-Host "Instalacion local completada. Para iniciar la API: .\scripts\start_local_api.ps1"
Write-Host "La API se enlaza a 127.0.0.1; esto no valida un despliegue de produccion."
