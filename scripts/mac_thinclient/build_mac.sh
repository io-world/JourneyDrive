#!/usr/bin/env bash
# One-shot macOS build script for journeydrive-mac.
#
# Usage:
#   scripts/mac_thinclient/build_mac.sh [--skip-tests] [--open-dist]
set -uo pipefail
# Not `set -e` — uv/pytest/pyinstaller exit codes are checked explicitly below so a
# single failed step can print a clear message instead of an opaque `set -e` abort.

SKIP_TESTS=0
OPEN_DIST=0
for arg in "$@"; do
    case "$arg" in
        --skip-tests) SKIP_TESTS=1 ;;
        --open-dist) OPEN_DIST=1 ;;
        *) echo "unknown argument: $arg" >&2; exit 1 ;;
    esac
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

step() { printf '\n==> %s\n' "$1"; }
ok()   { printf '    %s\n' "$1"; }
fail() { printf '    ERROR: %s\n' "$1" >&2; exit 1; }

# -- 1. Check uv ----------------------------------------------------------------
step "Checking for uv"
if ! command -v uv >/dev/null 2>&1; then
    echo "    uv not found - installing..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
    if ! command -v uv >/dev/null 2>&1; then
        fail "uv install succeeded but 'uv' is still not on PATH. Open a new shell and re-run this script."
    fi
fi
ok "uv $(uv --version)"

# -- 2. Sync dependencies ---------------------------------------------------------
step "Syncing dependencies (uv sync)"
uv sync || fail "uv sync failed."
ok "Dependencies up to date."

# -- 3. Run tests -------------------------------------------------------------------
if [ "$SKIP_TESTS" -eq 0 ]; then
    step "Running test suite (uv run pytest -q)"
    uv run pytest -q || fail "Tests failed - aborting build. Pass --skip-tests to bypass."
    ok "All tests passed."
else
    echo -e "\n    [skipping tests]"
fi

# -- 4. Build binary ----------------------------------------------------------------
# Read version straight from pyproject.toml (via Python's tomllib) so it always
# reflects the current source file, even if `uv sync` hasn't reinstalled since a
# version bump.
VERSION="$(uv run python -c "import tomllib; print(tomllib.load(open('pyproject.toml', 'rb'))['project']['version'])")"
[ -n "$VERSION" ] || fail "Could not read package version."
BIN_NAME="journeydrive-mac-$VERSION"
step "Building dist/$BIN_NAME (PyInstaller)"
# Remove stale spec so PyInstaller always uses our explicit flags.
rm -f "$ROOT/build/$BIN_NAME.spec"
uv run pyinstaller --onefile --console --name "$BIN_NAME" --distpath "$ROOT/dist" --workpath "$ROOT/build" --specpath "$ROOT/build" packaging/run_mac.py \
    || fail "PyInstaller build failed."
ok "Build complete: $ROOT/dist/$BIN_NAME"

# -- 5. Copy config if missing --------------------------------------------------------
CFG="$ROOT/dist/config.json"
if [ ! -f "$CFG" ]; then
    step "Copying examples/config.mac_thinclient.example.json -> dist/config.json"
    cp "$ROOT/examples/config.mac_thinclient.example.json" "$CFG"
    echo "    Edit dist/config.json and set a real broker_host/machine_id/api_key before running."
else
    ok "dist/config.json already exists - skipping copy."
fi

# -- Done -----------------------------------------------------------------------------
echo -e "\n==> Build finished successfully."
echo "    Run with:  cd dist && ./$BIN_NAME"
echo "    First run will prompt for Accessibility/Screen Recording permission — see docs/MACOS_BUILD.md."

if [ "$OPEN_DIST" -eq 1 ]; then
    open "$ROOT/dist"
fi
