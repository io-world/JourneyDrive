# Manual smoke test (macOS thin client)

This exercises `journeycapture-mac` through a running broker (`docs/BROKER.md`) —
same as the Windows agent, it only ever connects *out* to the broker, so a broker
must be up and reachable from both the Mac (outbound) and wherever you run the
checks from. The broker can run anywhere reachable by both sides; it doesn't need to
be on the Mac itself.

## Automated checks

Once a broker is running and `journeycapture-mac` on the Mac is configured with that
broker's `broker_host`/`broker_port` and a `machine_id`/`api_key` registered in the
broker's own config, run
[`scripts/testing/live_check.py`](../scripts/testing/live_check.py) from any machine
that can reach the broker:

```
uv run python scripts/testing/live_check.py --broker-host <broker-ip> --api-key <broker-key> --machine <machine-id>
```

This covers `/health`, wrong-key rejection (401), `/screenshot/monitors`, and
`/screenshot` (saves a `.jpg` locally so you can visually confirm it matches the
desktop). Add `--with-mouse` to also move the remote cursor as a round-trip check,
and `--with-keyboard` to type a short benign string (types into whatever window
currently has focus on the remote machine — use with care). Prints a pass/fail
summary and exits non-zero on any failure. This script is agent-agnostic — it only
talks to the broker's HTTP API, so it works unchanged whether `<machine-id>` is a
Windows or a Mac agent.

It does **not** cover a wrong `machine_id`/`api_key` at the handshake, the
Accessibility/Screen Recording permission grants, multi-monitor arrangement
correctness, or Gatekeeper/notarization behavior on first launch — those still need
the manual checks below.

## Manual checks

These require a real macOS desktop session and cannot be automated from another OS.

1. Confirm both permission grants from `docs/MACOS_BUILD.md` step 1 (System
   Settings → Privacy & Security → Accessibility, and → Screen Recording) are in
   place for whatever process is running the agent.
2. Copy `examples/config.example.json` to `config.json` next to
   `journeycapture-mac-<version>`, set `broker_host`/`broker_port` to the broker,
   and set `machine_id`/`api_key` to match an entry in the broker's own `machines`
   config.
3. Launch `journeycapture-mac-<version>`. Confirm `journeycapture-mac.log` shows a
   successful connection and handshake to the broker (not a listening socket — this
   machine only ever connects out).
4. Confirm the machine shows up: `GET /machines` on the broker (with the broker's
   `X-API-Key`) should list this `machine_id`.
5. Deliberately start it with a wrong `machine_id` or `api_key` (not registered on
   the broker) → confirm the broker rejects the handshake and the process logs a
   clear error and exits non-zero, then fix it back before continuing.
6. `GET /machines/<machine-id>/health` with the broker's `X-API-Key` header set
   correctly → `200 {"status": "ok", ...}`.
7. Same request with a wrong/missing broker key → `401`.
8. `GET /machines/<machine-id>/screenshot/monitors` → sane monitor layout for the
   machine, including any external displays — check that a multi-monitor
   arrangement (monitors left/right/above the primary) reports `left`/`top` signs
   matching the real physical layout, the same pitfall `CLAUDE.md`'s "Never assume
   the screen resolution" section documents for Windows.
9. `GET /machines/<machine-id>/screenshot` → capture bytes (raw binary, not
   base64-in-JSON — see `docs/BROKER.md`), open and visually confirm it matches the
   desktop, including on a Retina display (width/height should be real pixels, e.g.
   double the "point" resolution shown in System Settings → Displays).
10. `POST /machines/<machine-id>/mouse/move` to a known coordinate → confirm the
    cursor lands there (multi-monitor + Retina scaling: coordinates from a
    screenshot pixel should match where the cursor actually goes — this was
    verified once against a real Retina Mac during development, but re-confirm on
    your actual hardware/display arrangement).
11. `POST /machines/<machine-id>/mouse/click` → confirm a click registers where
    expected.
12. Open TextEdit (or any text field), `POST /machines/<machine-id>/keyboard/type`
    some text → confirm it appears correctly, including any characters that may
    need pynput's macOS-specific keycode mapping (accented characters, etc.).
13. `POST /machines/<machine-id>/keyboard/key` with `{"keys": ["cmd", "q"]}`
    behaves as an OS-level combo against whatever app has focus (careful — this may
    quit an app with unsaved work; test last, and against something disposable).
14. Note any Gatekeeper prompt on first launch of an unsigned/unnotarized binary
    (see `docs/MACOS_BUILD.md`'s "Known friction").
15. Stop the broker (or disconnect the network) while `journeycapture-mac-<version>`
    is running, then restore it → confirm the log shows a reconnect-with-backoff
    rather than the process exiting, and that `GET /machines` shows it connected
    again afterward.

Example request from a terminal (run against the broker, not the Mac):

```bash
curl -s -H "X-API-Key: <broker-key>" "http://<broker-host>:8600/machines/<machine-id>/health"
```
