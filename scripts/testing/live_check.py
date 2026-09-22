"""Smoke-test a machine through a running broker, over the network.

Run this from any machine that can reach the broker (this dev machine, a
teammate's laptop, etc.) — it never runs on the broker or the thin client itself.
Exercises health, monitors, screenshot, and optionally the mouse/keyboard routes for
one machine behind the broker, and prints a pass/fail summary.

Usage:
    uv run python scripts/testing/live_check.py --broker-host 192.168.1.10 --api-key <broker-key> --machine office-pc
    uv run python scripts/testing/live_check.py --broker-host 192.168.1.10 --api-key <broker-key> --machine office-pc --with-mouse --with-keyboard
"""

from __future__ import annotations

import argparse
import io
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from PIL import Image

from journeydrive_windows_thinclient.tls_pinning import fetch_pinned_ssl_context


@dataclass
class Results:
    passed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    def ok(self, label: str) -> None:
        print(f"  PASS: {label}")
        self.passed.append(label)

    def fail(self, label: str, detail: str) -> None:
        print(f"  FAIL: {label} — {detail}")
        self.failed.append(label)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--broker-host", required=True, help="IP or hostname of the broker")
    parser.add_argument("--broker-port", type=int, default=8600)
    parser.add_argument("--broker-scheme", default="http", choices=["http", "https"])
    parser.add_argument("--machine", required=True, help="machine_id to test, as configured on the broker")
    parser.add_argument(
        "--api-key",
        default=os.environ.get("JOURNEYDRIVE_BROKER_API_KEY"),
        help="The broker's own api_key. Defaults to the JOURNEYDRIVE_BROKER_API_KEY env var",
    )
    parser.add_argument(
        "--broker-cert-fingerprint",
        default=os.environ.get("JOURNEYDRIVE_BROKER_CERT_FINGERPRINT"),
        help="SHA-256 fingerprint of the broker's TLS certificate. Required when --broker-scheme https. "
        "Defaults to the JOURNEYDRIVE_BROKER_CERT_FINGERPRINT env var",
    )
    parser.add_argument("--out-dir", default=".", help="Where to save the fetched screenshot")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument(
        "--with-mouse",
        action="store_true",
        help="Also move the remote mouse cursor as a round-trip check (visible side effect)",
    )
    parser.add_argument(
        "--with-keyboard",
        action="store_true",
        help="Also type a short benign string on the remote machine (types into whatever "
        "window currently has focus there — use with care)",
    )
    args = parser.parse_args()
    if not args.api_key:
        parser.error("--api-key is required (or set JOURNEYDRIVE_BROKER_API_KEY)")
    if args.broker_scheme == "https" and not args.broker_cert_fingerprint:
        parser.error(
            "--broker-cert-fingerprint is required when --broker-scheme https "
            "(or set JOURNEYDRIVE_BROKER_CERT_FINGERPRINT)"
        )
    return args


def main() -> int:
    args = parse_args()
    base_url = f"{args.broker_scheme}://{args.broker_host}:{args.broker_port}/machines/{args.machine}"
    headers = {"X-API-Key": args.api_key}
    results = Results()

    print(f"Checking {base_url}\n")

    verify: bool | object = True
    if args.broker_scheme == "https":
        verify = fetch_pinned_ssl_context(args.broker_host, args.broker_port, args.broker_cert_fingerprint)

    with httpx.Client(base_url=base_url, timeout=args.timeout, verify=verify) as client:
        check_health(client, headers, results)
        check_auth_rejection(client, results)
        monitors = check_monitors(client, headers, results)
        check_screenshot(client, headers, Path(args.out_dir), results)

        if args.with_mouse:
            check_mouse(client, headers, monitors, results)
        else:
            print("  SKIP: mouse round-trip (pass --with-mouse to enable)")

        if args.with_keyboard:
            check_keyboard(client, headers, results)
        else:
            print("  SKIP: keyboard type (pass --with-keyboard to enable)")

    print(f"\n{len(results.passed)} passed, {len(results.failed)} failed")
    return 1 if results.failed else 0


def check_health(client: httpx.Client, headers: dict, results: Results) -> None:
    print("GET .../health")
    try:
        resp = client.get("/health", headers=headers)
        if resp.status_code == 200 and resp.json().get("status") == "ok":
            results.ok(f"health ok (version {resp.json().get('version')})")
        else:
            results.fail("health", f"status={resp.status_code} body={resp.text}")
    except httpx.HTTPError as e:
        results.fail("health", f"request error: {e}")


def check_auth_rejection(client: httpx.Client, results: Results) -> None:
    print("GET .../health with wrong broker API key (expect 401)")
    try:
        resp = client.get("/health", headers={"X-API-Key": "definitely-wrong-key"})
        if resp.status_code == 401:
            results.ok("wrong key rejected with 401")
        else:
            results.fail("auth rejection", f"expected 401, got {resp.status_code}")
    except httpx.HTTPError as e:
        results.fail("auth rejection", f"request error: {e}")


def check_monitors(client: httpx.Client, headers: dict, results: Results) -> list[dict]:
    print("GET .../screenshot/monitors")
    try:
        resp = client.get("/screenshot/monitors", headers=headers)
        if resp.status_code == 200 and resp.json():
            monitors = resp.json()
            results.ok(f"{len(monitors)} monitor(s): {monitors}")
            return monitors
        results.fail("monitors", f"status={resp.status_code} body={resp.text}")
    except httpx.HTTPError as e:
        results.fail("monitors", f"request error: {e}")
    return []


def check_screenshot(client: httpx.Client, headers: dict, out_dir: Path, results: Results) -> None:
    print("GET .../screenshot")
    try:
        resp = client.get("/screenshot", headers=headers)
        if resp.status_code != 200:
            results.fail("screenshot", f"status={resp.status_code} body={resp.text}")
            return
        image = Image.open(io.BytesIO(resp.content))
        out_path = out_dir / "live_check_screenshot.jpg"
        out_path.write_bytes(resp.content)
        results.ok(f"screenshot {image.size[0]}x{image.size[1]}, saved to {out_path}")
    except httpx.HTTPError as e:
        results.fail("screenshot", f"request error: {e}")
    except Exception as e:
        results.fail("screenshot", f"could not decode image: {e}")


def check_mouse(client: httpx.Client, headers: dict, monitors: list[dict], results: Results) -> None:
    print("POST .../mouse/move (round trip)")
    monitor = monitors[1] if len(monitors) > 1 else (monitors[0] if monitors else None)
    if monitor is None:
        results.fail("mouse move", "no monitor info available to compute a target coordinate")
        return
    target_x = monitor["left"] + monitor["width"] // 2
    target_y = monitor["top"] + monitor["height"] // 2
    try:
        resp = client.post("/mouse/move", headers=headers, json={"x": target_x, "y": target_y})
        body = resp.json()
        if resp.status_code == 200 and body.get("x") == target_x and body.get("y") == target_y:
            results.ok(f"cursor moved to ({target_x}, {target_y})")
        else:
            results.fail("mouse move", f"status={resp.status_code} body={body}")
    except httpx.HTTPError as e:
        results.fail("mouse move", f"request error: {e}")


def check_keyboard(client: httpx.Client, headers: dict, results: Results) -> None:
    print("POST .../keyboard/type ('journeydrive-live-check')")
    text = "journeydrive-live-check"
    try:
        resp = client.post("/keyboard/type", headers=headers, json={"text": text})
        body = resp.json()
        if resp.status_code == 200 and body.get("length") == len(text):
            results.ok(f"typed {len(text)} characters — verify it landed in the right window")
        else:
            results.fail("keyboard type", f"status={resp.status_code} body={body}")
    except httpx.HTTPError as e:
        results.fail("keyboard type", f"request error: {e}")


if __name__ == "__main__":
    sys.exit(main())
