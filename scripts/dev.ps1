# Start Magic Hour on localhost, from PowerShell.
#
#   .\scripts\dev.ps1
#   .\scripts\dev.ps1 -Port 8090
#
# Creates the venv and installs requirements on first run, then reloads on save.
param([int]$Port = 8080)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Backend = Join-Path $Root "backend"
$Project = "nyu-ai-builder26nyc-9338"

Set-Location $Backend

$Py = Join-Path $Backend ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) {
  Write-Host "· creating .venv"
  python -m venv .venv
}

Write-Host "· installing requirements"
& $Py -m pip install -q --upgrade pip
& $Py -m pip install -q -r requirements.txt

# The single most expensive gotcha on this project. Without a quota project, ADC
# bills against a starvation-tier bucket and every Vertex call returns 429
# RESOURCE_EXHAUSTED, which looks exactly like a broken app.
if (Get-Command gcloud -ErrorAction SilentlyContinue) {
  $current = (gcloud config get-value project 2>$null)
  if ($current -ne $Project) {
    Write-Host "· pointing gcloud at $Project"
    gcloud config set project $Project | Out-Null
  }
  $adc = Join-Path $env:APPDATA "gcloud\application_default_credentials.json"
  if (-not (Test-Path $adc)) {
    Write-Host ""
    Write-Host "  No application default credentials found. Model calls will fail."
    Write-Host "  Run these once, then restart:"
    Write-Host "      gcloud auth application-default login"
    Write-Host "      gcloud auth application-default set-quota-project $Project"
    Write-Host ""
  }
}

Write-Host ""
Write-Host "  Magic Hour  ·  http://127.0.0.1:$Port"
Write-Host "  API docs    ·  http://127.0.0.1:$Port/docs"
Write-Host "  Health      ·  http://127.0.0.1:$Port/healthz"
Write-Host ""
& $Py -m uvicorn app.main:app --host 127.0.0.1 --port $Port --reload
