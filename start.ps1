param([int]$Port = 8765, [switch]$NoBrowser, [switch]$AutoSetup)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
try {
    $runningStudio = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/environment" -TimeoutSec 2
    if ($runningStudio.service -eq 'music-studio') {
        $runningSettings = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/settings" -TimeoutSec 2
        if (-not $runningSettings.capabilities.prompt_library) {
            Write-Host 'An older Studio server is running. Close its original terminal, then run this launcher again.' -ForegroundColor Yellow
            Write-Host 'Refreshing the browser cannot reload the Python backend.'
            exit 1
        }
        Write-Host "Music Studio is already running: http://127.0.0.1:$Port"
        if (-not $NoBrowser) { Start-Process -FilePath "http://127.0.0.1:$Port" -WindowStyle Hidden }
        exit 0
    }
} catch { }
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    if ($AutoSetup) {
        & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'setup.ps1')
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
}
if (-not (Test-Path -LiteralPath $python)) {
    Write-Host 'Run .\setup.ps1 first. Python 3.11+ is required.'
    exit 1
}
& $python -c 'import fastapi, uvicorn, multipart, httpx, dotenv'
if ($LASTEXITCODE -ne 0) {
    if ($AutoSetup) {
        & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'setup.ps1')
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    } else { Write-Host 'Dependencies missing. Run .\setup.ps1'; exit 1 }
}
Write-Host "Music Studio: http://127.0.0.1:$Port"
Write-Host 'Keep this terminal running. Ctrl+C stops the local worker. Interrupted tasks can be restored next launch.'
if (-not $NoBrowser) { Start-Process -FilePath "http://127.0.0.1:$Port" -WindowStyle Hidden }
& $python -m uvicorn app.main:app --host 127.0.0.1 --port $Port
