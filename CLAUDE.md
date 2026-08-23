# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Four components in one repo, meant to run on (potentially) several different machines:

```
MCP client  --stdio/HTTP-->  journeycapture_mcp  --HTTP-->  journeycapture_broker  <--WebSocket--  journeycapture_windows_thinclient(s)
                                                                                    <--WebSocket--  journeycapture_mac_thinclient(s)
```

- **`journeycapture_windows_thinclient`** — a Windows thin client that drives mouse/keyboard/
  screenshot capture, only ever *run* as a packaged `.exe` on a real Windows desktop.
  It does **not** run a server — it connects *out* to the broker over a websocket and
  stays connected, so it never needs an inbound port open. See "Retired: the thin
  client's REST API" below for why.
- **`journeycapture_mac_thinclient`** — the same agent role for a Mac, added per
  `docs/THIN_AGENT_PLAYBOOK.md`'s recipe for a new agent OS. `pynput`/`mss` turned
  out to be genuinely cross-platform (verified live against a real Retina Mac, not
  assumed) — `capture.py`/`input_control.py` are near-identical to the Windows
  agent's, and `ws_client.py` reproduces the same wire protocol byte-for-byte. Kept
  as a fully separate package rather than one package branching on `sys.platform`,
  per the playbook's guidance — see "journeycapture_mac_thinclient" below and
  `docs/MACOS_BUILD.md`.
- **`journeycapture_broker`** — routes requests from the MCP server to whichever
  thin client they're addressed to (by `machine_id`), regardless of which OS agent
  is behind that id. One broker can relay to many machines at once — this is what
  makes "one MCP server, many machines" possible. It's also the single place
  operational config for every thin client and the MCP server can live, instead of
  each needing its own local copy — see "Broker-pushed config" below and
  `docs/BROKER.md`.
- **`journeycapture_mcp`** — an MCP server exposing the broker's HTTP API as MCP
  tools, one `machine` parameter per tool. Runs on the *controller* machine
  (wherever your MCP client is). See `docs/MCP_SERVER.md`.

## Commands

```
uv sync                    # install deps for both thin clients (creates .venv)
uv sync --extra broker     # also install deps for the broker (controller-side only)
uv sync --extra mcp        # also install deps for the MCP server (controller-side only)
uv run journeycapture      # run the Windows thin client from source (needs config.json - see below)
uv run journeycapture-mac  # run the macOS thin client from source (needs config.json - see below)
uv run journeycapture-broker --config broker_config.json  # run the broker
uv run journeycapture-mcp --config scripts/mcp/mcp_config.json  # run the MCP server
uv run pytest -q           # run the full test suite
uv run pytest tests/test_config.py::test_valid_config_parses_with_defaults  # run a single test
```

Thin client config: copy `examples/config.example.json` to `config.json`, set `broker_host`/
`machine_id`/`api_key` to match an entry in the broker's own `machines` config.
Config path resolution order: `--config PATH` CLI arg → `JOURNEYCAPTURE_CONFIG` env
var → `config.json` next to the executable (or CWD when run from source). Refuses to
start on a missing/invalid config (see `journeycapture_windows_thinclient.config.load_config`).

Broker config: copy `examples/config.broker.example.json`, set its own `api_key` (what the MCP
server authenticates with) and a `machines` map of `machine_id: api_key` pairs — see
`docs/BROKER.md`.

There is no lint/format command configured in this repo.

## Architecture

### Retired: the thin client's REST API

Through `2026-08-19`, `journeycapture_windows_thinclient` ran its own FastAPI/uvicorn HTTP
server (`api.py`, `routes/`, `security.py`), authenticated by an API-key header plus a
source-IP allowlist. That's gone — replaced by the broker/websocket design described
above, to prep the architecture for multiple machines and machines that aren't
directly network-reachable from the controller (NAT/firewalls). If you're reading
old context (commits, docs, memory) that mentions `/mouse/move`, `allowed_ips`, or
`journeycapture_windows_thinclient.routes`, it's describing the pre-broker design — the
*behavior* those routes implemented still exists, just reachable through the broker's
`/machines/{id}/...` HTTP API now, not directly from the thin client. See
`docs/CHANGELOG.md`'s broker entries for the full reasoning.

- **`journeycapture_windows_thinclient.server`** — entry point (`main()`). Loads config, sets
  up logging, calls `winutil.set_dpi_awareness()` (Windows-only, no-op elsewhere),
  then runs `asyncio.run(ws_client.run(config))`.
- **`journeycapture_windows_thinclient.ws_client`** — connects to the broker via
  `websockets.connect()`'s built-in auto-reconnect-with-backoff
  (`async for websocket in connect(...)`), sends a `{"machine_id", "api_key"}`
  handshake, then loops reading `{"id", "method", "params"}` messages and dispatching
  them through a small method-name → function table to the same `capture`/
  `input_control` functions the old routes called — that logic didn't change at all,
  see below. Each handler runs via `asyncio.to_thread` so a long blocking call (e.g. a
  ~40s `type_text`) doesn't stall the event loop's websocket keepalive pings and get
  the connection dropped. `screenshot` is the one method with a different response
  shape — see `docs/BROKER.md` on binary frames. A rejected handshake (unknown
  `machine_id` or wrong `api_key`) raises `RegistrationRejected` instead of being
  swallowed — auto-reconnect only makes sense for transient network issues, not wrong
  credentials, so `server.main()` catches it and exits non-zero with a clear stderr
  message rather than exiting 0 indistinguishably from a normal shutdown.
- **Auth model now**: each thin client's `api_key` (in its own `config.json`)
  authenticates its websocket handshake to the broker, checked against that
  `machine_id`'s entry in the broker's `machines` config
  (`journeycapture_broker.ws_server`). Separately, the broker's own `api_key`
  authenticates the MCP server's HTTP calls to it
  (`journeycapture_broker.http_api`). No IP allowlist anymore — a websocket
  connecting *out* doesn't have a client IP to allowlist the way an inbound
  connection did. TLS is opt-in on both legs, off by default (plaintext is still the
  accepted tradeoff as long as everything's on a trusted network): the broker turns
  it on for both its listeners by setting `tls_cert_file`/`tls_key_file`; each
  client (thin client's `broker_tls`, MCP server's `broker_scheme=https`, or
  `scripts/testing/*.py`'s `--broker-scheme https`) turns it on by also setting a matching
  `broker_cert_fingerprint`. Trust model is a self-signed certificate plus pinned
  fingerprint verification (TOFU, like an SSH `known_hosts` entry) — not a private
  CA or mutual TLS — since nothing in this system is ever reached by a DNS name,
  only a raw LAN IP, so a public CA isn't an option and a private CA/mTLS would be
  more PKI than this system's scale needs. See
  `journeycapture_windows_thinclient.tls_pinning` and `docs/BROKER.md`'s "TLS setup"
  section for the mechanics and cert-generation steps. This is additive to, not a
  replacement for, the `api_key`/`machine_id` auth model above.
- **`journeycapture_windows_thinclient.capture`/`input_control`** — unchanged by the broker
  work. All actual mouse/keyboard/screenshot logic lives here; nothing in these two
  modules knows or cares whether it's being called from an HTTP route (the old
  design) or a websocket dispatch table (now).
- **`journeycapture_windows_thinclient.config.Config`** — pydantic model with
  `extra="forbid"`, so an unrecognized config key is a hard validation error, not a
  silent no-op. Same philosophy in `schemas.py` (shared with the broker — see below):
  `MouseClickRequest` has a `model_validator` rejecting a lone `x` or `y` (must be
  given together, or both omitted) with a validation error — it used to silently
  ignore a partial pair and click at the current cursor position instead.
- **`packaging/run.py`** — the PyInstaller entry point (`from journeycapture_windows_thinclient import
  main; main()`), kept as a separate file from `server.py` deliberately for the build.

### `journeycapture_mac_thinclient` — the macOS agent

Lives in `src/journeycapture_mac_thinclient/`, a fourth top-level package alongside
the other three (`[tool.uv.build-backend] module-name` lists all four). Built
following `docs/THIN_AGENT_PLAYBOOK.md`'s recipe for adding a new agent OS — the
broker and MCP server needed **zero changes** to support it, since both agents speak
the identical wire protocol described in that doc's §1.

- **Same base `dependencies`, no new ones.** `mss`/`pynput` turned out to be
  genuinely cross-platform, not "Windows-only in practice" as the Windows agent's
  own module once assumed — `capture.py` (`mss`) and `input_control.py` (`pynput`)
  are near-identical to their Windows counterparts. Verified live against a real
  Retina Mac during development (not assumed): `mss`'s macOS (Quartz) backend
  already reports monitor bounds and captures pixel data in real display pixels —
  `sct.monitors[n]["width"/"height"]` matched `sct.grab(...)`'s actual image size
  exactly, with no HiDPI "points vs. pixels" correction needed — and `pynput`'s
  reported cursor position falls within the same real-pixel monitor bounds `mss`
  reports, not a scaled-down "points" space. This means the fx/fy fractional-
  coordinate math in `journeycapture_mcp` (see "Never assume the screen resolution"
  below) works identically on this agent with no Mac-specific correction.
- **Fully separate package, not one package branching on `sys.platform`.** Per the
  playbook's explicit guidance — `journeycapture_windows_thinclient.capture`/
  `input_control`/`config`/`schemas`/`logging_setup`/`ws_client`/`server` are each
  duplicated (not imported) into the mac package's own modules, so the two agents
  stay independently buildable/packageable/testable without pulling in the other's
  namespace. The one exception: `journeycapture_mac_thinclient.ws_client` imports
  `tls_pinning` directly from `journeycapture_windows_thinclient` rather than
  duplicating it — that module is pure-stdlib certificate-fingerprint logic with no
  OS-specific code, and `journeycapture_mcp.client` already reuses it the same way,
  so a future fix to the pinning logic only needs to land in one place.
- **No DPI-awareness equivalent needed.** The Windows agent's `winutil.py` exists
  because Windows silently reports pre-DPI-scaling coordinates unless a process
  opts in to per-monitor DPI awareness. macOS has no equivalent opt-in step to call
  — the `mss`/`pynput` verification above already confirmed real pixels are
  reported without one, so `journeycapture_mac_thinclient.server` simply has no
  call here at all, rather than a mac-specific no-op function mirroring
  `winutil.set_dpi_awareness()`.
- **macOS-only manual step**: `pynput` (mouse/keyboard) and `mss` (screen capture)
  both require this process to be trusted under System Settings → Privacy &
  Security → Accessibility and → Screen Recording respectively — a one-time grant
  that can't be scripted around, and that macOS can silently withhold without even
  prompting depending on version. See `docs/MACOS_BUILD.md` step 1.
- **`packaging/run_mac.py`** — the PyInstaller entry point for this agent (`from
  journeycapture_mac_thinclient import main; main()`), same reasoning as
  `packaging/run.py`.
- **`scripts/mac_thinclient/build_mac.sh`** — the one-shot build script, a bash
  port of `scripts/thinclient/build_windows.ps1`'s steps (installs `uv` if missing,
  syncs deps, runs the test suite, builds a version-named
  `dist/journeycapture-mac-<version>` via PyInstaller, copies
  `examples/config.example.json` → `dist/config.json` if missing). PyInstaller
  doesn't cross-compile, so this must run on a real Mac, same constraint as the
  Windows build.
- Tests: `tests/test_mac_config.py`, `tests/test_mac_input_control.py`,
  `tests/test_mac_ws_client.py` mirror `test_config.py`/`test_input_control.py`/
  `test_ws_client.py`'s structure exactly, per the playbook's testing guidance —
  everything above the `mss`/`pynput` leaf calls (config validation, dispatch,
  auto-release, reconnect) is unit tested with those mocked out.

### Keyboard typing is intentionally paced

`input_control.type_text` sends one character at a time with a small `time.sleep`
between each, rather than a single unpaced `pynput` `.type(text)` call. An unpaced call
was found to silently drop and corrupt characters under load (verified via live
testing against a real Windows box — not reproducible in the mocked test suite), while
still reporting the full length as typed. Don't revert to a single bulk `.type()` call
without re-verifying against a live instance. `\n` in typed text is translated to
`Key.enter` by `pynput` automatically (see `pynput`'s `_CONTROL_CODES`), so no special
handling is needed to end typed text with a return.

`journeycapture_mac_thinclient.input_control.type_text` keeps the same paced
behavior as a conservative default — this was **not** independently re-verified as
necessary on macOS (only the `mss`/`pynput` coordinate-space finding above was); per
`docs/THIN_AGENT_PLAYBOOK.md` §3, don't assume the Windows corruption finding
transfers, but don't assume it doesn't either — verify against a live Mac before
trusting an unpaced call there.

### Never assume the screen resolution

`/mouse/move` and `/mouse/click`'s `x`/`y` are real pixel coordinates on the actual
Windows box — there's no scaling or normalization. A caller (human or LLM) that
estimates an icon's position from a screenshot without confirming the image's real
pixel dimensions first will silently compute coordinates for the wrong resolution and
click empty desktop instead. This actually happened: something assumed 1366×768 on a
machine that's really 1920×1080, and every click landed about 29% short of the real
target (`351×(1920/1366)≈493`, the real x for what should have been Chrome's icon).
`GET /screenshot/monitors` and `take_screenshot`'s own response both carry the real
`width`/`height` — always read coordinates from one of those, never assume a
standard resolution. `journeycapture_mcp`'s `list_monitors`/`take_screenshot` tool
docstrings and `screenshot_monitors`'s OpenAPI description both say this explicitly
for the same reason. See `docs/CHANGELOG.md`'s "Fix the actual root cause" entry.

This trap turned out to have a second form even after that fix landed: an MCP client
correctly checked `list_monitors`, then still miscalculated because it reasoned that
the screenshot image "renders smaller in the chat" than its real dimensions and tried
to derive its own scale-factor correction — conflating how big an image *looks* in a
UI with the actual pixel data a model reasons over, which are unrelated. It happened
to land two clicks correctly; there was no way for the model to actually verify the
scale factor it invented, so treat that as luck, not a validated technique. The real
fix, in `journeycapture_mcp.server`: `move_mouse`/`click_mouse` accept `fx`/`fy`
(0.0–1.0, a fraction of the target monitor) as an alternative to pixel `x`/`y`. A
fraction is correct regardless of what size the model actually perceived the image
at — no scale-factor guessing needed, ever. Prefer `fx`/`fy` over `x`/`y` whenever a
target was identified visually from a screenshot. See `docs/MCP_SERVER.md`'s
"Fractional coordinates" section and `docs/CHANGELOG.md`'s corresponding entry.

### Screenshot/pixel control is deliberate, not a limitation to patch

UI Automation (UIA)-based element targeting — asking Windows directly "where is the
button named X" instead of guessing a pixel coordinate from a screenshot — was
considered and explicitly rejected, not overlooked. The design goal is to mimic how
an actual human uses the computer: look at the screen, decide where to click based on
what's visible, act — the same thing a screenshot + pixel coordinates already does.
UIA would be a fundamentally different paradigm (querying a structured accessibility
tree a user never sees), not a refinement of this one.

It was also weighed against real costs, not just philosophy: UIA's `Value` pattern
exposes live text field contents (a privacy leak the `/keyboard/type` audit-logging
carve-out specifically avoids elsewhere), UIA calls can hang against unresponsive
apps, coordinates from a second system would need their own DPI/multi-monitor
correctness verification, and splitting "find" and "click" into two calls reopens a
staleness/TOCTOU window. None of that changes the resolution-assumption bug this
session actually hit — the fix for that is `list_monitors`/reading the screenshot's
real dimensions (see below), which is already in place and doesn't require any of
this. Don't propose UIA-based targeting again without this context.

### Every command is logged; held input auto-releases

Every `ws_client.py` handler (`_handle_mouse_move`, `_handle_keyboard_type`, etc.)
logs one line before acting, via `logging.getLogger(__name__)` — reaches both the
console and the rotating log file through the root-logger handlers
`logging_setup.configure_logging` already sets up, so nothing extra is needed to see
it. `keyboard_type` logs the character *count* only, never the text itself, since
that could be a password or other sensitive content. `health` is deliberately not
logged — it's a liveness ping, not a remote-control command.

`click_mouse`/`send_keys`'s `action="down"`/`"press"` (holding a button/key across
separate requests, e.g. for a drag) auto-release after `_AUTO_RELEASE_SECONDS` (10s) if
the matching `"up"`/`"release"` never arrives, so a dropped request can't leave input
stuck on the remote machine indefinitely. `action="tap"` (the default, and everything
the test suite and live-testing scripts use) presses and releases within the same call
and never touches this — only an explicit `down`/`press` schedules a timer. See
`tests/test_input_control.py` for how this is tested without waiting on the real timeout.

### Testing against a real Windows or Mac instance

`scripts/` is organized by which component each file's config belongs to —
`scripts/broker/` (`broker_config.json`, plus `broker_cert.pem`/`broker_key.pem` if
TLS is on), `scripts/mcp/` (`mcp_config.json`), `scripts/thinclient/`
(`thinclient_config.json`, `build_windows.ps1`), `scripts/mac_thinclient/`
(`mac_thinclient_config.json`, `build_mac.sh`) — with everything else (the live-test
scripts below, and their own shared config) under `scripts/testing/`. Every file
under these six directories except `build_windows.ps1`/`build_mac.sh` is gitignored
(all contain real API keys or private key material).

Since `pynput`/`mss` behavior and the packaged executables can only be fully
verified on the real target OS, `scripts/testing/` holds standalone httpx-based
scripts for live testing, run from any machine that can reach the target (never on
either thin client itself):

- `scripts/testing/live_check.py` — full smoke test: health, wrong-key rejection,
  monitors, screenshot, optional `--with-mouse`/`--with-keyboard` round trips.
- `scripts/testing/get_screenshot.py` — fetch one screenshot, saved as a timestamped
  file under `screenshot_dir` (same config key/default `journeycapture_mcp`'s
  optional local-saving feature uses, so both land in one shared folder) unless
  `--out` gives an exact path.
- `scripts/testing/send_text.py` — type a `TEXT` string (edit the constant at the top
  of the file) into whatever window has focus remotely.
- `scripts/testing/move_mouse.py` — walk the cursor through the primary monitor's
  corners and center plus one relative move, checking the reported position against
  what was requested at each step.
- `scripts/testing/click_test.py` — single-click vs double-click at a fixed point,
  saving before/after screenshots so you can visually confirm a single click selects
  without launching and a double click launches.

All five route through the broker (`{broker_scheme}://{broker_host}:{broker_port}/machines/{machine_id}/...`),
not the thin client directly — the thin client's own REST API is retired (see above).
They read connection defaults from `scripts/testing/config.json`, which is
gitignored (contains real API keys) — there's no committed example for it, so
recreate it locally with the field names each script's `--help` documents
(`broker_host`, `broker_port`, `broker_scheme`, `machine_id`, `broker_api_key`, and
`broker_cert_fingerprint` if `broker_scheme` is `https`).

Manual-only checks that can't be scripted from macOS (DPI-scaling coordinate
correctness, UIPI/elevated-window behavior, Firewall/AV prompts) are in
`docs/WINDOWS_SMOKE_TEST.md`.

### Building the Windows executable

Must run on real Windows (PyInstaller doesn't cross-compile) — see
`docs/WINDOWS_BUILD.md` for the full manual walkthrough, or run
`scripts/thinclient/build_windows.ps1` for the one-shot version (installs `uv` if missing, syncs
deps, runs the test suite, builds a version-named `dist/journeycapture-<version>.exe`
via PyInstaller, copies `examples/config.example.json` → `dist/config.json` if missing).
Same constraint and one-shot pattern for the macOS agent — must run on a real Mac,
see `docs/MACOS_BUILD.md` and `scripts/mac_thinclient/build_mac.sh`.

### `journeycapture_broker` — routes MCP requests to the right machine

Lives in `src/journeycapture_broker/`, a third top-level package alongside the other
two (`[tool.uv.build-backend] module-name` lists all three). Its dependencies
(`fastapi`, `uvicorn`, `websockets`) sit under a `broker` optional-dependency group —
controller-side only, same reasoning as the `mcp` group.

- **`config.py`** — `load_settings(config_path=None)`: `api_key` (the MCP-facing
  secret) and `machines` (a `machine_id: api_key` map — each thin client's own key,
  checked at websocket handshake time) are required; `host`/`http_port` (default
  `0.0.0.0:8600`) and `ws_host`/`ws_port` (default `0.0.0.0:8601`) are separate
  listen addresses for the two different protocols this process serves.
  `machine_profiles`/`mcp_profile` (both default `{}`) are validated at load
  time — every `machine_profiles` key must exist in `machines`, `screenshot`
  sub-objects are validated by reusing `journeycapture_windows_thinclient.config.ScreenshotConfig`
  (same cross-package-reuse precedent as `schemas.py` below), `log_level` against
  `logging.getLevelNamesMapping()`, and both objects reject unknown keys — same
  fail-fast-on-typo philosophy as everything else in this repo. See "Broker-pushed
  config" below.
- **`registry.py`** — `ConnectionRegistry` is the actual routing logic: maps
  `machine_id` → live websocket connection, and correlates each outgoing
  `{"id", "method", "params"}` message with the HTTP call `await`-ing a response, by
  a `uuid4` id, via a pending `asyncio.Future` per in-flight request (with a
  `request_timeout` — default 15s — so a disconnected/hung machine fails the HTTP
  call with a clear timeout instead of hanging it forever). Screenshots get special
  handling: the JSON response (metadata only) doesn't resolve the future by itself —
  the registry holds it open until the very next frame on that connection, expected
  to be binary, arrives with the actual image bytes. This depends on connections
  being processed sequentially on both ends (`ws_client.py` handles one broker
  request fully — including sending both screenshot frames — before reading the
  next), which is what makes "the next frame belongs to this response" true without
  needing to embed a request id inside binary data.
- **`ws_server.py`** — the `websockets`-based server thin clients connect to.
  Rejects (closes the connection) a `machine_id` it doesn't know or a key that
  doesn't match, using `secrets.compare_digest` — same fail-closed, constant-time-
  comparison philosophy the retired thin-client REST auth used to have.
- **`http_api.py`** — FastAPI app exposing `/machines` (list connected ids) and
  `/machines/{id}/...` mirroring the thin client's original route shapes exactly,
  reusing `journeycapture_windows_thinclient.schemas` directly for request/response models
  rather than duplicating them (same repo, no reason not to). Translates
  `ConnectionRegistry`'s `MachineNotConnected`/`MachineTimeout`/`MachineError`
  exceptions into `404`/`504`/`400` respectively.
- **`__init__.main()`** — runs the FastAPI/uvicorn HTTP server and the `websockets`
  server concurrently in one process (`asyncio.gather`) — one broker, two listeners,
  one shared `ConnectionRegistry`.

Full setup/API/testing details: `docs/BROKER.md`.

### Broker-pushed config

`machine_profiles` (`machine_id: {"screenshot": {...}, "log_level": ...}`) and
`mcp_profile` (`{"save_screenshots": ..., "screenshot_dir": ..., "max_saved_screenshots": ..., "timeout": ...}`)
in the broker's own config are the *recommended* place for this operational
config to live — centralized on the broker rather than hand-edited per machine.
Both default to `{}`, and each client's own local `config.json` is the fallback
for whatever a profile doesn't mention (or when the broker's briefly
unreachable), not the primary source. Only fields with no bearing on *finding or
trusting* the broker are eligible for this — everything needed to reach the
broker in the first place (`broker_host`/`broker_port`/`broker_tls`/
`broker_cert_fingerprint`, a thin client's own `machine_id`/`api_key`) has to
stay local, since there's no connection to push it over yet. Two more fields stay
local by choice, not necessity: `mcp_host`/`mcp_port` (letting the broker move
the MCP server's bind address would undermine the loopback-only default that's
the only thing standing between "this machine only" and unauthenticated remote
control) and a thin client's `log_file` (would need live-swapping the active
`RotatingFileHandler`'s target file, real complexity nobody's asked for yet).

`ws_server.py`'s handler always sends one more frame right after the handshake
ack — `{"type": "config", **machine_profiles.get(machine_id, {})}`, `{"type":
"config"}` if that machine has no profile — which `ws_client.py`'s
`_apply_config_push` applies on top of the local `config.json` on every successful
(re)connect. This is now a fixed part of the wire protocol (see
`docs/THIN_AGENT_PLAYBOOK.md`'s §1), not optional — any new agent has to expect and
consume this frame. The broker's `GET /mcp-config` route serves the same idea to
the MCP server, fetched once at startup (`journeycapture_mcp/__init__.main()`,
tolerant of the broker being briefly unreachable — logs a warning and falls back
to local settings rather than refusing to start) and merged over `Settings` via
`dataclasses.replace()` before `build_server()` is called. A pushed `timeout`
needs one extra step beyond that merge: `client.set_timeout(...)`, since
`client`'s `httpx.AsyncClient` was already built with the old value at
construction time and `httpx.Client.timeout` has a public setter (no need to
rebuild the client, or redo a TLS-pinning handshake).

Full design and the exact JSON shapes: `docs/BROKER.md`'s "Broker-pushed config"
section.

### `journeycapture_mcp` — the MCP server

Lives in `src/journeycapture_mcp/`. Its dependencies (`mcp`, `httpx`) sit under the
`mcp` optional-dependency group.

- **`config.py`** — `load_settings(config_path=None)`: with a path (the `--config`
  CLI flag), reads a JSON file; without one, falls back to
  `JOURNEYCAPTURE_BROKER_HOST`/`_PORT`/`_SCHEME`/`_API_KEY`/`_MCP_HOST`/`_MCP_PORT`/
  `_MCP_SAVE_SCREENSHOTS`/`_MCP_SCREENSHOT_DIR`/`_MCP_MAX_SAVED_SCREENSHOTS` env
  vars. It's the broker's address (`broker_host`/`broker_port`/`broker_scheme`/
  `broker_api_key` — one broker, potentially many machines behind it) plus where
  this server itself listens (`mcp_host`/`mcp_port`, default `127.0.0.1:8000` —
  deliberately separate names to avoid confusing "the broker" with "this server")
  plus the (off-by-default) local screenshot-saving toggle (`save_screenshots`/
  `screenshot_dir`/`max_saved_screenshots`, the last defaulting to 100 — a count-based
  cap, pruned oldest-first after each save, matching how `journeycapture.log`/
  `journeycapture-mcp.log` both rotate rather than growing forever; 0 or negative
  disables pruning). Fails fast with a clear stderr message if broker_host/
  broker_api_key are missing either way (mirrors
  `journeycapture_windows_thinclient.config.load_config`'s fail-fast philosophy).
- **`client.py`** — `JourneyCaptureClient`, an async `httpx`-based wrapper around the
  broker's HTTP API. Every method takes a `machine` id as its first argument and
  builds a `/machines/{machine}/...` path. Raises `JourneyCaptureError` on non-2xx
  responses, with the response body included (that's where the broker's
  `404`/`504`/`400` detail messages live). `get_mcp_config()` is the one exception
  to the `/machines/{machine}/...` shape (it hits the broker's top-level
  `/mcp-config`) and the one method that tolerates a `404` by returning `{}` instead
  of raising — see "Broker-pushed config" above.
- **`server.py`** — `build_server(client, settings)` builds an `MCPServer`
  (`mcp.server.mcpserver.MCPServer` — this SDK's current name for what used to be
  called `FastMCP`) and registers one `@server.tool()` per broker endpoint, plus
  `list_machines` (new — lets an LLM discover what's connected before picking a
  target). Every tool except `list_machines` takes a required `machine` parameter,
  passed straight through to `client`. `take_screenshot` returns
  `mcp.server.mcpserver.Image` (base64-encoded image content), not raw bytes or a
  file path — the one endpoint needing translation rather than a passthrough — and,
  when `settings.save_screenshots` is on, also writes a timestamped copy to
  `settings.screenshot_dir` and prunes the oldest files beyond
  `settings.max_saved_screenshots` (a save/prune failure logs a warning rather than
  failing the tool call — it's a debugging convenience, not core functionality).
  Parameterized by `client`/`settings` (rather than importing module-level
  singletons) so tests can pass an `AsyncMock`/a throwaway `Settings` and call
  `server.call_tool(name, args)` directly, in-process, with no real network or HTTP
  transport involved. Every tool logs its name, machine, and arguments before calling
  `client` (same privacy carve-out as the thin client: `type_text` logs the character
  count, never the text) — this is what to check if something looks wrong, e.g.
  whether a double-click actually arrived as one `clicks=2` call or as two separate
  single clicks too far apart to register as a real double-click on the target
  machine.
- **`logging_setup.py`** — same console + rotating-file-handler pattern as
  `journeycapture_windows_thinclient.logging_setup`, writing to `journeycapture-mcp.log` next
  to wherever the command was run from.
- **`__init__.main()`** — the `journeycapture-mcp` console-script entry point: loads
  config, builds the client and server, calls
  `server.run(transport="streamable-http", host=settings.mcp_host, port=settings.mcp_port)`.
  Streamable HTTP, not stdio — the user runs this themselves in a terminal and it
  keeps listening (loopback-only by default) rather than being spawned/owned by the
  MCP client's own lifecycle. This server has no auth of its own at the MCP/HTTP
  layer, so the loopback default is the only thing standing between "just this
  machine" and "unauthenticated remote control of every machine behind the broker"
  if `JOURNEYCAPTURE_MCP_HOST` were ever pointed at a non-loopback address.

Full setup/config/testing details: `docs/MCP_SERVER.md`.
