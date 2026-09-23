# JourneyDrive

Lets an MCP-aware AI assistant see and drive a real Windows or Mac desktop — take
screenshots, move the mouse, click, and type — the same way a human would, so it can
carry out on-screen tasks in apps that have no API of their own. Remote-controls
desktops (mouse, keyboard, screenshots) through four components:

```
MCP client  --stdio/HTTP-->  MCP server  --HTTP-->  broker  <--WebSocket--  Windows thin client(s)
                                                            <--WebSocket--  macOS thin client(s)
```

- **Windows thin client** (`journeydrive-win`) — runs on each Windows box, drives the
  mouse/keyboard/screenshots there. Connects *out* to the broker; doesn't accept
  inbound connections.
- **macOS thin client** (`journeydrive-mac`) — the same role for a Mac. Same wire
  protocol, same broker, addressed independently by its own `machine_id`.
- **Broker** (`journeydrive-broker`) — routes requests to whichever machine they're
  addressed to, regardless of which OS agent is behind that id. One broker can relay
  to many thin clients at once.
- **MCP server** (`journeydrive-mcp`) — exposes the broker's API as MCP tools for
  an MCP-aware assistant.

Optionally, **automation** (`journeydrive-automation`) runs a JSON script of goals
on a machine unattended, talking to the broker directly — see [Automation](#automation).

Each runs on its own machine (thin clients: the Windows/Mac box; broker and MCP
server: typically the controller machine, though nothing requires that).

Set these up in order: the broker first (everything else connects to it), then the
thin client(s), then the MCP server.

## Broker

Sits between the MCP server and every thin client.

**First-time setup** (once):

```
uv sync --extra broker
mkdir -p scripts/broker
cp examples/config.broker.example.json scripts/broker/broker_config.json
```

Then edit `scripts/broker/broker_config.json`:

- `api_key` — a secret the MCP server will use to talk to the broker. Make one up,
  or generate one (see [Generating API keys](#generating-api-keys) below).
- `machines` — one `"machine_id": "api_key"` entry per thin client. The
  `machine_id` is any name you choose (e.g. `"office-pc"`), and each machine gets
  its own separate secret. The same pair goes into that thin client's own config.

**Run it:**

```
uv run journeydrive-broker --config scripts/broker/broker_config.json
```

See [docs/BROKER.md](docs/BROKER.md) for the HTTP API and how routing works.

### Generating API keys

Any long random string works. To generate one:

```
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Run it once for the broker's `api_key` and once per machine. You'll end up with
the same keys in two places each:

| Key | Set in the broker's config | Also set in |
|---|---|---|
| Broker key | `api_key` | the MCP server's `broker_api_key` |
| Each machine's key | `machines` → `"<machine_id>": "<key>"` | that thin client's `machine_id` + `api_key` |

### TLS (optional)

Off by default — plaintext is fine as long as every leg stays on a trusted network.
Turn it on when a thin client or the MCP server needs to reach the broker across a
network you don't fully trust:

```
openssl req -x509 -newkey rsa:4096 -sha256 -days 3650 -nodes \
  -keyout scripts/broker/broker_key.pem -out scripts/broker/broker_cert.pem \
  -subj "/CN=journeydrive-broker" \
  -addext "subjectAltName=IP:<broker's real LAN IP>"
openssl x509 -in scripts/broker/broker_cert.pem -noout -fingerprint -sha256
```

Set `tls_cert_file`/`tls_key_file` in `scripts/broker/broker_config.json` to
`"scripts/broker/broker_cert.pem"`/`"scripts/broker/broker_key.pem"` (paths are
relative to the repo root, where you run the broker from), then
give every client (thin client's `broker_tls`+`broker_cert_fingerprint`, MCP
server's `broker_scheme: "https"`+`broker_cert_fingerprint`, or
`scripts/testing/*.py --broker-scheme https --broker-cert-fingerprint ...`) the fingerprint
the second command printed. See [docs/BROKER.md](docs/BROKER.md)'s "TLS setup"
section for the full explanation (why a self-signed cert + pinned fingerprint
instead of a CA, and what to do if the cert is ever regenerated).

### Broker-pushed config

The broker is the recommended place to set operational config centrally, instead
of hand-editing every thin client / the MCP server's own local copy — the more
that lives in one place, the less there is to keep in sync across machines. Add
`machine_profiles` (per-`machine_id`: `screenshot`/`log_level`) and/or
`mcp_profile` (`save_screenshots`/`screenshot_dir`/`max_saved_screenshots`/
`timeout`) to `scripts/broker/broker_config.json` and it's pushed to clients automatically (thin
clients on every connect, the MCP server once at startup). A client's own local
config is the fallback, not the primary source: a field a profile doesn't mention
keeps whatever the client's local value already was, and a broker that's briefly
unreachable at startup just leaves the MCP server on local settings with a
warning rather than refusing to start. Only settings with no bearing on *finding
or trusting* the broker are eligible for this — credentials and the broker's own
address always stay local. See [docs/BROKER.md](docs/BROKER.md)'s "Broker-pushed
config" section for the full design.

## Running a thin client from source

```
uv sync

# Windows
cp examples/config.win_thinclient.example.json config.json
uv run journeydrive-win

# macOS
cp examples/config.mac_thinclient.example.json config.json
uv run journeydrive-mac
```

Before running, edit `config.json`: set `broker_host` to the broker's IP, and
`machine_id`/`api_key` to match that machine's entry in the broker's `machines`
config. Config is loaded from (in order): `--config PATH`, the
`JOURNEYDRIVE_CONFIG` env var, or `config.json` next to the executable/CWD.

## Building the standalone executables

Windows: see [docs/WINDOWS_BUILD.md](docs/WINDOWS_BUILD.md) — must be built on
Windows. Manual verification checklist:
[docs/WINDOWS_SMOKE_TEST.md](docs/WINDOWS_SMOKE_TEST.md).

macOS: see [docs/MACOS_BUILD.md](docs/MACOS_BUILD.md) — must be built on a Mac, and
needs a one-time Accessibility/Screen Recording permission grant (can't be
scripted). Manual verification checklist:
[docs/MACOS_SMOKE_TEST.md](docs/MACOS_SMOKE_TEST.md).

## MCP server

**First-time setup** (once):

```
uv sync --extra mcp
mkdir -p scripts/mcp
cp examples/config.mcp.example.json scripts/mcp/mcp_config.json
```

Then edit `scripts/mcp/mcp_config.json`:

- `broker_api_key` — the broker's own `api_key` from
  `scripts/broker/broker_config.json` (not any machine's key).
- `broker_host` — leave as `127.0.0.1` if the broker runs on this same machine,
  otherwise the broker's IP.

**Run it:**

```
uv run journeydrive-mcp --config scripts/mcp/mcp_config.json
```

Then point your MCP client at `http://127.0.0.1:8000/mcp`. See
[docs/MCP_SERVER.md](docs/MCP_SERVER.md) for configuration options, the `machine`
parameter every tool takes, and wiring it into an MCP client.

## Automation

Runs a JSON script of goals on one machine, start to finish: an agent works out
the clicks and keystrokes from screenshots, and a separate reviewer confirms each
step from the screen before moving on. Needs the broker running and the target
machine's thin client connected; the MCP server doesn't need to be running.

**First-time setup** (once):

```
uv sync --extra automation
```

It also needs a Claude API credential: put `ANTHROPIC_API_KEY=sk-ant-...` in a
`.env` file in the repo root (gitignored), export it in your shell, or run
`ant auth login` once. Unlike the broker and MCP server, the automation calls
Claude itself — there's no MCP client in the loop to supply one. It reuses the MCP server's broker connection config
(`scripts/mcp/mcp_config.json`).

**Run it:**

```
uv run journeydrive-automation examples/automation.example.json --config scripts/mcp/mcp_config.json --machine office-pc
```

Everything it writes goes into `logs/`: a combined `journeydrive-automation.log`,
plus one folder per run with that run's console log, every action, every
screenshot, and the reviewer's verdicts. See [docs/AUTOMATION.md](docs/AUTOMATION.md) for the script
format, how it keeps the model on track, and cost.

## Adding a new agent for another OS

The broker doesn't care what OS or language an agent is written in — only that it
speaks its WebSocket protocol and always connects *out* to the broker, never the
reverse. `journeydrive_mac_thinclient` is a worked example of this: a second,
independent agent added with zero changes to the broker or MCP server. See
[docs/THIN_AGENT_PLAYBOOK.md](docs/THIN_AGENT_PLAYBOOK.md) for the exact wire
protocol and the recipe for writing a Linux/other agent the same way.

## Dependencies

See [docs/DEPENDENCIES.md](docs/DEPENDENCIES.md) for what each library is used for.

## Changelog

See [docs/CHANGELOG.md](docs/CHANGELOG.md) for notable changes over time.
