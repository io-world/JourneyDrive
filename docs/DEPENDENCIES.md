# Dependencies

What each library in `pyproject.toml` is actually used for. Versions are lower
bounds (`>=`); see `uv.lock` for exact resolved versions.

## Thin clients (`journeydrive_windows_thinclient`, `journeydrive_mac_thinclient`) — base `dependencies`

One runs on the Windows box, the other on a Mac — see `docs/THIN_AGENT_PLAYBOOK.md`
for why they're separate packages with near-identical internals rather than one
package branching on `sys.platform`. Both `scripts/windows_thinclient/build_windows.ps1`
and `scripts/mac_thinclient/build_mac.sh`'s plain `uv sync` installs only this
group — nothing below it — since `mss`/`pynput` are cross-platform libraries and
neither agent needs the `mcp`/`broker` extras.

| Library | Used for |
|---|---|
| `websockets` | `ws_client.py` connects *out* to the broker and speaks its request/response protocol — this replaced the thin client's own listening HTTP server. |
| `pydantic` | A direct dependency now (previously only transitive via `fastapi`, which moved to the `broker` extra) — `schemas.py`'s request/response models and `config.py`'s `Config` validation both subclass `pydantic.BaseModel`, and `ws_client.py` validates incoming broker messages against those same schemas. |
| `mss` | Cross-platform screenshot capture — grabs raw monitor pixels (`capture.py`'s `list_monitors`/`take_screenshot`). Verified live on a real Mac that its macOS (Quartz) backend already reports monitor bounds and captures pixel data in real display pixels, matching its Windows behavior with no Retina-scaling correction needed. |
| `pillow` | Encodes `mss`'s raw pixel data to JPEG (`capture.py`); PNG output uses `mss`'s own encoder instead. Also used by `journeydrive_mcp.server`'s `preview_click` tool (draws a crosshair marker on a screenshot) and `take_screenshot`'s `fx1`/`fy1`/`fx2`/`fy2` region crop — a base dependency already, so no separate addition to the `mcp` extra was needed for either. |
| `pynput` | Synthesizes mouse/keyboard input at the OS level (`input_control.py`) — this is what actually moves the cursor, clicks, and types. On macOS this additionally requires a one-time manual Accessibility permission grant (see `docs/MACOS_BUILD.md`) that can't be scripted around. |
| `pyperclip` | Reads/writes the remote clipboard (`input_control.py`'s `get_clipboard`/`set_clipboard`), exposed as `journeydrive_mcp`'s `get_clipboard`/`set_clipboard` tools. |

`journeydrive_mac_thinclient.ws_client` imports `tls_pinning` directly from
`journeydrive_windows_thinclient` rather than duplicating it — that module is
pure-stdlib TLS/fingerprint logic with no OS-specific code, and `journeydrive_mcp`
already reuses it the same way, so a future fix only needs to land in one place.

## Broker (`journeydrive_broker`) — `broker` optional-dependency group

Controller-side only. Install with `uv sync --extra broker`; see `docs/BROKER.md`.

| Library | Used for |
|---|---|
| `fastapi` | The MCP-facing HTTP API (`http_api.py`) — moved here from the thin client's base dependencies now that the thin client no longer runs an HTTP server itself. |
| `uvicorn[standard]` | The ASGI server that runs the broker's FastAPI app (`__init__.py`'s `uvicorn.Server(...).serve()`, run concurrently with the websocket server). |
| `websockets` | The server side of the thin-client-facing websocket (`ws_server.py`) — same package as the thin client's client side, different API surface (`websockets.asyncio.server.serve` vs. `websockets.asyncio.client.connect`). |

## MCP server (`journeydrive_mcp`) — `mcp` optional-dependency group

Controller-side only. Install with `uv sync --extra mcp`; see `docs/MCP_SERVER.md`.

| Library | Used for |
|---|---|
| `mcp` | The official MCP Python SDK — `MCPServer`/`Image` (`server.py`) build the actual MCP tool server and its streamable-HTTP transport. |
| `httpx` | `client.py`'s `JourneyDriveClient` — an async HTTP client that calls the broker's HTTP API over the network. |

## Automation (`journeydrive_automation`) — `automation` optional-dependency group

Controller-side only. Install with `uv sync --extra automation`; see `docs/AUTOMATION.md`.

| Library | Used for |
|---|---|
| `langgraph` | The run's state machine (`graph.py`): nodes for observe/agent/act/guard/verify and the conditional edges between them. Only the orchestration — no LangChain model wrappers. |
| `anthropic` | The official Anthropic SDK — `llm.py`'s agent and verifier calls (`AsyncAnthropic().beta.messages.create`, for refusal fallbacks and context editing). |
| `python-dotenv` | Loads `ANTHROPIC_API_KEY` from the gitignored repo-root `.env` at startup (`__init__._run`); a variable already set in the shell wins. |
| `journeydrive[mcp]` | Pulls in the `mcp` extra: the automation reuses `journeydrive_mcp.client`/`config`/`geometry`, and importing anything from `journeydrive_mcp` runs its `__init__`, which imports the MCP SDK. |

## Dev tooling — `dependency-groups.dev`

Not needed to run any of the three components, only to develop/test/build them.
Installed by default whenever `uv sync` runs (with or without `--extra mcp`/`--extra broker`).

| Library | Used for |
|---|---|
| `pytest` | The test suite (`tests/`). |
| `pytest-asyncio` | Enables `async def test_*` functions — needed for `journeydrive_mcp`'s and `journeydrive_broker`'s async tool/client/registry tests. |
| `httpx` | Also here (not just the `mcp` extra) because `fastapi.testclient.TestClient` is built on it (used by `test_broker_http_api.py` too), and the live-testing `scripts/testing/*.py` use it directly. |
| `pyinstaller` | Packages `journeydrive_windows_thinclient` into the standalone Windows `.exe` (`scripts/windows_thinclient/build_windows.ps1`, `docs/WINDOWS_BUILD.md`) and `journeydrive_mac_thinclient` into the standalone macOS binary (`scripts/mac_thinclient/build_mac.sh`, `docs/MACOS_BUILD.md`). |

## Build backend

`uv_build` (declared in `[build-system]`) — not a dependency of the code itself, it's
what `uv` uses to build/install this project. `[tool.uv.build-backend].module-name`
explicitly lists all four packages (`journeydrive_windows_thinclient`,
`journeydrive_mac_thinclient`, `journeydrive_mcp`, `journeydrive_broker`)
since this repo has four top-level packages under one `pyproject.toml`.
