from __future__ import annotations

import argparse
import asyncio
import logging
import sys

import uvicorn

from journeydrive_broker.config import ConfigError, Settings, load_settings
from journeydrive_broker.http_api import create_app
from journeydrive_broker.logging_setup import configure_logging
from journeydrive_broker.registry import ConnectionRegistry
from journeydrive_broker.ws_server import run as run_ws_server

__all__ = ["main"]

logger = logging.getLogger(__name__)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="journeydrive-broker")
    parser.add_argument(
        "--config",
        default=None,
        help="Path to a JSON config file with api_key/machines/etc. "
        "Overrides the JOURNEYDRIVE_BROKER_* environment variables when given.",
    )
    return parser.parse_args(argv)


async def _run(settings: Settings) -> None:
    registry = ConnectionRegistry(settings)
    app = create_app(settings, registry)
    uvicorn_config = uvicorn.Config(
        app,
        host=settings.host,
        port=settings.http_port,
        log_config=None,
        ssl_keyfile=settings.tls_key_file,
        ssl_certfile=settings.tls_cert_file,
    )
    http_server = uvicorn.Server(uvicorn_config)

    tls_on = bool(settings.tls_cert_file and settings.tls_key_file)
    logger.info(
        "Starting broker: HTTP on %s:%d, websocket on %s:%d, %d known machine(s), TLS %s",
        settings.host,
        settings.http_port,
        settings.ws_host,
        settings.ws_port,
        len(settings.machines),
        "on" if tls_on else "off",
    )
    await asyncio.gather(http_server.serve(), run_ws_server(settings, registry))


def main() -> None:
    args = parse_args()
    try:
        settings = load_settings(args.config)
    except ConfigError as e:
        print(f"journeydrive-broker: {e}", file=sys.stderr)
        sys.exit(1)

    configure_logging()
    asyncio.run(_run(settings))
