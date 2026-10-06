$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$python = $null
if (Test-Path -LiteralPath '.runtime\python\python.exe') { $python = (Resolve-Path '.runtime\python\python.exe').Path }
elseif (Get-Command python -ErrorAction SilentlyContinue) { $python = 'python' }
elseif (Get-Command py -ErrorAction SilentlyContinue) { $python = 'py' }
if (-not $python) {
    Write-Host 'Python 3.11+ was not found. Install Python from https://www.python.org/downloads/windows/ and enable Add python.exe to PATH.'
    exit 1
}
& $python -c 'import sys; assert sys.version_info >= (3,11), "Python 3.11+ required"'
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    & $python -m venv .venv
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
& '.\.venv\Scripts\python.exe' -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
if (-not (Test-Path -LiteralPath '.env')) { Copy-Item -LiteralPath '.env.example' -Destination '.env' }
Write-Host 'Core dependencies installed. Optional transcription: .\.venv\Scripts\python.exe -m pip install -r requirements-transcription.txt'
Write-Host 'Install FFmpeg, then double-click the launcher. Enter your API key in the Settings page.'
