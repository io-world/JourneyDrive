# Dependencies

What each library in `pyproject.toml` is actually used for. Versions are lower
bounds (`>=`); see `uv.lock` for exact resolved versions.

## Thin client (`journeycapture_windows_thinclient`) — base `dependencies`

Runs on the Windows box. `scripts/thinclient/build_windows.ps1`'s plain `uv sync` installs only
this group — nothing below it.

| Library | Used for |
|---|---|
| `websockets` | `ws_client.py` connects *out* to the broker and speaks its request/response protocol — this replaced the thin client's own listening HTTP server. |
| `pydantic` | A direct dependency now (previously only transitive via `fastapi`, which moved to the `broker` extra) — `schemas.py`'s request/response models and `config.py`'s `Config` validation both subclass `pydantic.BaseModel`, and `ws_client.py` validates incoming broker messages against those same schemas. |
| `mss` | Cross-platform screenshot capture — grabs raw monitor pixels (`capture.py`'s `list_monitors`/`take_screenshot`). |
| `pillow` | Encodes `mss`'s raw pixel data to JPEG (`capture.py`); PNG output uses `mss`'s own encoder instead. Also used by `journeycapture_mcp.server`'s `preview_click` tool to draw a crosshair marker on a screenshot — a base dependency already, so no separate addition to the `mcp` extra was needed. |
| `pynput` | Synthesizes mouse/keyboard input at the OS level (`input_control.py`) — this is what actually moves the cursor, clicks, and types. |

## Broker (`journeycapture_broker`) — `broker` optional-dependency group

Controller-side only. Install with `uv sync --extra broker`; see `docs/BROKER.md`.

| Library | Used for |
|---|---|
| `fastapi` | The MCP-facing HTTP API (`http_api.py`) — moved here from the thin client's base dependencies now that the thin client no longer runs an HTTP server itself. |
| `uvicorn[standard]` | The ASGI server that runs the broker's FastAPI app (`__init__.py`'s `uvicorn.Server(...).serve()`, run concurrently with the websocket server). |
| `websockets` | The server side of the thin-client-facing websocket (`ws_server.py`) — same package as the thin client's client side, different API surface (`websockets.asyncio.server.serve` vs. `websockets.asyncio.client.connect`). |

## MCP server (`journeycapture_mcp`) — `mcp` optional-dependency group

Controller-side only. Install with `uv sync --extra mcp`; see `docs/MCP_SERVER.md`.

| Library | Used for |
|---|---|
| `mcp` | The official MCP Python SDK — `MCPServer`/`Image` (`server.py`) build the actual MCP tool server and its streamable-HTTP transport. |
| `httpx` | `client.py`'s `JourneyCaptureClient` — an async HTTP client that calls the broker's HTTP API over the network. |

## Dev tooling — `dependency-groups.dev`

Not needed to run any of the three components, only to develop/test/build them.
Installed by default whenever `uv sync` runs (with or without `--extra mcp`/`--extra broker`).

| Library | Used for |
|---|---|
| `pytest` | The test suite (`tests/`). |
| `pytest-asyncio` | Enables `async def test_*` functions — needed for `journeycapture_mcp`'s and `journeycapture_broker`'s async tool/client/registry tests. |
| `httpx` | Also here (not just the `mcp` extra) because `fastapi.testclient.TestClient` is built on it (used by `test_broker_http_api.py` too), and the live-testing `scripts/testing/*.py` use it directly. |
| `pyinstaller` | Packages `journeycapture_windows_thinclient` into the standalone Windows `.exe` (`scripts/thinclient/build_windows.ps1`, `docs/WINDOWS_BUILD.md`). |

## Build backend

`uv_build` (declared in `[build-system]`) — not a dependency of the code itself, it's
what `uv` uses to build/install this project. `[tool.uv.build-backend].module-name`
explicitly lists all three packages (`journeycapture_windows_thinclient`,
`journeycapture_mcp`, `journeycapture_broker`) since this repo has three top-level
packages under one `pyproject.toml`.
