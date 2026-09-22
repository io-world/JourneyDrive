from __future__ import annotations

import argparse
import asyncio
import dataclasses
import logging
import sys

from journeydrive_mcp.client import JourneyDriveClient, JourneyDriveError
from journeydrive_mcp.config import ConfigError, load_settings
from journeydrive_mcp.logging_setup import configure_logging
from journeydrive_mcp.server import build_server
from journeydrive_windows_thinclient.tls_pinning import CertificateFingerprintMismatch

__all__ = ["main"]

logger = logging.getLogger(__name__)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="journeydrive-mcp")
    parser.add_argument(
        "--config",
        default=None,
        help="Path to a JSON config file (e.g. scripts/mcp/mcp_config.json) with broker_host/broker_api_key/etc. "
        "Overrides the JOURNEYDRIVE_* environment variables when given.",
    )
    return parser.parse_args(argv)


def main() -> None:
    configure_logging()

    args = parse_args()
    try:
        settings = load_settings(args.config)
    except ConfigError as e:
        print(f"journeydrive-mcp: {e}", file=sys.stderr)
        sys.exit(1)

    logger.info("Configured for broker %s:%s, listening on %s:%s", settings.broker_host, settings.broker_port, settings.mcp_host, settings.mcp_port)

    try:
        client = JourneyDriveClient(settings)
    except CertificateFingerprintMismatch as e:
        # Not recoverable by retrying — either broker_cert_fingerprint in config is
        # wrong, or the broker's certificate was regenerated without updating it.
        logger.error("%s", e)
        print(f"journeydrive-mcp: {e}", file=sys.stderr)
        sys.exit(1)

    # Broker-owned operational config (see docs/BROKER.md's "Broker-pushed
    # config") overrides the matching local fields, fetched once at startup — not
    # a hard requirement to start, since the broker being briefly unreachable here
    # shouldn't block this server from listening at all; every tool call already
    # handles a broker that's down the same way (a normal JourneyDriveError).
    try:
        pushed = asyncio.run(client.get_mcp_config())
    except JourneyDriveError as e:
        logger.warning("could not fetch broker-pushed config, using local settings only: %s", e)
        pushed = {}
    if pushed:
        settings = dataclasses.replace(settings, **pushed)
        logger.info("applied broker-pushed config: %s", pushed)
        if "timeout" in pushed:
            # settings.timeout is now updated, but `client`'s own httpx.AsyncClient
            # was already built with the old value at construction time — re-apply
            # it directly rather than rebuilding the client (which would redo the
            # TLS-pinning handshake unnecessarily when TLS is on).
            client.set_timeout(settings.timeout)

    server = build_server(client, settings)
    server.run(transport="streamable-http", host=settings.mcp_host, port=settings.mcp_port)
