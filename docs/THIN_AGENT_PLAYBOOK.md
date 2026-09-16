# Building a new thin agent for this broker

`journeycapture_broker` is already built, already shipped, and doesn't care what OS
or language connects to it — the only contract is the WebSocket protocol below. This
is the recipe for writing a **new agent** — a Linux box, a Mac, a second Windows
machine, or something that isn't even Python — that connects to the *same* broker
your MCP server already talks to, alongside (or instead of) `journeycapture_windows_thinclient`.

The one invariant that makes this whole architecture work: **the agent always
connects out to the broker and stays connected. It never listens on a port.** That's
what lets one broker reach machines behind NAT/firewalls it could never dial into
directly, and it's non-negotiable for any new agent you write — don't build a
variant that has the broker connect to the agent instead.

```
MCP server  --HTTP-->  journeycapture_broker  <--WebSocket--  journeycapture_windows_thinclient (Windows)
                                               <--WebSocket--  your new agent (Linux/Mac/whatever)
```

Both agents register under their own `machine_id` in the broker's `machines` config
(`docs/BROKER.md`) and are addressed independently by every MCP tool's `machine`
parameter — the broker and MCP server need zero changes to support a second, third,
or differently-OS'd agent.

## 1. The wire protocol (exact, not illustrative)

This is what `journeycapture_windows_thinclient/ws_client.py` actually does, and what any
new agent must reproduce byte-for-byte in its handshake/dispatch shape — the broker
side (`journeycapture_broker/ws_server.py`) is not going to change to accommodate a
different shape.

**Connect and handshake:**

```
ws://<broker_host>:<broker_ws_port>        (ws_port, default 8601 — NOT http_port/8600)
```

Send, as the very first text frame:

```json
{"machine_id": "your-machine-id", "api_key": "your-machines-secret"}
```

`machine_id` must be a key in the broker's `machines` config; `api_key` must match
that entry exactly (compared with a constant-time comparison on the broker's side).
The broker replies with one of:

```json
{"ok": true}
```
```json
{"ok": false, "error": "unknown machine_id or invalid api_key"}
```

A `false` ack means your credentials are wrong, not that the network hiccuped —
don't reconnect-and-retry on this; surface it and stop (see §3's
`RegistrationRejected` pattern).

**Monitor report**: on a `true` ack, before anything else, the agent sends exactly
one more text frame — its own monitor layout, from the same source `screenshot_monitors`
used to provide (real `mss`-detected bounds, not guessed or hardcoded):

```json
{"type": "monitors", "monitors": [{"index": 0, "left": 0, "top": 0, "width": 1920, "height": 1080}, ...]}
```

The broker caches this for the life of the connection and serves it from
`GET /machines` and `GET /machines/{id}/screenshot/monitors` without a live
round trip — a machine's monitor layout is assumed stable for the life of its
connection; if it does change, a reconnect (automatic on a dropped connection,
or a manual restart) re-sends a fresh report. Send this unprompted, right after
reading the ack, before waiting for anything from the broker.

**Config push**: right after that, the broker always sends exactly one more text
frame before anything else, unprompted — this agent's pushed operational config:

```json
{"type": "config", "screenshot": {"format": "png", "quality": 75, "monitor": 0}, "log_level": "INFO"}
```

Both fields are optional and independent — a broker with nothing configured for
this `machine_id` sends `{"type": "config"}` with neither, and a real agent should
treat "field absent" as "don't change whatever I was already using" (local
`config.json` value, or this agent's own built-in default), not as "reset to
nothing." This is what makes a broker with no profile for a given machine
behave identically to a broker with no config-push feature at all — see
`docs/BROKER.md`'s "Broker-pushed config" section for the full design and why only
these two fields are broker-owned (not credentials, not the broker's own address —
an agent needs those *before* it could ever receive a push). Apply the pushed
values fresh on every successful (re)connect, not just the first.

**Request/response loop**, once past the config push: the broker sends text frames shaped
`{"id": "<uuid>", "method": "<name>", "params": {...}}`; the agent must reply on the
same connection with either:

```json
{"id": "<same-id>", "result": {...}}
```
```json
{"id": "<same-id>", "error": {"message": "..."}}
```

**Method table to implement** (`method` → expected `params` → `result`), taken
directly from `journeycapture_windows_thinclient/schemas.py`. Monitor layout is
*not* in this table — it's reported once via the "Monitor report" frame above, not
a live-queryable method; don't implement a `screenshot_monitors` dispatch handler.

| method | params | result |
|---|---|---|
| `health` | `{}` | `{"status": "ok", "version": "<str>"}` |
| `mouse_move` | `{"x": int, "y": int, "relative": bool}` | `{"status": "ok", "x": int, "y": int}` |
| `mouse_click` | `{"button": "left"\|"right"\|"middle", "action": "click"\|"down"\|"up", "clicks": int, "x": int\|null, "y": int\|null}` | `{"status": "ok"}` |
| `mouse_scroll` | `{"dx": int, "dy": int}` (wheel notches, not pixels) | `{"status": "ok"}` |
| `keyboard_type` | `{"text": str}` | `{"status": "ok", "length": int}` |
| `keyboard_key` | `{"keys": [str, ...], "action": "press"\|"release"\|"tap"}` | `{"status": "ok"}` |
| `screenshot` | `{"format": str\|null, "quality": int\|null, "monitor": int\|null}` | *see below — two frames, not one* |

An unrecognized `method` gets `{"id": ..., "error": {"message": "unknown method: '...'"}}`
back — don't let it hang or drop silently.

**`screenshot` is the one method with a different response shape**: send the JSON
result frame first (`{"id": ..., "result": {"content_type": "image/png"}}`), then
immediately send the raw image bytes as a **binary** websocket frame, no base64. The
broker holds that request's HTTP caller open and treats the next frame *on that same
connection* as the image data — which only works because the connection handles one
request fully (both frames) before reading the next. If your agent implementation is
concurrent/multiplexed internally, you must still serialize actual frame-sends per
connection, or the broker will attach the wrong bytes to the wrong response.

**Binary frames arriving *from* the broker never happen** — the broker only ever
sends text frames to the agent. If your websocket library hands you a binary frame,
that's a bug somewhere; `ws_client.py` just discards it defensively rather than
crashing.

**Reconnection**: use whatever auto-reconnect-with-backoff your websocket library
offers (Python's `websockets.connect()` as `async for websocket in connect(...)`
does this natively) — but split *first-ever* connection failures from *dropped after
connecting* failures. Never having reached the broker at all (wrong host/port,
broker not up yet) should give up after a handful of attempts and exit non-zero with
a clear message; a connection that drops after registering successfully should keep
retrying indefinitely, since the broker/network coming back is the expected case a
hub architecture exists to tolerate. See `_INITIAL_CONNECT_ATTEMPTS` and
`BrokerUnreachable` in `ws_client.py` for the reference implementation of this split.

**TLS (optional)**: off by default, on when the broker has `tls_cert_file`/
`tls_key_file` configured (`docs/BROKER.md`'s "TLS setup"). When it's on, the
handshake above happens over `wss://` instead of `ws://`, and *before* sending the
handshake frame, your agent must independently verify the broker's certificate
against a pinned SHA-256 fingerprint given to it out-of-band (the same way
`api_key` already is) — trust-on-first-use, like an SSH `known_hosts` entry, not a
CA chain, since nothing here is ever reached by a DNS name. This is described
protocol-agnostically because it applies regardless of implementation language: fetch
the certificate the broker presents, compare its fingerprint to the configured
value, fail closed (never fall back to an unpinned or plaintext connection) on a
mismatch, and only then proceed to send credentials. See
`journeycapture_windows_thinclient/tls_pinning.py` for a reference implementation
(Python stdlib `ssl`) of exactly this, including why a per-connection verify
callback isn't the right mechanism.

## 2. What to reuse vs. what's genuinely OS-specific

Everything in §1 is fixed regardless of target OS. What actually changes per
platform is the *capability layer* — the equivalent of
`journeycapture_windows_thinclient/capture.py` (screenshots) and `input_control.py`
(mouse/keyboard) — since those are the modules that call into OS-specific APIs
(`mss`/`pynput` on Windows).

Do **not** try to make one capability module conditionally branch on OS at runtime
inside the same file as the dispatch loop — write a new agent package (or a new
capture/input_control pair inside one, gated by platform at import time) that
implements the same function signatures the dispatch table calls
(`take_screenshot`, `list_monitors`, `move_mouse`, `click_mouse`, `scroll_mouse`,
`type_text`, `send_keys`), and reuse the dispatch/handshake/reconnect logic in §1
as-is — that part has zero OS-specific code in it today and shouldn't grow any.

**Linux options:**
- X11: `python-xlib` or shelling out to `xdotool` for input, `mss`
  (already X11-compatible) or a direct XGetImage call for capture.
- Wayland: meaningfully harder — most compositors deliberately restrict synthetic
  input and screen capture for security reasons unless the compositor exposes a
  portal API (`xdg-desktop-portal`) or you use a compositor-specific protocol
  (`wlroots`' virtual-pointer/virtual-keyboard protocols, GNOME's own D-Bus
  interfaces). Confirm which compositor your target actually runs before assuming
  either approach works — treat this as a real design constraint to check early, not
  a detail to discover mid-implementation.

**macOS options:**
- `Quartz`/`CoreGraphics` (via `pyobjc`) for both capture (`CGWindowListCreateImage`
  or `CGDisplayCreateImage`) and synthetic input (`CGEventCreateMouseEvent`,
  `CGEventCreateKeyboardEvent`). `pynput` also works on macOS and may be the least
  code change from the Windows implementation.
- macOS will prompt for (and can silently block without prompting, depending on
  version) **Accessibility** and **Screen Recording** permission grants for
  whatever process performs input/capture — this needs a one-time manual grant in
  System Settings and cannot be scripted around. Document this as a manual setup
  step, the same way `docs/WINDOWS_SMOKE_TEST.md` documents Windows-only manual
  checks that can't be automated from another OS.

Whatever you pick, the monitor layout reported in the handshake-time `"monitors"`
frame (§1) — real `width`/`height`/`left`/`top` per monitor — must be genuinely
accurate — every downstream fractional-coordinate (`fx`/`fy`) calculation in
`journeycapture_mcp` depends on this being correct, and there is no cross-check
that catches it being subtly wrong (see the resolution-assumption pitfall in §4
of this doc, and `CLAUDE.md`'s "Never assume the screen resolution" section for
the real incident this protects against).

## 3. Config and startup discipline to carry over unchanged

These aren't OS-specific and there's no reason a new agent should relax any of them:

- Fail fast with a specific, actionable error message on a missing/invalid config
  field — see `journeycapture_windows_thinclient/config.py`'s `Config` model
  (`extra="forbid"`, `api_key` minimum length 16, `x`/`y` must be given together)
  for the exact validation shape to match; a new agent's config should require the
  same fields (`broker_host`, `broker_port`, `machine_id`, `api_key`) so
  `examples/config.example.json` stays a valid template for it too.
- `RegistrationRejected` (bad credentials) and `BrokerUnreachable` (never connected)
  are distinct failure modes with distinct exit behavior — both should exit
  non-zero with a clear stderr message, never exit 0 the way a normal shutdown
  would, since that's indistinguishable to anything watching the process.
- Log every dispatched command (method + enough params to debug, via the same
  privacy carve-out `keyboard_type` uses: log the character *count*, never the
  text) through the same rotating-file-plus-console setup style as
  `journeycapture_windows_thinclient/logging_setup.py`. Don't log `health` — it's a
  liveness ping, not a command.
- `click_mouse`/`send_keys`'s `action="down"`/`"press"` needs the same
  auto-release-after-N-seconds safety net `input_control.py` already has for
  Windows (`_AUTO_RELEASE_SECONDS`) — a dropped "up"/"release" request must not
  leave a button or key stuck held on the new agent's machine either.
- Pace `type_text` (send one character at a time with a small delay) rather than
  one bulk platform `.type()`/equivalent call, until you've specifically verified
  the new platform's input API doesn't drop/corrupt characters under a fast bulk
  call the way the Windows `pynput` path was found to. Don't assume the Windows
  finding transfers, but don't assume it doesn't either — verify against the real
  new target before trusting an unpaced call in production.

## 4. Pitfalls worth re-reading before you start

The full list (screen-resolution assumptions, fx/fy fractional coordinates, why UIA-
style introspection was rejected in favor of mimicking a human operator, auto-release
semantics, the two auth secrets, why `type_text` is paced) is already written up in
`CLAUDE.md` — read it before writing the new agent's capability layer, since every
one of those was a real bug or a real design decision made for reasons that apply
equally to a Linux or Mac agent, not just the Windows one.

## 5. Testing a new agent

- Everything above the OS-specific leaf calls (dispatch, handshake, reconnect
  logic, config validation) can and should be unit tested with the OS-specific
  functions mocked out, on any dev machine — mirror
  `tests/test_ws_client.py`/`tests/test_input_control.py`'s structure.
- Add the new agent to `scripts/testing/live_check.py`-style smoke testing: point
  `--broker-host`/`--machine` at the new agent's `machine_id` and run the existing
  script unchanged — it already only talks to the broker's HTTP API, so it works
  against any agent behind it with zero script changes.
- Anything that only manifests on the real OS (permission prompts, Wayland-vs-X11
  behavior, timing/dropped-character behavior under real load) needs a manual
  checklist doc for that OS, the same role `docs/WINDOWS_SMOKE_TEST.md` plays for
  Windows — write it down rather than relying on memory of what was manually
  verified once.

## 6. Starter prompt for an LLM

> I want to write a new agent that connects to this repo's existing
> `journeycapture_broker`, targeting `<Linux/macOS/other>`, following
> `docs/THIN_AGENT_PLAYBOOK.md`. Reuse the wire protocol in its §1 exactly — don't
> change the broker or the MCP server. Implement `capture`/`input_control`
> equivalents for `<target OS>` per §2, wire them into a dispatch loop that
> reproduces `journeycapture_windows_thinclient/ws_client.py`'s handshake/reconnect/
> dispatch logic, and carry over every item in §3 unchanged. Read `CLAUDE.md` for
> the full pitfall list in §4 before writing the capability layer. Stop after the
> capability layer (before wiring up the websocket loop) so I can review it first.
