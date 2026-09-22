<#
.SYNOPSIS
    One-shot Windows build script for journeydrive.exe
.PARAMETER SkipTests
    Skip the pytest run before building.
.PARAMETER OpenDist
    Open the dist\ folder in Explorer after a successful build.
.PARAMETER NoPause
    Don't wait for a keypress before closing. Double-clicking (or "Run with
    PowerShell") launches this in its own console that closes the instant the
    script exits, taking any error message with it — the default pause keeps
    that window open. Pass -NoPause when running from an existing terminal or
    from CI, where the window (or the calling process) sticks around anyway.
#>
param(
    [switch]$SkipTests,
    [switch]$OpenDist,
    [switch]$NoPause
)

Set-StrictMode -Version Latest
# Do NOT set $ErrorActionPreference = 'Stop' — uv writes info to stderr which
# would be treated as a terminating error. Exit codes are checked explicitly.

$Root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
Set-Location $Root

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Ok($msg)   { Write-Host "    $msg" -ForegroundColor Green }

function Finish($exitCode) {
    if (-not $NoPause) {
        Write-Host "`nPress Enter to close..." -ForegroundColor DarkGray
        Read-Host | Out-Null
    }
    exit $exitCode
}

function Fail($msg) { Write-Host "    ERROR: $msg" -ForegroundColor Red; Finish 1 }

# -- 1. Check uv ---------------------------------------------------------------
Step "Checking for uv"
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "    uv not found - installing..." -ForegroundColor Yellow
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    # Refresh PATH for this session
    $env:PATH = [System.Environment]::GetEnvironmentVariable('PATH', 'User') + ';' + $env:PATH
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Fail "uv install succeeded but 'uv' is still not on PATH. Open a new PowerShell and re-run this script."
    }
}
Ok "uv $(uv --version)"

# -- 2. Sync dependencies ------------------------------------------------------
Step "Syncing dependencies (uv sync)"
uv sync
if ($LASTEXITCODE -ne 0) { Fail "uv sync failed." }
Ok "Dependencies up to date."

# -- 3. Run tests ---------------------------------------------------------------
if (-not $SkipTests) {
    Step "Running test suite (uv run pytest -q)"
    # --basetemp keeps pytest's tmp_path fixture off the shared %TEMP%\pytest-of-<user>
    # tree: that directory accumulates a "pytest-current" symlink pytest re-points on
    # every run, and a stale/corrupted one there can make an otherwise-passing run
    # crash during teardown with a WinError 5 (access denied) unrelated to the tests
    # themselves. build\ is already gitignored and safe to wipe each run.
    uv run pytest -q --basetemp="$Root\build\pytest-tmp"
    if ($LASTEXITCODE -ne 0) { Fail "Tests failed - aborting build. Use -SkipTests to bypass." }
    Ok "All tests passed."
} else {
    Write-Host "`n    [skipping tests]" -ForegroundColor Yellow
}

# -- 4. Build exe ---------------------------------------------------------------
# Read version straight from pyproject.toml (via Python's tomllib) so it always
# reflects the current source file, even if `uv sync` hasn't reinstalled since a
# version bump.
$Version = uv run python -c "import tomllib; print(tomllib.load(open('pyproject.toml', 'rb'))['project']['version'])"
if (-not $Version) { Fail "Could not read package version." }
$ExeName = "journeydrive-$Version"
Step "Building dist\${ExeName}.exe (PyInstaller)"
# Remove stale spec so PyInstaller always uses our explicit flags
Remove-Item -Path "$Root\${ExeName}.spec" -ErrorAction SilentlyContinue
uv run pyinstaller --onefile --console --name $ExeName --distpath "$Root\dist" --workpath "$Root\build" --specpath "$Root\build" packaging/run.py
if ($LASTEXITCODE -ne 0) { Fail "PyInstaller build failed." }
Ok "Build complete: $Root\dist\${ExeName}.exe"

# -- 5. Copy config if missing --------------------------------------------------
$cfg = "$Root\dist\config.json"
if (-not (Test-Path $cfg)) {
    Step "Copying examples\config.example.json -> dist\config.json"
    Copy-Item "$Root\examples\config.example.json" $cfg
    Write-Host "    Edit dist\config.json and set a real api_key and allowed_ips before running." -ForegroundColor Yellow
} else {
    Ok "dist\config.json already exists - skipping copy."
}

# -- Done -----------------------------------------------------------------------
Write-Host "`n==> Build finished successfully." -ForegroundColor Green
Write-Host "    Run with:  cd dist ; .\${ExeName}.exe" -ForegroundColor White

if ($OpenDist) { Start-Process explorer.exe "$Root\dist" }

Finish 0
