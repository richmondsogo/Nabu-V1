<#
.SYNOPSIS
    Quick environment health check.

.DESCRIPTION
    Reports the state of local development tooling.

    Distinguishes between:
      - What the environment is capable of (tools available).
      - What the project requires (defined by your stack).

    This script does not check Antigravity or MCP state -- those are
    managed by the Antigravity IDE and cannot be reliably inspected
    from a shell script.
#>

Write-Host ""
Write-Host "Environment Health Check" -ForegroundColor Cyan
Write-Host "========================" -ForegroundColor Cyan
Write-Host ""

$issueList = @()

# ---- Helper -----------------------------------------------------------------

function Test-CliTool {
    param(
        [string]$Name,
        [string]$Cmd,
        [string]$VersionFlag = "--version"
    )
    try {
        $out = & $Cmd $VersionFlag 2>&1 | Select-Object -First 1
        if ($LASTEXITCODE -eq 0 -or $out) {
            $ver = ($out -replace "`r`n|`n", "").Trim()
            if ($ver.Length -gt 50) { $ver = $ver.Substring(0, 50) + "..." }
            Write-Host ("  OK  {0,-12} {1}" -f $Name, $ver) -ForegroundColor Green
            return $true
        }
    } catch {}
    Write-Host ("  !!  {0,-12} [not found]" -f $Name) -ForegroundColor Red
    return $false
}

# ---- Environment capabilities -----------------------------------------------

Write-Host "Environment capabilities:" -ForegroundColor White
$hasGit    = Test-CliTool "git"    "git"
$hasNode   = Test-CliTool "node"   "node"
$hasNpm    = Test-CliTool "npm"    "npm"
$hasPnpm   = Test-CliTool "pnpm"   "pnpm"
$hasPython = Test-CliTool "python" "python"
$hasUv     = Test-CliTool "uv"     "uv"

Write-Host ""

# ---- Git repository state ---------------------------------------------------

Write-Host "Repository:" -ForegroundColor White

if ($hasGit) {
    $branch    = git branch --show-current 2>&1
    $gitShort  = git status --short 2>&1

    if ($LASTEXITCODE -eq 0) {
        Write-Host "  OK  Git repository on branch: $branch" -ForegroundColor Green

        $dirty = ($gitShort | Where-Object { $_ -ne "" }).Count
        if ($dirty -eq 0) {
            Write-Host "  OK  Working tree clean" -ForegroundColor Green
        } else {
            Write-Host "  WW  $dirty uncommitted change(s)" -ForegroundColor Yellow
            $issueList += "Uncommitted changes in working tree."
        }
    } else {
        Write-Host "  WW  Not a git repository" -ForegroundColor Yellow
        $issueList += "Directory is not a git repository."
    }
} else {
    Write-Host "  !!  Git not available -- cannot inspect repository state." -ForegroundColor Red
    $issueList += "Git is not installed."
}

Write-Host ""

# ---- Project requirements ---------------------------------------------------
#
# Customize this section for your project.
# Add project-specific tool checks here and push to $issueList when missing.
#
# Example:
#   if (-not $hasNode) { $issueList += "Node.js is required. Install from https://nodejs.org" }
#   if (-not $hasPnpm) { $issueList += "pnpm is required. Run: npm install -g pnpm" }
#
Write-Host "Project requirements:" -ForegroundColor White
Write-Host "  --  No project-specific requirements defined yet." -ForegroundColor Yellow
Write-Host "      Customize scripts\check-env.ps1 for your stack." -ForegroundColor DarkGray

Write-Host ""

# ---- Agent environment note -------------------------------------------------

Write-Host "Agent environment:" -ForegroundColor White
Write-Host "  --  Antigravity IDE, MCPs, and skills cannot be verified from" -ForegroundColor Yellow
Write-Host "      a shell script. Verify these inside the Antigravity IDE." -ForegroundColor DarkGray
Write-Host "      See docs\agents\mcps.md and docs\agents\skills.md." -ForegroundColor DarkGray

Write-Host ""

# ---- Summary ----------------------------------------------------------------

if ($issueList.Count -eq 0) {
    Write-Host "Status: OK" -ForegroundColor Green
} else {
    Write-Host "Status: Issues found" -ForegroundColor Red
    Write-Host ""
    foreach ($issue in $issueList) {
        Write-Host "  - $issue" -ForegroundColor Red
    }
}

Write-Host ""
