"""Fetch a single screenshot from one machine through a running broker.

Run this from any machine that can reach the broker — it never runs on the broker or
the thin client itself.

Reads defaults from config.json next to this script (same file used by the other
scripts and by journeydrive-mcp). Any CLI flag overrides the matching config value,
so it also works with no config file at all, e.g. run unmodified from VS Code's Run
button once the config file has broker_host/api_key/machine_id filled in. Saves into
screenshot_dir (same config key and default journeydrive_mcp's optional
local-saving feature uses) with a timestamped filename, unless --out gives an exact
path:

    uv run python scripts/testing/get_screenshot.py
    uv run python scripts/testing/get_screenshot.py --broker-host 192.168.1.10 --api-key <key> --machine office-pc
    uv run python scripts/testing/get_screenshot.py --monitor 1 --format png --dir shots
    uv run python scripts/testing/get_screenshot.py --out desktop.png
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

from journeydrive_windows_thinclient.tls_pinning import fetch_pinned_ssl_context

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
        "--monitor", type=int, default=config.get("monitor"), help="Monitor index (default: machine's own config)"
    )
    parser.add_argument("--format", choices=["png", "jpeg"], default=config.get("format"), help="Default: machine's own config")
    parser.add_argument(
        "--quality", type=int, default=config.get("quality"), help="JPEG quality 1-100 (default: machine's own config)"
    )
    parser.add_argument(
        "--dir", default=config.get("screenshot_dir", "screenshots"), help="Directory to save into"
    )
    parser.add_argument("--out", default=None, help="Exact output file path, overrides --dir/timestamp naming")
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
    params = {}
    if args.format is not None:
        params["format"] = args.format
    if args.quality is not None:
        params["quality"] = args.quality
    if args.monitor is not None:
        params["monitor"] = args.monitor

    verify: bool | object = True
    if args.broker_scheme == "https":
        verify = fetch_pinned_ssl_context(args.broker_host, args.broker_port, args.broker_cert_fingerprint)

    try:
        resp = httpx.get(
            f"{base_url}/screenshot",
            headers={"X-API-Key": args.api_key},
            params=params,
            timeout=args.timeout,
            verify=verify,
        )
    except httpx.HTTPError as e:
        print(f"request error: {e}", file=sys.stderr)
        return 1

    if resp.status_code != 200:
        print(f"FAIL: status={resp.status_code} body={resp.text}", file=sys.stderr)
        return 1

    content_type = resp.headers.get("content-type", "")
    ext = "png" if "png" in content_type else "jpeg"
    if args.out:
        out_path = Path(args.out)
    else:
        out_dir = Path(args.dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%f")
        out_path = out_dir / f"{timestamp}.{ext}"
    out_path.write_bytes(resp.content)
    print(f"saved {len(resp.content)} bytes to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
