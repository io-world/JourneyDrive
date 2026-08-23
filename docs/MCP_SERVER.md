# MCP server

`journeycapture-mcp` exposes the broker's HTTP API (`docs/BROKER.md`) as MCP tools, so
an MCP-aware assistant can move the mouse, click, scroll, type, send key chords, and
capture screenshots on any Windows machine the broker has a connection to.

It's controller-side only — it runs wherever your MCP client (Claude Code, Claude
Desktop, etc.) runs. It talks to exactly one broker, but that broker can be relaying
to many machines — every tool takes a `machine` id, resolved by calling
`list_machines` first. This replaced a one-server-per-machine model; see
`docs/CHANGELOG.md` for why.

## Setup

```
uv sync --extra mcp
```

This is a separate step from the plain `uv sync` used for the thin client itself —
the MCP SDK isn't a dependency of `journeycapture.exe` or its Windows build.

## Configuration

Two ways to configure it — a JSON file or environment variables.
`journeycapture_mcp/config.py` uses the file when `--config` is given, otherwise
falls back to the environment variables.

**File** (`--config PATH`):

```
uv run journeycapture-mcp --config scripts/mcp/mcp_config.json
```

Recognized keys: `broker_host`, `broker_api_key` (both required — `broker_api_key`
must match the broker's own `api_key`, not any individual machine's), `broker_port`
(default `8600`), `broker_scheme` (default `"http"`), `broker_cert_fingerprint`
(required when `broker_scheme` is `"https"` — see `docs/BROKER.md`'s "TLS setup"),
`timeout`, `mcp_host` (default `"127.0.0.1"`), `mcp_port` (default `8000`),
`save_screenshots` (default `false`), `screenshot_dir` (default `"screenshots"`),
`max_saved_screenshots` (default `100`).

**Environment variables** (used only when `--config` isn't given):

| Variable | Required | Default | Notes |
|---|---|---|---|
| `JOURNEYCAPTURE_BROKER_HOST` | yes | — | IP or hostname of the broker |
| `JOURNEYCAPTURE_BROKER_API_KEY` | yes | — | must match the broker's own `api_key` |
| `JOURNEYCAPTURE_BROKER_PORT` | no | `8600` | the broker's HTTP port |
| `JOURNEYCAPTURE_BROKER_SCHEME` | no | `http` | `http` or `https`, for reaching the broker |
| `JOURNEYCAPTURE_BROKER_CERT_FINGERPRINT` | if `https` | — | the broker cert's SHA-256 fingerprint, pinned |
| `JOURNEYCAPTURE_MCP_HOST` | no | `127.0.0.1` | where **this** server itself listens |
| `JOURNEYCAPTURE_MCP_PORT` | no | `8000` | where **this** server itself listens |
| `JOURNEYCAPTURE_MCP_SAVE_SCREENSHOTS` | no | off | `1`/`true`/`yes` to enable — see below |
| `JOURNEYCAPTURE_MCP_SCREENSHOT_DIR` | no | `screenshots` | where saved copies go |
| `JOURNEYCAPTURE_MCP_MAX_SAVED_SCREENSHOTS` | no | `100` | 0 or negative disables pruning |

Either way, a missing broker_host/broker_api_key fails fast with a clear message on
stderr rather than starting half-configured.

Don't confuse the two host/port pairs: `broker_host`/`broker_port` (or
`JOURNEYCAPTURE_BROKER_HOST`/`_PORT`) is where the broker is; `mcp_host`/`mcp_port`
(or `JOURNEYCAPTURE_MCP_HOST`/`_PORT`) is where this server binds for its own MCP
clients to connect to.

## Running it

This server speaks MCP over **streamable HTTP**, not stdio — you start it yourself,
separately from your MCP client, and it keeps running until you stop it:

```
uv run journeycapture-mcp --config scripts/mcp/mcp_config.json
```

By default it binds `127.0.0.1:8000` — loopback only, so nothing off this machine can
reach it. Then point your MCP client at it, e.g. a `.mcp.json` entry:

```json
{
  "mcpServers": {
    "journeycapture": {
      "type": "http",
      "url": "http://127.0.0.1:8000/mcp"
    }
  }
}
```

**Security note:** this server holds the broker's real API key internally and has no
authentication of its own at the MCP/HTTP layer — anything that can reach its bound
address can drive every machine connected to the broker through it. The loopback-only
default (`JOURNEYCAPTURE_MCP_HOST=127.0.0.1`) is what keeps this to "processes on this
machine only." Only change it to a non-loopback address if you specifically intend to
expose it to other machines, and understand what that means for every machine behind
the broker, not just one.

Setting `broker_scheme: "https"` (with a matching `broker_cert_fingerprint`)
protects the MCP↔broker leg's traffic in transit — including the `X-API-Key`
header — when the broker has TLS configured (`docs/BROKER.md`'s "TLS setup"). It
doesn't change the caveat above: this server's own listener still has no auth of
its own, TLS or not.

Since VS Code no longer manages this process's lifecycle, restarting it (e.g. after
pulling code changes) is on you — stop it (`Ctrl-C` or `kill`) and run it again.

## Logging

Every tool call is logged (`journeycapture_mcp/server.py`) — tool name, machine, and
arguments — to both the console and a rotating `journeycapture-mcp.log` file next to
wherever you ran the command (`journeycapture_mcp/logging_setup.py`, same
console+file pattern as the thin client's own logging). `type_text` logs the
character count only, never the typed text itself, for the same reason the thin
client does — it could be a password or other sensitive content.

This is the log to check if something looks wrong — e.g. to tell whether a
double-click actually arrived as one `click_mouse(clicks=2)` call or as two separate
single clicks close together (which won't register as a real double-click no matter
how close together they are, since each is a fully separate round trip).

## Tools

One tool per REST endpoint the broker exposes (`journeycapture_mcp/server.py`) —
`list_machines`, `health_check`, `list_monitors`, `take_screenshot`, `preview_click`,
`move_mouse`, `click_mouse`, `scroll_mouse`, `type_text`, `send_keys`. Every tool
except `list_machines` takes a required `machine` id — call `list_machines` first to
see what's connected. Tool descriptions mirror the broker's own OpenAPI descriptions
(coordinate origin, scroll units, valid key names), which in turn mirror the thin
client's original ones — see `CLAUDE.md`'s architecture section.

`take_screenshot` returns MCP image content (base64-encoded), not a file path or raw
bytes — nothing is written to disk on the controller side unless screenshot-saving
(below) is enabled.

### Fractional coordinates (`fx`/`fy`)

`move_mouse` and `click_mouse` both accept `fx`/`fy` (floats, 0.0–1.0) as an
alternative to pixel `x`/`y` — a fraction of the target monitor's width/height
instead of a raw pixel number. `monitor` picks which monitor they're relative to
(defaults to the primary physical monitor, `list_monitors` index 1).

Use `fx`/`fy` whenever the target was identified visually from a `take_screenshot`
result, instead of estimating a pixel coordinate. This isn't just convenience: a
model has no reliable way to know what resolution an image was actually rendered at
by the time it reasons about it (chat UIs and vision pipelines can both resize images
before/while a model looks at them), so a pixel guess is really a guess about an
unknown scale factor wearing the model's confidence as camouflage. A fraction of the
image is correct regardless of what size the model actually perceived it at — the
server does the real-pixel conversion using `list_monitors`' actual dimensions. See
`CLAUDE.md`'s "Never assume the screen resolution" section for the incident that
prompted this.

`x`/`y` and `fx`/`fy` are mutually exclusive per call (pick one), and `fx`/`fy` can't
be combined with `move_mouse`'s `relative=True` — a fraction of the screen isn't a
meaningful concept for a relative offset.

`fx`/`fy` fixes guessing against the *wrong resolution*, but doesn't fix misjudging
*where on the image* a target actually sits — that's a separate, ordinary
eyeballing-accuracy problem, most likely on small targets (tabs, sidebar thumbnails)
or targets next to other clickable elements. There was previously no way to check an
`fx`/`fy` guess before the real click committed — see `preview_click` below.

### Previewing a click before it happens (`preview_click`)

`preview_click(machine, x=None, y=None, fx=None, fy=None, monitor=None)` takes the
exact same coordinate arguments as `click_mouse`, but instead of clicking, it returns
a fresh screenshot with a magenta crosshair drawn at the resolved position — nothing
on the target machine is touched. Use it before `click_mouse` on anything small or
ambiguous: call it, look at where the marker landed relative to the intended target,
and if it's off, adjust `fx`/`fy` and call `preview_click` again rather than guessing
a correction — only call `click_mouse` once the marker actually lines up. The same
`monitor` is used to both resolve the coordinate and capture the screenshot, so the
marker's position is always consistent with the image it's drawn on.

### Saving screenshots locally

Off by default. Set `save_screenshots: true` (config file) or
`JOURNEYCAPTURE_MCP_SAVE_SCREENSHOTS=1` (env var) to also save a timestamped copy of
every `take_screenshot` result to `screenshot_dir` (default `screenshots/`, created if
missing, relative to wherever `journeycapture-mcp` was run from) — useful for
debugging what the model actually saw. Filenames are UTC timestamps down to the
microsecond (`20260819T235959_123456.jpeg`), so concurrent/rapid screenshots don't
collide, but they aren't namespaced by machine — if you're saving screenshots from
more than one machine, they land in the same folder. A save failure (disk full,
permissions) logs a warning but doesn't fail the underlying `take_screenshot` call —
this is a debugging convenience, not core functionality. `screenshots/` is
gitignored; nothing here is ever committed.

Capped at `max_saved_screenshots` (default `100`, config file key or
`JOURNEYCAPTURE_MCP_MAX_SAVED_SCREENSHOTS` env var) — after each save, the oldest
files beyond the cap are pruned automatically, so the folder doesn't grow forever.
Set it to `0` (or negative) to disable pruning and keep everything.

`save_screenshots`/`screenshot_dir`/`max_saved_screenshots`, and `timeout`, are
all recommended to set centrally on the broker (`mcp_profile`) rather than in
this server's own local config — fetched once at startup and overriding the
matching local value for whichever keys the broker actually has configured, with
the local config file only acting as the fallback. See `docs/BROKER.md`'s
"Broker-pushed config" section.

## Testing

`tests/test_mcp_client.py` and `tests/test_mcp_server.py` require the `mcp` extra;
they call `pytest.importorskip("mcp")` so they're skipped (not failed) when it isn't
installed — which is the normal state on the Windows build, since
`scripts/thinclient/build_windows.ps1` only runs a plain `uv sync`. Run
`uv sync --extra mcp && uv run pytest -q` to include them.

No live broker/machine is needed for these tests — `test_mcp_client.py` mocks the
HTTP layer with `httpx.MockTransport`, and `test_mcp_server.py` mocks
`JourneyCaptureClient` itself. For an actual end-to-end check, run a broker and a
thin client (see `docs/BROKER.md`), then call `list_machines` and `health_check`
first (cheapest, no side effects) before anything else.
