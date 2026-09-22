# Building the Windows executable

PyInstaller bundles the host platform's Python interpreter and native DLLs — it does not
cross-compile. This build must run on a real Windows machine (or a `windows-latest`
GitHub Actions runner). All commands below are PowerShell, run on Windows.

Building the macOS agent instead? See [MACOS_BUILD.md](MACOS_BUILD.md).

## 0. Get the code onto the Windows machine

```powershell
git clone https://github.com/io-world/journeydrive.git
cd journeydrive
```

(Or `git pull` if you already have a checkout there.)

## 1. One-shot build

```powershell
powershell -ExecutionPolicy Bypass -File scripts\thinclient\build_windows.ps1
```

This does everything through step 6 below in one command: installs `uv` if it's
missing, runs `uv sync`, runs the test suite (`uv run pytest -q`, aborting the build on
failure — pass `-SkipTests` to bypass), builds `dist\journeydrive-win-<version>.exe` with
PyInstaller (version read straight from `pyproject.toml`'s `[project].version` via
Python's `tomllib`, so the exe name always matches the source file even if `uv sync`
hasn't reinstalled since a version bump), and copies `examples\config.win_thinclient.example.json` →
`dist\config.json` if one isn't already there. Pass `-OpenDist` to have it open
`dist\` in Explorer when done.

If you open a fresh PowerShell window to run this, it starts in your user home
directory (or `system32` if launched some other way), not the repo — `cd` into the
repo first, or the script/`.venv` paths below won't resolve:

```powershell
cd "C:\Users\me\OneDrive\Desktop\JourneyDrive"
.\scripts\thinclient\build_windows.ps1
```

Skip to [step 6](#6-set-up-configjson-next-to-the-exe) to configure and run it. The
manual steps below are what the script automates — use them if you need to customize
the PyInstaller invocation (e.g. adding a `--hidden-import`).

## 2. Install prerequisites (manual)

- **Python 3.11+**: not strictly required up front — `uv` can download and manage it
  for you — but having it doesn't hurt.
- **uv**: install if not already present:
  ```powershell
  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  ```
  Then restart the PowerShell window so `uv` is on `PATH`.

## 3. Install dependencies (manual)

```powershell
uv sync
```

This creates `.venv` and installs everything from `pyproject.toml`/`uv.lock`, including
the `pyinstaller` dev dependency.

## 4. Run the test suite first (manual)

```powershell
uv run pytest -q
```

They run cross-platform with `mss`/`pynput` mocked, so this is a good sanity check
that the checkout is intact before spending time on a build. A plain `uv sync` doesn't
install the `mcp`/`broker` extras (those are controller-side only — see
`docs/DEPENDENCIES.md`), so tests that need them are skipped automatically
(`pytest.importorskip`), not failed — a run showing some `s` alongside passes is
expected on Windows, not a problem.

## 5. Build the executable (manual)

```powershell
uv run pyinstaller --onefile --console --name journeydrive-win packaging/run.py
```

- `--console` (not `--windowed`) is intentional for the first build, so stdout/stderr
  are visible if something goes wrong at startup. Switch to `--windowed` later once
  file logging (`journeydrive-win.log`) has been proven sufficient on its own.
- This produces `dist\journeydrive-win.exe`, plus a `build\` scratch directory and a
  generated `journeydrive-win.spec` in the repo root — both are gitignored/disposable.
- If mouse/keyboard control or screenshots fail with an import error at runtime,
  `pynput` ships its own PyInstaller hook and should Just Work; `mss` needs no special
  hook (pure `ctypes` against `user32`/`gdi32`). Otherwise add the missing module
  explicitly with `--hidden-import <module>`.

## 6. Set up config.json next to the exe

`build_windows.ps1` does this for you if `dist\config.json` doesn't already exist. To
do it by hand:

```powershell
Copy-Item examples\config.win_thinclient.example.json dist\config.json
notepad dist\config.json
```

Edit it: set `broker_host`/`broker_port` to point at the broker this machine should
connect to, `machine_id` to a name unique among that broker's configured machines, and
`api_key` to match that `machine_id`'s key in the broker's own `machines` config (see
`docs/BROKER.md`) — the broker rejects the connection if either doesn't match. The exe
refuses to start on invalid/incomplete config (a placeholder key that's too short, a
missing `machine_id`) — this is deliberate, same fail-fast philosophy as the rest of
the system.

## 7. Run it

```powershell
cd dist
.\journeydrive-win-<version>.exe
```

This machine only ever connects *out* to the broker (a websocket client, not a
listening server), so there's no inbound Firewall prompt to expect anymore — confirm
it instead by checking `journeydrive-win.log` next to the exe for a successful broker
connection, and `GET /machines` on the broker's HTTP API for this `machine_id`
showing up as connected.

## 8. Publish a release (optional)

```powershell
gh release create v<version> "dist\journeydrive-win-<version>.exe" --title v<version> --notes "..."
```

**Remember to rebuild and publish a new release after any change to
`input_control.py`, `capture.py`, or other runtime code** — a published release is a
frozen artifact; pulling the latest source on the Windows box doesn't update an
already-built/running `.exe`, and re-running `build_windows.ps1` doesn't re-publish the
release on its own.

## Next: functional testing

The build succeeding doesn't mean mouse/keyboard/screenshot behavior is correct —
walk through [WINDOWS_SMOKE_TEST.md](WINDOWS_SMOKE_TEST.md) next, or use the
`scripts/testing/*.py` live-testing scripts from a controller machine (see `CLAUDE.md`).

## Known friction

A onefile exe that opens an outbound network connection and drives mouse/keyboard
matches the heuristic signature of a RAT. Expect Windows Defender/AV flags. Code-
signing and/or AV exclusions may be needed for real deployment — this is not something
the Python code can fix.

## Troubleshooting

**`Access is denied` deleting a `journeydrive-<version>.dist-info` folder during
`uv sync`.** This repo lives under `OneDrive\Desktop\JourneyDrive` for most
contributors, and OneDrive's sync engine grabs a file handle on newly-written files
right as `uv` tries to delete/replace them during a reinstall — a race, not a real
permissions problem. Fix by rebuilding `.venv` from scratch:

```powershell
Remove-Item -Recurse -Force .venv
.\scripts\thinclient\build_windows.ps1
```

(`.venv` is gitignored/disposable — `uv sync` recreates it.) If you were in an
activated venv when this happened, run `deactivate` first. To stop this recurring on
future builds, either exclude `.venv` from OneDrive sync (OneDrive Settings → Sync and
backup → manage the folder's backup/exclusions), or point `uv` at a venv location
outside OneDrive via the `UV_PROJECT_ENVIRONMENT` env var.

**A PowerShell window running the script closes immediately on error, before you can
read what failed.** This happens when the script is launched in a way that closes its
host window on exit (e.g. double-clicking the `.ps1`, or some "Run with PowerShell"
shortcuts) rather than run inside a window that stays open. Run it from an already-open
PowerShell window instead (`cd` to the repo, then `.\scripts\thinclient\build_windows.ps1`), or
capture output to a file to inspect after the fact: `.\scripts\thinclient\build_windows.ps1 *>&1 |
Tee-Object build_log.txt`.

## Optional: CI builds instead of a personal Windows box

A `windows-latest` GitHub Actions workflow running the same `uv sync` +
`build_windows.ps1` steps and uploading the exe as a build artifact (or publishing a
release directly) removes the need to do this manually on every release. Not set up
yet — ask if you want one added.
