<#
.SYNOPSIS
    Project verification entry point.

.DESCRIPTION
    Runs verification checks for the project.

    This script is intentionally stack-agnostic in the starter.
    Real projects should customize the "Project verification" section
    for their specific tools and commands.

    The starter-level checks (git state, no tracked secrets) always run.
    Project-level checks are delegated to project-defined commands.

.NOTES
    How to customize for a real project:

    Node/pnpm:
        Run-Check "TypeScript typecheck" { pnpm run typecheck }
        Run-Check "Lint"                 { pnpm run lint }
        Run-Check "Tests"                { pnpm run test }

    Python/uv:
        Run-Check "Python tests" { uv run pytest }
        Run-Check "Python lint"  { uv run ruff check . }
        Run-Check "Type check"   { uv run mypy . }
#>

Write-Host ""
Write-Host "Project Verification" -ForegroundColor Cyan
Write-Host "====================" -ForegroundColor Cyan
Write-Host ""

$passed  = 0
$failed  = 0
$skipped = 0

# ---- Helper -----------------------------------------------------------------

function Run-Check {
    param(
        [string]$Label,
        [scriptblock]$Block,
        [bool]$Skip = $false
    )

    if ($Skip) {
        Write-Host ("  --  {0}  [skipped]" -f $Label) -ForegroundColor DarkGray
        $script:skipped++
        return
    }

    Write-Host ("  ..  {0}" -f $Label) -ForegroundColor DarkGray
    try {
        $null = & $Block 2>&1
        if ($LASTEXITCODE -eq 0 -or $LASTEXITCODE -eq $null) {
            Write-Host ("  OK  {0}" -f $Label) -ForegroundColor Green
            $script:passed++
        } else {
            Write-Host ("  !!  {0}  [exit code $LASTEXITCODE]" -f $Label) -ForegroundColor Red
            $script:failed++
        }
    } catch {
        Write-Host ("  !!  {0}  [error: {1}]" -f $Label, $_) -ForegroundColor Red
        $script:failed++
    }
}

# ---- Git state --------------------------------------------------------------

Write-Host "Repository state:" -ForegroundColor White

Run-Check "Git available" {
    git --version | Out-Null
}

Run-Check "No obviously tracked secrets" {
    $patterns = @("*.pem", "*.key", ".env", "*.secret")
    $found    = @()
    foreach ($p in $patterns) {
        $matches = git ls-files $p 2>&1
        if ($matches -and $LASTEXITCODE -eq 0) { $found += $matches }
    }
    if ($found.Count -gt 0) {
        Write-Host "  Possible secrets tracked by git: $($found -join ', ')" -ForegroundColor Red
        exit 1
    }
}

Write-Host ""

# ---- Project verification ---------------------------------------------------
#
# Replace this section with your project's actual verification commands.
# See the .NOTES section above for examples.
#

Write-Host "Project checks:" -ForegroundColor White
$pyExe = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } else { "python" }
Run-Check "Python tests (pytest)" {
    & $pyExe -m pytest tests
}

Write-Host ""

# ---- Summary ----------------------------------------------------------------

$total = $passed + $failed + $skipped

$color = if ($failed -gt 0) { "Red" } elseif ($skipped -gt 0) { "Yellow" } else { "Green" }
Write-Host ("Results: {0} passed, {1} failed, {2} skipped  (of {3} checks)" `
    -f $passed, $failed, $skipped, $total) -ForegroundColor $color

Write-Host ""

if ($failed -gt 0) {
    exit 1
}
