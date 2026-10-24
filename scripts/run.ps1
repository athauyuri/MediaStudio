$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot
try {
    & (Join-Path $projectRoot '.venv\Scripts\python.exe') -m app @args
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
