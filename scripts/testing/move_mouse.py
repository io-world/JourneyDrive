"""Move the mouse on a machine through a running broker and verify it landed correctly.

Run this from any machine that can reach the broker — it never runs on the broker or
the thin client itself. Moves the cursor through a fixed set of pixel points on the
primary monitor (corners + center, all in absolute screen coordinates where (0, 0) is
the primary monitor's top-left corner — see CLAUDE.md), then does one relative move.

Reads defaults from config.json next to this script (same file used by
get_screenshot.py / send_text.py). Any CLI flag overrides the matching config value:

    uv run python scripts/testing/move_mouse.py
    uv run python scripts/testing/move_mouse.py --broker-host 192.168.1.10 --api-key <key> --machine office-pc
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import httpx

from journeydrive_windows_thinclient.tls_pinning import fetch_pinned_ssl_context

# Edit these to change what gets tested. Points are absolute (x, y) pixel
# coordinates; MARGIN keeps corner points a bit off the literal edge so the
# cursor stays visible rather than hiding behind a taskbar/edge.
MARGIN = 50
PAUSE_SECONDS = 0.75  # gives you time to see each move before the next one fires

CONFIG_PATH = Path(__file__).parent / "config.json"


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    return json.loads(CONFIG_PATH.read_text())


def parse_args() -> argparse.Namespace:
    config = load_config()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--broker-host", default=config.get("broker_host"), help="IP or hostname of the broker"
    )
    parser.add_argument("--broker-port", type=int, default=config.get("broker_port", 8600))
    parser.add_argument("--broker-scheme", default=config.get("broker_scheme", "http"), choices=["http", "https"])
    parser.add_argument("--machine", default=config.get("machine_id"), help="machine_id to target, as configured on the broker")
    parser.add_argument(
        "--api-key",
        default=config.get("broker_api_key") or os.environ.get("JOURNEYDRIVE_BROKER_API_KEY"),
        help=f"The broker's own api_key. Defaults to {CONFIG_PATH.name}'s broker_api_key, then the JOURNEYDRIVE_BROKER_API_KEY env var",
    )
    parser.add_argument(
        "--broker-cert-fingerprint",
        default=config.get("broker_cert_fingerprint") or os.environ.get("JOURNEYDRIVE_BROKER_CERT_FINGERPRINT"),
        help=f"SHA-256 fingerprint of the broker's TLS certificate. Required when --broker-scheme https. "
        f"Defaults to {CONFIG_PATH.name}'s broker_cert_fingerprint, then the JOURNEYDRIVE_BROKER_CERT_FINGERPRINT env var",
    )
    parser.add_argument("--timeout", type=float, default=config.get("timeout", 10.0))
    args = parser.parse_args()

    if not args.broker_host:
        parser.error(f"--broker-host is required (or set 'broker_host' in {CONFIG_PATH.name})")
    if not args.machine:
        parser.error(f"--machine is required (or set 'machine_id' in {CONFIG_PATH.name})")
    if not args.api_key:
        parser.error(
            f"--api-key is required (or set 'broker_api_key' in {CONFIG_PATH.name}, or JOURNEYDRIVE_BROKER_API_KEY)"
        )
    if args.broker_scheme == "https" and not args.broker_cert_fingerprint:
        parser.error(
            f"--broker-cert-fingerprint is required when --broker-scheme https "
            f"(or set 'broker_cert_fingerprint' in {CONFIG_PATH.name}, or JOURNEYDRIVE_BROKER_CERT_FINGERPRINT)"
        )
    return args


def get_primary_monitor(client: httpx.Client, headers: dict) -> dict:
    resp = client.get("/screenshot/monitors", headers=headers)
    resp.raise_for_status()
    monitors = resp.json()
    # monitors[0] from mss is the bounding box of everything; the primary
    # display is monitors[1] when present, else fall back to monitors[0].
    return monitors[1] if len(monitors) > 1 else monitors[0]


def move_and_check(client: httpx.Client, headers: dict, label: str, x: int, y: int) -> bool:
    resp = client.post("/mouse/move", headers=headers, json={"x": x, "y": y})
    if resp.status_code != 200:
        print(f"  FAIL {label}: status={resp.status_code} body={resp.text}")
        return False
    body = resp.json()
    ok = body.get("x") == x and body.get("y") == y
    status = "PASS" if ok else "FAIL"
    print(f"  {status} {label}: requested ({x}, {y}) -> reported ({body.get('x')}, {body.get('y')})")
    return ok


def main() -> int:
    args = parse_args()
    base_url = f"{args.broker_scheme}://{args.broker_host}:{args.broker_port}/machines/{args.machine}"
    headers = {"X-API-Key": args.api_key}
    failures = 0

    verify: bool | object = True
    if args.broker_scheme == "https":
        verify = fetch_pinned_ssl_context(args.broker_host, args.broker_port, args.broker_cert_fingerprint)

    with httpx.Client(base_url=base_url, timeout=args.timeout, verify=verify) as client:
        monitor = get_primary_monitor(client, headers)
        left, top = monitor["left"], monitor["top"]
        right = left + monitor["width"] - 1
        bottom = top + monitor["height"] - 1
        cx = left + monitor["width"] // 2
        cy = top + monitor["height"] // 2
        print(f"Primary monitor: {monitor}\n")

        points = [
            ("top-left", left + MARGIN, top + MARGIN),
            ("top-right", right - MARGIN, top + MARGIN),
            ("center", cx, cy),
            ("bottom-right", right - MARGIN, bottom - MARGIN),
            ("bottom-left", left + MARGIN, bottom - MARGIN),
            ("center (return)", cx, cy),
        ]

        print("Absolute moves:")
        for label, x, y in points:
            if not move_and_check(client, headers, label, x, y):
                failures += 1
            time.sleep(PAUSE_SECONDS)

        print("\nRelative move (+100, +0) from center:")
        resp = client.post("/mouse/move", headers=headers, json={"x": 100, "y": 0, "relative": True})
        if resp.status_code == 200:
            body = resp.json()
            expected = cx + 100
            ok = body.get("x") == expected and body.get("y") == cy
            status = "PASS" if ok else "FAIL"
            print(f"  {status}: expected ({expected}, {cy}) -> reported ({body.get('x')}, {body.get('y')})")
            failures += 0 if ok else 1
        else:
            print(f"  FAIL: status={resp.status_code} body={resp.text}")
            failures += 1

    print(f"\n{'All checks passed' if failures == 0 else f'{failures} check(s) failed'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
