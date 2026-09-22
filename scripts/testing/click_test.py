"""Test single-click vs double-click behavior on a machine through a running broker.

Run this from any machine that can reach the broker — it never runs on the broker or
the thin client itself. Moves to TARGET_X/TARGET_Y (edit these — default is wherever
your Chrome desktop icon sits), single-clicks it, screenshots, waits past the OS
double-click threshold, deselects, then double-clicks it and screenshots again. Saves
before/after/after-double screenshots locally so you can visually confirm: a single
click should select/highlight the icon without launching anything; a double click
should launch it.

Reads defaults from config.json next to this script (same file used by the other
scripts). Any CLI flag overrides the matching config value:

    uv run python scripts/testing/click_test.py
    uv run python scripts/testing/click_test.py --x 493 --y 131
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

# Edit these to target a different icon/point.
TARGET_X = 493
TARGET_Y = 131

# Windows' default double-click time threshold is ~500ms. Deliberately wait longer
# than that between the single-click test and the double-click test so the two don't
# get coalesced into one another by the OS.
SETTLE_SECONDS = 1.5

CONFIG_PATH = Path(__file__).parent / "config.json"
OUT_DIR = Path(__file__).parent


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
    parser.add_argument("--x", type=int, default=TARGET_X, help="Target X coordinate")
    parser.add_argument("--y", type=int, default=TARGET_Y, help="Target Y coordinate")
    parser.add_argument("--timeout", type=float, default=config.get("timeout", 10.0))
    parser.add_argument(
        "--broker-cert-fingerprint",
        default=config.get("broker_cert_fingerprint") or os.environ.get("JOURNEYDRIVE_BROKER_CERT_FINGERPRINT"),
        help=f"SHA-256 fingerprint of the broker's TLS certificate. Required when --broker-scheme https. "
        f"Defaults to {CONFIG_PATH.name}'s broker_cert_fingerprint, then the JOURNEYDRIVE_BROKER_CERT_FINGERPRINT env var",
    )
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


def save_screenshot(client: httpx.Client, headers: dict, label: str) -> None:
    resp = client.get("/screenshot", headers=headers)
    resp.raise_for_status()
    out_path = OUT_DIR / f"click_test_{label}.jpg"
    out_path.write_bytes(resp.content)
    print(f"  saved {out_path}")


def click(client: httpx.Client, headers: dict, x: int, y: int, clicks: int) -> None:
    resp = client.post(
        "/mouse/click",
        headers=headers,
        json={"button": "left", "action": "click", "clicks": clicks, "x": x, "y": y},
    )
    resp.raise_for_status()


def main() -> int:
    args = parse_args()
    base_url = f"{args.broker_scheme}://{args.broker_host}:{args.broker_port}/machines/{args.machine}"
    headers = {"X-API-Key": args.api_key}

    verify: bool | object = True
    if args.broker_scheme == "https":
        verify = fetch_pinned_ssl_context(args.broker_host, args.broker_port, args.broker_cert_fingerprint)

    with httpx.Client(base_url=base_url, timeout=args.timeout, verify=verify) as client:
        print(f"Target: ({args.x}, {args.y})\n")

        print("1. Screenshot before anything")
        save_screenshot(client, headers, "1_before")

        print(f"2. Single click at ({args.x}, {args.y}) — should select, not launch")
        click(client, headers, args.x, args.y, clicks=1)
        time.sleep(0.5)
        save_screenshot(client, headers, "2_after_single_click")

        print(f"   waiting {SETTLE_SECONDS}s past the OS double-click threshold")
        time.sleep(SETTLE_SECONDS)

        print("3. Click on empty desktop to deselect")
        click(client, headers, 50, 950, clicks=1)
        time.sleep(0.5)
        save_screenshot(client, headers, "3_after_deselect")

        print(f"4. Double click at ({args.x}, {args.y}) — should launch")
        click(client, headers, args.x, args.y, clicks=2)
        time.sleep(2.0)
        save_screenshot(client, headers, "4_after_double_click")

    print("\nCompare the four click_test_*.jpg files: 2 should show only a selection")
    print("highlight, 4 should show the target actually launched.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
