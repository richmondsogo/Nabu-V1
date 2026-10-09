# PowerShell wrapper to run link resolver probes

Write-Host "Running Nabu-V1 Link Resolver Probes..." -ForegroundColor Cyan

$PythonExe = Join-Path $PSScriptRoot "..\.venv\Scripts\python.exe"
$ScriptPy = Join-Path $PSScriptRoot "probe-links.py"

if (-not (Test-Path $PythonExe)) {
    Write-Error "Virtual environment Python not found at: $PythonExe"
    exit 1
}

& $PythonExe $ScriptPy
exit $LASTEXITCODE
