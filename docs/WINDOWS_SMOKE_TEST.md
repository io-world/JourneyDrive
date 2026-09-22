# Manual smoke test (Windows only)

This exercises `journeydrive-win.exe` through a running broker (`docs/BROKER.md`) — the
thin client no longer accepts inbound connections directly, so a broker must be up and
reachable from both the Windows box (outbound) and wherever you run the checks from.
The broker can run anywhere reachable by both sides; it doesn't need to be on the
Windows box itself.

## Automated checks

Once a broker is running and `journeydrive-win.exe` on the Windows box is configured
with that broker's `broker_host`/`broker_port` and a `machine_id`/`api_key` registered
in the broker's own config, run
[`scripts/testing/live_check.py`](../scripts/testing/live_check.py) from any machine
that can reach the broker (this dev machine, a teammate's laptop — not the Windows
box or the broker itself):

```
uv run python scripts/testing/live_check.py --broker-host <broker-ip> --api-key <broker-key> --machine <machine-id>
```

This covers `/health`, wrong-key rejection (401), `/screenshot/monitors`, and
`/screenshot` (saves a `.jpg` locally so you can visually confirm it matches the
desktop). Add `--with-mouse` to also move the remote cursor as a round-trip check, and
`--with-keyboard` to type a short benign string (types into whatever window currently
has focus on the remote machine — use with care). Prints a pass/fail summary and exits
non-zero on any failure.

It does **not** cover a wrong `machine_id`/`api_key` at the thin-client-to-broker
handshake, UIPI/elevated-window behavior, DPI-scaling coordinate correctness, or the
AV flags on first launch — those still need the manual checks below.

## Manual checks

These require a real Windows desktop session and cannot be automated from macOS.

1. Copy `examples/config.win_thinclient.example.json` to `config.json` next to `journeydrive-win.exe`, set
   `broker_host`/`broker_port` to the broker, and set `machine_id`/`api_key` to match
   an entry in the broker's own `machines` config.
2. Launch `journeydrive-win.exe`. Confirm `journeydrive-win.log` shows a successful
   connection and handshake to the broker (not a listening socket — this machine only
   ever connects out).
3. Confirm the machine shows up: `GET /machines` on the broker (with the broker's
   `X-API-Key`) should list this `machine_id`.
4. Deliberately start it with a wrong `machine_id` or `api_key` (not registered on the
   broker) → confirm the broker rejects the handshake and the exe logs a clear error,
   then fix it back before continuing.
5. `GET /machines/<machine-id>/health` with the broker's `X-API-Key` header set
   correctly → `200 {"status": "ok", ...}`.
6. Same request with a wrong/missing broker key → `401`.
7. `GET /machines/<machine-id>/screenshot/monitors` → sane monitor layout for the
   machine.
8. `GET /machines/<machine-id>/screenshot` → capture bytes (raw binary, not
   base64-in-JSON — see `docs/BROKER.md`), open and visually confirm it matches the
   desktop.
9. `POST /machines/<machine-id>/mouse/move` to a known coordinate → confirm the cursor
   lands there (multi-monitor + DPI scaling: coordinates from a screenshot pixel
   should match where the cursor actually goes).
10. `POST /machines/<machine-id>/mouse/click` → confirm a click registers where
    expected.
11. Open Notepad, `POST /machines/<machine-id>/keyboard/type` some text → confirm it
    appears correctly.
12. `POST /machines/<machine-id>/keyboard/key` with `{"keys": ["ctrl", "alt",
    "delete"]}` behaves as an OS-level combo (careful — this may lock the session;
    test last).
13. Confirm UIPI behavior: input to an elevated window is blocked unless
    `journeydrive-win.exe` itself is run as Administrator.
14. Note any antivirus flag on first launch (no Firewall prompt is expected anymore —
    this machine makes only outbound connections to the broker).
15. Stop the broker (or disconnect the network) while `journeydrive-win.exe` is running,
    then restore it → confirm the log shows a reconnect-with-backoff rather than the
    exe exiting, and that `GET /machines` shows it connected again afterward.

Example request from PowerShell (run against the broker, not the Windows box):

```powershell
Invoke-RestMethod -Uri "http://<broker-host>:8600/machines/<machine-id>/health" -Headers @{ "X-API-Key" = "<broker-key>" }
```
