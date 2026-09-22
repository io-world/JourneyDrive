"""Type text into whatever window has focus on a machine, through a running broker.

Run this from any machine that can reach the broker — it never runs on the broker or
the thin client itself. Types into whatever window currently has focus there, so make
sure the right window is focused before running it.

Reads defaults from config.json next to this script (same file used by the other
scripts). Any CLI flag overrides the matching config value:

    uv run python scripts/testing/send_text.py --text "hello world"
    uv run python scripts/testing/send_text.py --broker-host 192.168.1.10 --api-key <key> --machine office-pc --text "hi"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx

from journeydrive_windows_thinclient.tls_pinning import fetch_pinned_ssl_context

TEXT = "Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat. Duis aute irure dolor in reprehenderit in voluptate velit esse cillum dolore eu fugiat nulla pariatur. Excepteur sint occaecat cupidatat non proident, sunt in culpa qui officia deserunt mollit anim id est laborum.\n\n "

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
    parser.add_argument("--text", default=TEXT, help="Text to type on the remote machine")
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


def main() -> int:
    args = parse_args()
    base_url = f"{args.broker_scheme}://{args.broker_host}:{args.broker_port}/machines/{args.machine}"

    verify: bool | object = True
    if args.broker_scheme == "https":
        verify = fetch_pinned_ssl_context(args.broker_host, args.broker_port, args.broker_cert_fingerprint)

    try:
        resp = httpx.post(
            f"{base_url}/keyboard/type",
            headers={"X-API-Key": args.api_key},
            json={"text": args.text},
            timeout=args.timeout,
            verify=verify,
        )
    except httpx.HTTPError as e:
        print(f"request error: {e}", file=sys.stderr)
        return 1

    if resp.status_code != 200:
        print(f"FAIL: status={resp.status_code} body={resp.text}", file=sys.stderr)
        return 1

    body = resp.json()
    print(f"typed {body.get('length')} character(s) — verify it landed in the right window")
    return 0


if __name__ == "__main__":
    sys.exit(main())
