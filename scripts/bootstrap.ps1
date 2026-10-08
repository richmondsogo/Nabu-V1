<#
.SYNOPSIS
    Inspect a newly cloned environment and report setup status.

.DESCRIPTION
    Checks for common development tools and reports which are available,
    missing, or optional. Does not install anything automatically.

    Run this after cloning the repository for the first time.
    Customize the "Project requirements" section for your specific stack.
#>

Write-Host ""
Write-Host "Engineering Starter -- Environment Bootstrap" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

# ---- Helper -----------------------------------------------------------------

function Check-Tool {
    param (
        [string]$Name,
        [string]$Command,
        [string]$VersionArg = "--version",
        [string]$Status = "required",
        [string]$InstallHint = ""
    )

    $found   = $false
    $version = ""

    try {
        $raw = & $Command $VersionArg 2>&1
        if ($LASTEXITCODE -eq 0 -or $raw) {
            $found   = $true
            $version = ($raw | Select-Object -First 1) -replace "`r`n|`n", ""
            if ($version.Length -gt 60) { $version = $version.Substring(0, 60) + "..." }
        }
    } catch {
        $found = $false
    }

    if ($found) {
        Write-Host ("  OK  {0,-14} {1}" -f $Name, $version) -ForegroundColor Green
    } elseif ($Status -eq "optional") {
        Write-Host ("  --  {0,-14} [optional - not found]" -f $Name) -ForegroundColor Yellow
        if ($InstallHint) {
            Write-Host ("          {0}" -f $InstallHint) -ForegroundColor DarkGray
        }
    } else {
        Write-Host ("  !!  {0,-14} [not found]" -f $Name) -ForegroundColor Red
        if ($InstallHint) {
            Write-Host ("          {0}" -f $InstallHint) -ForegroundColor DarkGray
        }
    }
}

# ---- Core tools -------------------------------------------------------------

Write-Host "Core tools:" -ForegroundColor White
Check-Tool -Name "Git" -Command "git" -VersionArg "--version"

Write-Host ""

# ---- JavaScript / Node ------------------------------------------------------

Write-Host "JavaScript / Node:" -ForegroundColor White
Check-Tool -Name "Node.js" -Command "node" -VersionArg "--version" `
           -Status "optional" -InstallHint "https://nodejs.org"
Check-Tool -Name "npm"     -Command "npm"  -VersionArg "--version" -Status "optional"
Check-Tool -Name "pnpm"    -Command "pnpm" -VersionArg "--version" `
           -Status "optional" -InstallHint "npm install -g pnpm"

Write-Host ""

# ---- Python -----------------------------------------------------------------

Write-Host "Python:" -ForegroundColor White
Check-Tool -Name "Python" -Command "python" -VersionArg "--version" `
           -Status "optional" -InstallHint "https://python.org"
Check-Tool -Name "uv"     -Command "uv"     -VersionArg "--version" `
           -Status "optional" -InstallHint "https://docs.astral.sh/uv"
Check-Tool -Name "pip"    -Command "pip"    -VersionArg "--version" -Status "optional"

Write-Host ""

# ---- Project-specific requirements ------------------------------------------
#
# Customize this section for your project.
# Add or remove tools based on your actual stack.
# Delete this section if the checks above are sufficient.
#
# Example:
#   Check-Tool -Name "Docker" -Command "docker" -VersionArg "--version" `
#              -Status "required" -InstallHint "https://docs.docker.com/get-docker/"
#
Write-Host "Project-specific requirements:" -ForegroundColor White
Write-Host "  Customize scripts\bootstrap.ps1 for your stack." -ForegroundColor Yellow

Write-Host ""

# ---- Next steps -------------------------------------------------------------

Write-Host "Next steps:" -ForegroundColor White
Write-Host "  1. Complete CONTEXT.md with project-specific information."
Write-Host "  2. Update DESIGN.md with project-specific design tokens (if UI)."
Write-Host "  3. Run the project-bootstrap skill to initialize the project."
Write-Host "  4. Create docs\steps\00-plan.md using the step template."
Write-Host ""
