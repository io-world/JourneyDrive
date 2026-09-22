# Broker

`journeydrive-broker` sits between the MCP server and one or more thin clients
(`journeydrive-win.exe` on Windows, `journeydrive-mac` on macOS — see
`docs/THIN_AGENT_PLAYBOOK.md` for adding another OS). It's what makes "one MCP
server, many machines" possible: each thin client connects *out* to the broker over
a websocket — it doesn't accept inbound connections at all — so the broker can reach
any machine that can reach it, regardless of NAT/firewalls on the machine's side. The
broker exposes an HTTP API to the MCP server, namespaced by machine id, mirroring
the thin client's original REST shape — identically regardless of which OS agent is
actually behind a given machine id.

```
MCP server  --HTTP-->  broker  <--WebSocket--  thin client (journeydrive-win.exe, Windows)
                                <--WebSocket--  thin client (journeydrive-mac, macOS)
```

Runs wherever is reachable by both the MCP server and every thin client — today that's
the same controller machine the MCP server runs on, but nothing about the design
requires that.

## Setup

```
uv sync --extra broker
```

Separate step from the plain `uv sync` used for either thin client — same reasoning
as the `mcp` extra: neither thin client build needs to know the broker exists.

## Configuration

JSON file or environment variables, same `load_settings(config_path=None)` pattern as
the MCP server.

**File** (`--config PATH`), see `examples/config.broker.example.json`:

```json
{
  "api_key": "long-random-secret-the-mcp-server-uses",
  "machines": {
    "office-pc": "that-machine's-long-random-secret",
    "home-pc": "a-different-long-random-secret"
  },
  "http_port": 8600,
  "ws_port": 8601
}
```

- `api_key` (required, at least 16 characters) — what the MCP server authenticates
  with, checked against every `/machines/...` HTTP request's `X-API-Key` header. One
  key for the whole broker, not per-machine.
- `machines` (required, at least one entry) — `machine_id: api_key` pairs, each key
  also at least 16 characters. Each thin client's own `config.json` needs a matching
  `machine_id`/`api_key` to be accepted when it connects. A thin client with an
  unknown `machine_id` or wrong key is rejected at the websocket handshake, not
  silently ignored.
- `host`/`http_port` (default `0.0.0.0`/`8600`) — where the MCP-facing HTTP API
  listens.
- `ws_host`/`ws_port` (default `0.0.0.0`/`8601`) — where thin clients connect.
- `request_timeout` (default `15.0` seconds) — how long an HTTP call waits for a
  machine to respond before failing with a `504`.
- `tls_cert_file`/`tls_key_file` (optional, must be given together) — paths to a
  certificate and private key. When both are set, TLS turns on for *both* listeners
  (HTTP and WS share one cert, since they're the same process/machine identity).
  Absent (the default): both listeners stay plaintext. See "TLS setup" below.
- `machine_profiles` (optional, default `{}`) — `machine_id: {...}` pairs, each
  value an object with `screenshot` and/or `log_level`. Every `machine_id` used
  here must also be a key in `machines`. Pushed to that thin client right after its
  websocket handshake succeeds — see "Broker-pushed config" below.
- `mcp_profile` (optional, default `{}`) — an object with `save_screenshots`/
  `screenshot_dir`/`max_saved_screenshots`/`timeout`. Fetched by the MCP server
  once at startup via `GET /mcp-config`. See "Broker-pushed config" below.

**Environment variables**: `JOURNEYDRIVE_BROKER_API_KEY`,
`JOURNEYDRIVE_BROKER_MACHINES` (a JSON object, e.g.
`'{"office-pc": "...", "home-pc": "..."}'`), `JOURNEYDRIVE_BROKER_HOST`,
`JOURNEYDRIVE_BROKER_WS_HOST`, `JOURNEYDRIVE_BROKER_HTTP_PORT`,
`JOURNEYDRIVE_BROKER_WS_PORT`, `JOURNEYDRIVE_BROKER_REQUEST_TIMEOUT`,
`JOURNEYDRIVE_BROKER_TLS_CERT_FILE`, `JOURNEYDRIVE_BROKER_TLS_KEY_FILE`,
`JOURNEYDRIVE_BROKER_MACHINE_PROFILES` (a JSON object keyed by `machine_id`),
`JOURNEYDRIVE_BROKER_MCP_PROFILE` (a JSON object).

## TLS setup

Off by default — everything above still works exactly as before if you skip this.
Turn it on when a thin client or the MCP server needs to cross a network you don't
fully trust (see `CLAUDE.md`'s "Auth model now" section for the full reasoning).

There's no DNS name anywhere in this system (the broker is always reached by a raw
LAN IP), so a public CA like Let's Encrypt isn't an option. Instead: generate one
self-signed certificate for the broker, and give each client (thin client, MCP
server, or a `scripts/testing/*.py` live-test script) that certificate's fingerprint to pin
— the client verifies the broker presents *that exact certificate* on every
connection, the same trust-on-first-use model as an SSH `known_hosts` entry. This is
purely transport security; the existing `api_key`/`machine_id` auth above is
unchanged.

```
openssl req -x509 -newkey rsa:4096 -sha256 -days 3650 -nodes \
  -keyout broker_key.pem -out broker_cert.pem \
  -subj "/CN=journeydrive-broker" \
  -addext "subjectAltName=IP:192.168.1.10"

openssl x509 -in broker_cert.pem -noout -fingerprint -sha256
```

Replace `192.168.1.10` with the broker's real LAN IP. The 10-year validity is
deliberate — there's no automated renewal here, and trust comes entirely from the
pinned fingerprint on each client, not from certificate expiry. `broker_cert.pem`/
`broker_key.pem` should live next to `broker_config.json` (already gitignored — the
private key must never be committed) rather than in `examples/`.

Set `tls_cert_file`/`tls_key_file` in `broker_config.json` to those two paths and
restart the broker. Then, on every client: set `broker_tls: true` (thin client) or
`broker_scheme: "https"` (MCP server / `scripts/testing/*.py --broker-scheme https`), plus
`broker_cert_fingerprint` set to the fingerprint the second `openssl` command
printed (colons optional — the fingerprint isn't a secret, safe to paste anywhere).

If the broker's certificate is ever regenerated, every client's
`broker_cert_fingerprint` needs updating to match — there's no automatic re-trust
step by design, so a mismatch fails closed (a clear error, not a silent fallback to
plaintext or an unverified connection) rather than silently trusting a new,
unverified certificate.

## Broker-pushed config

The broker is the recommended, primary place to set operational config — the more
that lives in one place, the less there is to keep in sync by hand across every
thin client and the MCP server. `machine_profiles`/`mcp_profile` both default to
`{}`, and each client's own local `config.json` is the fallback for whatever the
broker doesn't have an opinion on, not the other way around: if you never
configure a profile, or the broker is briefly unreachable, a client just runs on
its local values, same as before this existed — nothing here trades away that
resilience.

**What can move to the broker, and what can't.** Only settings with no bearing on
*finding or trusting* the broker in the first place are eligible: a thin client's
`screenshot` (format/quality/monitor) and `log_level`; the MCP server's
`save_screenshots`/`screenshot_dir`/`max_saved_screenshots`/`timeout`. Three
things stay local, for two different reasons:

- `broker_host`/`broker_port`/`broker_tls`/`broker_cert_fingerprint` and a thin
  client's own `machine_id`/`api_key` — needed *before* any connection to the
  broker exists, so there's no channel to push them over yet. Not a design
  choice, a hard bootstrap constraint.
- `mcp_host`/`mcp_port` and a thin client's `log_file` — these *could* technically
  be wired the same way, but deliberately weren't. `mcp_host`/`mcp_port` control
  where the MCP server binds for its own MCP client, and the loopback-only
  default is the one thing standing between "this machine only" and
  unauthenticated remote control of every connected machine (see
  `docs/MCP_SERVER.md`'s security note) — letting a broker-side config change
  silently move that bind address is a real risk for close to no benefit.
  `log_file` would require live-swapping the thin client's *active*
  `RotatingFileHandler` to a new target file mid-process (open new, close old),
  meaningfully more engineering than `log_level`'s one-line
  `logging.getLogger().setLevel(...)`, for a setting nobody's asked to
  centralize. Revisit either if a real need shows up — the mechanism (§ below)
  doesn't change, just the allowlist of keys it accepts.

**Thin client**: right after the websocket handshake ack (`{"ok": true}`), the
thin client sends its monitor layout once (`{"type": "monitors", "monitors": [...]}`
— real `mss`-detected bounds), which the broker caches for the life of that
connection (see "Monitor layout is cached, not live-queried" below). The broker
then always sends one more frame — `{"type": "config", ...}` with whatever's in
`machine_profiles[machine_id]`, `{"type": "config"}` if there's no profile for that
machine. The thin client applies it on top of its local config.json (a field the
push doesn't mention keeps whatever the local value already was), on every
successful (re)connect, not just the first — see
`journeydrive_windows_thinclient/ws_client.py`'s `_apply_config_push`. See
`docs/THIN_AGENT_PLAYBOOK.md`'s §1 for the exact wire format, since this is part of
the protocol contract any new agent needs to implement too, not just this repo's
own thin client.

**Monitor layout is cached, not live-queried**: `GET /machines` and
`GET /machines/{id}/screenshot/monitors` both serve from `ConnectionRegistry`'s
per-connection cache (populated by the monitors frame above), not a live round
trip to the thin client — this assumes a machine's monitor layout is stable for
the life of its connection. If it does change (a monitor plugged/unplugged,
resolution changed), the fix is a reconnect — automatic on a dropped connection
via the thin client's existing retry loop, or a manual restart — which sends a
fresh monitors frame and refreshes the cache. This is why the (former)
`screenshot_monitors` live websocket method was removed — nothing calls it
anymore now that both HTTP routes read the cache.

**MCP server**: `journeydrive_mcp/__init__.main()` calls `GET /mcp-config` once
at startup (via `JourneyDriveClient.get_mcp_config()`) and merges whatever keys
come back over the matching local `Settings` fields before building the server.
This fetch failing (broker unreachable, wrong key) logs a warning and falls back to
local-only settings rather than refusing to start — a broker being briefly down at
MCP-server-startup time shouldn't take the whole server offline, especially since
every tool call already handles a down broker the same way, per-call. A pushed
`timeout` needs one extra step beyond the `Settings` merge: `client`'s own
`httpx.AsyncClient` was already built with the *old* timeout at construction time
(it's what made the `/mcp-config` call itself), so `main()` also calls
`client.set_timeout(settings.timeout)` — `httpx.Client.timeout` has a public
setter, so this doesn't require rebuilding the client (and redoing the
TLS-pinning handshake, when TLS is on).

**If you ever need to look up which config a given machine is actually running
with**: it's always the local `config.json` merged with whatever
`machine_profiles`/`mcp_profile` currently says in the broker's own config — there's
no third place it could be.

## Running it

```
uv run journeydrive-broker --config broker_config.json
```

Runs the HTTP API and the websocket server concurrently in one process
(`journeydrive_broker/__init__.py`, `asyncio.gather`). Logs to console and a
rotating `journeydrive-broker.log`, same pattern as the other two components.

## HTTP API

All routes require `X-API-Key` matching the broker's own `api_key`.

- `GET /machines` — list currently-connected machines, each as
  `{"machine_id": ..., "monitors": [...]}` (monitors from the connect-time cache,
  not a live query).
- `GET /mcp-config` — the broker's `mcp_profile`, fetched by the MCP server once at
  startup (see "Broker-pushed config" above). `{}` if nothing's configured.
- `GET /machines/{id}/health`
- `GET /machines/{id}/screenshot/monitors` — same cache as `GET /machines`, scoped
  to one machine; `404` if it isn't connected.
- `GET /machines/{id}/screenshot?format=&quality=&monitor=`
- `POST /machines/{id}/mouse/move`, `/mouse/click`, `/mouse/scroll`
- `POST /machines/{id}/keyboard/type`, `/keyboard/key`
- `GET`/`POST /machines/{id}/clipboard` — read/write the remote clipboard.

FastAPI's interactive docs (`/docs`, `/redoc`, `/openapi.json`) are deliberately
disabled (`create_app`'s `docs_url=None` etc.) rather than left on: those routes are
added outside the app-level `dependencies` mechanism, so they'd bypass the `X-API-Key`
check entirely and leak the full API schema to anyone who can reach the port — which,
since `host` defaults to `0.0.0.0` (unlike the MCP server's loopback-only default),
means anyone on the network by default, not just in an unusual deployment.

Request/response shapes are identical to the thin client's original REST API — the
broker reuses `journeydrive_windows_thinclient.schemas` directly rather than duplicating
them. A request for a machine id that isn't currently connected gets `404`; a machine
that doesn't respond within `request_timeout` gets `504`; an error the thin client
itself reports (e.g. an unknown key name) gets `400`.

## How a request actually gets routed

`journeydrive_broker/registry.py`'s `ConnectionRegistry` is the core of the broker:
it maps `machine_id` → the machine's live websocket connection, and correlates each
outgoing `{"id", "method", "params"}` message with the HTTP call awaiting a response,
by `id` (a `uuid4` generated per call). When the thin client's response arrives on
that connection, the registry resolves the matching `asyncio.Future`, which is what
the HTTP route was `await`-ing.

Screenshots are the one exception to the plain request/response shape: the thin
client sends the JSON metadata response first (`{"content_type": ...}`, no image
data), then a second, **binary** websocket frame with the raw image bytes — no
base64 anywhere. This only works because each connection is processed sequentially
on both ends (the thin client finishes one broker request, including sending both
screenshot frames, before reading the next) — that's what makes "the next frame after
this response" unambiguous without needing to embed a request id inside binary data.

`ConnectionRegistry` is also identity-aware about which connection is current for a
`machine_id`: `unregister`/`handle_text_frame`/`handle_binary_frame` all take the
`websocket` object alongside the `machine_id` and no-op if it isn't the one currently
registered. This matters for a fast reconnect (a thin client's network blips and it
reconnects before the broker's old socket handler notices the first one died) — without
this check, the old connection's delayed disconnect would evict the new, live
connection from the registry out from under it.

## Testing

`tests/test_broker_config.py`, `tests/test_broker_registry.py`, and
`tests/test_broker_http_api.py` all require the `broker` extra (`fastapi`) and start
with `pytest.importorskip("fastapi")`, same convention as the `mcp` extra's tests.
Run `uv sync --extra broker && uv run pytest -q` to include them. No live thin client
needed — `test_broker_registry.py` drives `ConnectionRegistry` directly with a mocked
websocket, and `test_broker_http_api.py` mocks the registry itself under a real
`TestClient`.

For an actual end-to-end check, run a broker and point a real thin client's
`config.json` at it (matching `machine_id`/`api_key`), then hit
`GET /machines/{id}/health` through the broker directly, or use
`scripts/testing/live_check.py` (see `CLAUDE.md`).
