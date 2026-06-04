#
# setup.ps1 — create the local virtualenv and install everything the scraper needs (Windows).
#
#   .\setup.ps1
#
# If PowerShell refuses to run it ("running scripts is disabled on this system"),
# launch it once via:
#   powershell -ExecutionPolicy Bypass -File .\setup.ps1
#
# Re-runnable: reuses an existing .venv and just (re)installs the dependencies
# and the Chromium build Playwright drives. Override the interpreter with:
#   $env:PYTHON = "C:\path\to\python.exe"; .\setup.ps1
#
$ErrorActionPreference = 'Stop'

# Always operate from the project root (this script's directory).
Set-Location -LiteralPath $PSScriptRoot

# Native commands (python/pip/playwright) don't throw on a non-zero exit, so check
# $LASTEXITCODE explicitly and stop the script the moment one of them fails.
function Invoke-Checked {
    param(
        [Parameter(Mandatory)][scriptblock]$Action,
        [Parameter(Mandatory)][string]$What
    )
    & $Action
    if ($LASTEXITCODE -ne 0) {
        throw "$What failed (exit code $LASTEXITCODE)."
    }
}

# Pick an interpreter: $env:PYTHON, then 'python', then the 'py' launcher.
# (Windows usually ships 'python'/'py', not the 'python3' that setup.sh defaults to.)
$PY = $env:PYTHON
if (-not $PY) {
    if (Get-Command python -ErrorAction SilentlyContinue) {
        $PY = 'python'
    } elseif (Get-Command py -ErrorAction SilentlyContinue) {
        $PY = 'py'
    } else {
        Write-Error "No Python found. Install Python 3.10+ from https://www.python.org/downloads/ (tick 'Add python.exe to PATH'), or set `$env:PYTHON."
        exit 1
    }
}

$VENV = '.venv'

Write-Host "==> Using $(& $PY --version) at $((Get-Command $PY).Source)"

if (-not (Test-Path -LiteralPath $VENV)) {
    Write-Host "==> Creating virtualenv in $VENV"
    Invoke-Checked { & $PY -m venv $VENV } 'venv creation'
} else {
    Write-Host "==> Reusing existing virtualenv in $VENV"
}

$VPY = Join-Path $VENV 'Scripts\python.exe'

Write-Host "==> Upgrading pip"
Invoke-Checked { & $VPY -m pip install --quiet --upgrade pip } 'pip upgrade'

Write-Host "==> Installing Python dependencies (requirements.txt)"
& $VPY -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    Write-Host "==> pip failed; retrying with trusted hosts (TLS-intercepting network?)"
    Invoke-Checked {
        & $VPY -m pip install `
            --trusted-host pypi.org --trusted-host files.pythonhosted.org `
            -r requirements.txt
    } 'pip install (trusted-host retry)'
}

Write-Host "==> Installing the Playwright Chromium browser"
Invoke-Checked { & $VPY -m playwright install chromium } 'playwright install'

Write-Host @"

Done. Next:
  .\$VENV\Scripts\Activate.ps1      # then: python -m lex.cli serve
or run without activating:
  $VPY -m lex.cli serve            # web UI at http://127.0.0.1:8765
  $VPY -m lex.cli login            # CLI: log in once, then 'extract'
"@
