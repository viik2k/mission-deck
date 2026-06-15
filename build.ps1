#Requires -Version 5.1
<#
.SYNOPSIS
    One-command build of the single, windowed mission-deck.exe.

.DESCRIPTION
    Thin convenience wrapper around `pyinstaller mission-deck.spec`. It runs from
    the repo root (regardless of the current directory), verifies PyInstaller is
    installed in the active interpreter, clears stale build artifacts, runs the
    build via `python -m PyInstaller`, and reports the resulting EXE path + size.

    Run from an activated virtualenv that has the dev dependencies installed:

        pip install -r requirements-dev.txt
        .\build.ps1

    The build is driven entirely by mission-deck.spec - that file remains the
    single source of truth for what gets bundled (config.example.json,
    CustomTkinter assets), the icon, and console/windowed mode.

.PARAMETER Clean
    Also remove the PyInstaller `build\` work directory and the generated
    `__pycache__` trees before building (a fully cold rebuild). `dist\` is always
    cleared so a failed build can't leave a stale EXE behind.

.EXAMPLE
    .\build.ps1
    Builds dist\mission-deck.exe.

.EXAMPLE
    .\build.ps1 -Clean
    Cold rebuild from scratch.
#>
[CmdletBinding()]
param(
    [switch]$Clean
)

$ErrorActionPreference = "Stop"

# Always operate from the repo root (the folder this script lives in), so the
# relative paths in mission-deck.spec resolve no matter where it's invoked from.
$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Definition
Push-Location $RepoRoot
try {
    $Spec = Join-Path $RepoRoot "mission-deck.spec"
    if (-not (Test-Path $Spec)) {
        throw "mission-deck.spec not found in $RepoRoot. Run this from the repo root."
    }

    # Resolve a Python interpreter (prefer the active venv on PATH).
    $Python = (Get-Command python -ErrorAction SilentlyContinue).Source
    if (-not $Python) {
        throw "Python not found on PATH. Activate your virtualenv first (venv\Scripts\Activate.ps1)."
    }

    # Verify PyInstaller is importable in *this* interpreter before we start.
    & $Python -c "import PyInstaller" 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller is not installed in the active interpreter. Run: pip install -r requirements-dev.txt"
    }

    Write-Host "==> Building mission-deck.exe" -ForegroundColor Cyan
    Write-Host "    Python : $Python"
    Write-Host "    Spec   : $Spec"

    # Always clear dist\ so a failed build never leaves a stale EXE in place.
    $Dist = Join-Path $RepoRoot "dist"
    if (Test-Path $Dist) {
        Write-Host "==> Clearing dist\" -ForegroundColor DarkGray
        Remove-Item -Recurse -Force $Dist
    }

    if ($Clean) {
        $BuildDir = Join-Path $RepoRoot "build"
        if (Test-Path $BuildDir) {
            Write-Host "==> Clearing build\ (cold rebuild)" -ForegroundColor DarkGray
            Remove-Item -Recurse -Force $BuildDir
        }
        Get-ChildItem -Path $RepoRoot -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -notmatch "\\venv\\" } |
            Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    }

    # Hand off to PyInstaller. `--noconfirm` keeps it non-interactive (overwrites
    # output without prompting); everything else comes from the spec.
    & $Python -m PyInstaller --noconfirm $Spec
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE."
    }

    $Exe = Join-Path $Dist "mission-deck.exe"
    if (-not (Test-Path $Exe)) {
        throw "Build reported success but $Exe was not produced."
    }

    $SizeMB = [math]::Round((Get-Item $Exe).Length / 1MB, 1)
    Write-Host ""
    Write-Host "==> Done: $Exe ($SizeMB MB)" -ForegroundColor Green
}
finally {
    Pop-Location
}
