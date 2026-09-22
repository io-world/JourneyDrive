# Building the macOS thin client

PyInstaller bundles the host platform's Python interpreter and native libraries — it
does not cross-compile. This build must run on a real Mac. All commands below are
`bash`/`zsh`, run on macOS.

`journeydrive_mac_thinclient` is a second agent alongside
`journeydrive_windows_thinclient`, both speaking the same wire protocol to the same
`journeydrive_broker` — see `docs/THIN_AGENT_PLAYBOOK.md` for how it was built and
what's shared vs. genuinely OS-specific. It reuses the exact same `capture.py`
(`mss`) and `input_control.py` (`pynput`) approach as the Windows agent — both
libraries are cross-platform and were verified live on a real Mac to report/act on
the same real-pixel coordinate space `mss` reports on Windows, with no Retina
"points vs. pixels" correction needed.

## 0. Get the code onto the Mac

```bash
git clone https://github.com/io-world/journeydrive.git
cd journeydrive
```

(Or `git pull` if you already have a checkout there.)

## 1. Grant permissions first (manual, can't be scripted)

macOS gates synthetic mouse/keyboard input and screen capture behind two
per-process permission grants, and will silently withhold them (sometimes without
even prompting, depending on macOS version) rather than error clearly — grant these
*before* your first real test, not after debugging a mysterious no-op:

1. **System Settings → Privacy & Security → Accessibility** — add and enable the
   terminal app (or the built `journeydrive-mac-<version>` binary itself, if
   you're running the built binary rather than `uv run` from a terminal) that will
   run the agent. Required for `pynput` to move the mouse, click, or type.
2. **System Settings → Privacy & Security → Screen Recording** — same, for `mss` to
   capture the screen. Required even for `list_monitors`, not just `take_screenshot`.

If you re-run a rebuilt binary and input/capture stop working with no error, the
first thing to check is whether the *new* binary needs to be re-added under both
lists (a changed binary path/signature can require re-granting).

## 2. One-shot build

```bash
scripts/mac_thinclient/build_mac.sh
```

This does everything through step 6 below in one command: installs `uv` if it's
missing, runs `uv sync`, runs the test suite (`uv run pytest -q`, aborting the build
on failure — pass `--skip-tests` to bypass), builds
`dist/journeydrive-mac-<version>` with PyInstaller (version read straight from
`pyproject.toml`'s `[project].version`), and copies `examples/config.example.json` →
`dist/config.json` if one isn't already there. Pass `--open-dist` to have it open
`dist/` in Finder when done.

Skip to [step 6](#6-set-up-configjson-next-to-the-binary) to configure and run it.
The manual steps below are what the script automates.

## 3. Install dependencies (manual)

```bash
uv sync
```

This creates `.venv` and installs everything from `pyproject.toml`/`uv.lock` —
`journeydrive_mac_thinclient` needs no extra dependency group; `mss`/`pynput`/
`pydantic`/`websockets` are already base project dependencies shared with the
Windows agent.

## 4. Run the test suite first (manual)

```bash
uv run pytest -q
```

`tests/test_mac_config.py`, `tests/test_mac_input_control.py`, and
`tests/test_mac_ws_client.py` cover everything above the `mss`/`pynput` leaf calls
(config validation, dispatch, auto-release, reconnect) with those mocked out — a
good sanity check the checkout is intact before spending time on a build.

## 5. Build the binary (manual)

```bash
uv run pyinstaller --onefile --console --name journeydrive-mac packaging/run_mac.py
```

This produces `dist/journeydrive-mac`, plus a `build/` scratch directory and a
generated `.spec` file — both gitignored/disposable. If mouse/keyboard control or
screenshots fail with an import error at runtime, add the missing module with
`--hidden-import <module>`.

## 6. Set up config.json next to the binary

`build_mac.sh` does this for you if `dist/config.json` doesn't already exist. To do
it by hand:

```bash
cp examples/config.example.json dist/config.json
open -e dist/config.json
```

Edit it: set `broker_host`/`broker_port` to point at the broker this machine should
connect to, `machine_id` to a name unique among that broker's configured machines,
and `api_key` to match that `machine_id`'s key in the broker's own `machines` config
(see `docs/BROKER.md`) — the broker rejects the connection if either doesn't match.
The binary refuses to start on invalid/incomplete config (a placeholder key that's
too short, a missing `machine_id`) — same fail-fast philosophy as the rest of the
system.

## 7. Run it

```bash
cd dist
./journeydrive-mac-<version>
```

The first run triggers the Accessibility/Screen Recording permission prompts if you
skipped step 1 — grant them, then re-run. This machine only ever connects *out* to
the broker (a websocket client, not a listening server). Confirm success by checking
`journeydrive-mac.log` next to the binary for a successful broker connection, and
`GET /machines` on the broker's HTTP API for this `machine_id` showing up as
connected.

## 8. Publish a release (optional)

```bash
gh release create v<version> "dist/journeydrive-mac-<version>" --title v<version> --notes "..."
```

**Remember to rebuild and publish a new release after any change to
`input_control.py`, `capture.py`, or other runtime code in
`journeydrive_mac_thinclient`** — a published release is a frozen artifact;
pulling the latest source on the Mac doesn't update an already-built/running binary.

## Next: functional testing

The build succeeding doesn't mean mouse/keyboard/screenshot behavior is correct —
walk through [MACOS_SMOKE_TEST.md](MACOS_SMOKE_TEST.md) next, or use the
`scripts/testing/*.py` live-testing scripts from a controller machine (see
`CLAUDE.md`) — they only talk to the broker's HTTP API, so they work against this
agent with zero script changes, same as the Windows one.

## Known friction

A onefile binary that opens an outbound network connection and drives mouse/keyboard
matches the heuristic signature of a RAT. Depending on distribution method,
Gatekeeper may quarantine an unsigned binary downloaded from outside the Mac (`xattr
-d com.apple.quarantine dist/journeydrive-mac-<version>` removes the quarantine
flag for local testing; real distribution would need Apple notarization). Code-
signing/notarization is not something the Python code can fix.
