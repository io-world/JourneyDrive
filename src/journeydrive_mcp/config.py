from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


class ConfigError(Exception):
    pass


def _validate_broker_scheme_and_fingerprint(broker_scheme: str, broker_cert_fingerprint: str | None) -> None:
    if broker_scheme not in ("http", "https"):
        raise ConfigError(f"broker_scheme must be 'http' or 'https', got {broker_scheme!r}")
    if broker_scheme == "https" and not broker_cert_fingerprint:
        raise ConfigError("broker_cert_fingerprint is required when broker_scheme is 'https'")


@dataclass(frozen=True)
class Settings:
    broker_host: str
    broker_api_key: str
    broker_port: int = 8600
    broker_scheme: str = "http"
    broker_cert_fingerprint: str | None = None
    timeout: float = 10.0
    # Where this MCP server itself listens (HTTP transport) — distinct from
    # broker_host/broker_port above, which are the broker's address, not this
    # server's.
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8000
    # Off by default: every take_screenshot call also saves a timestamped copy
    # locally, for debugging what the model actually saw.
    save_screenshots: bool = False
    screenshot_dir: str = "screenshots"
    # Oldest files beyond this count get pruned after each save. 0 or negative
    # disables pruning (keep everything).
    max_saved_screenshots: int = 100


def load_settings(config_path: str | Path | None = None) -> Settings:
    if config_path is not None:
        return _load_settings_from_file(Path(config_path))
    return _load_settings_from_env()


def _load_settings_from_file(path: Path) -> Settings:
    if not path.is_file():
        raise ConfigError(f"Config file not found: {path}")
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise ConfigError(f"Config file at {path} is not valid JSON: {e}") from e

    broker_host = data.get("broker_host")
    if not broker_host:
        raise ConfigError(f"{path}: 'broker_host' is required (the broker's IP or hostname)")

    broker_api_key = data.get("broker_api_key")
    if not broker_api_key:
        raise ConfigError(f"{path}: 'broker_api_key' is required (must match the broker's own config)")

    broker_scheme = data.get("broker_scheme", "http")
    broker_cert_fingerprint = data.get("broker_cert_fingerprint")
    try:
        _validate_broker_scheme_and_fingerprint(broker_scheme, broker_cert_fingerprint)
    except ConfigError as e:
        raise ConfigError(f"{path}: {e}") from e

    try:
        return Settings(
            broker_host=broker_host,
            broker_api_key=broker_api_key,
            broker_port=int(data.get("broker_port", 8600)),
            broker_scheme=broker_scheme,
            broker_cert_fingerprint=broker_cert_fingerprint,
            timeout=float(data.get("timeout", 10.0)),
            mcp_host=data.get("mcp_host", "127.0.0.1"),
            mcp_port=int(data.get("mcp_port", 8000)),
            save_screenshots=bool(data.get("save_screenshots", False)),
            screenshot_dir=data.get("screenshot_dir", "screenshots"),
            max_saved_screenshots=int(data.get("max_saved_screenshots", 100)),
        )
    except (TypeError, ValueError) as e:
        raise ConfigError(f"{path}: invalid value: {e}") from e


def _load_settings_from_env() -> Settings:
    broker_host = os.environ.get("JOURNEYDRIVE_BROKER_HOST")
    if not broker_host:
        raise ConfigError("JOURNEYDRIVE_BROKER_HOST is required (the broker's IP or hostname)")

    broker_api_key = os.environ.get("JOURNEYDRIVE_BROKER_API_KEY")
    if not broker_api_key:
        raise ConfigError("JOURNEYDRIVE_BROKER_API_KEY is required (must match the broker's own config)")

    broker_port_raw = os.environ.get("JOURNEYDRIVE_BROKER_PORT", "8600")
    try:
        broker_port = int(broker_port_raw)
    except ValueError as e:
        raise ConfigError(f"JOURNEYDRIVE_BROKER_PORT must be an integer, got {broker_port_raw!r}") from e

    broker_scheme = os.environ.get("JOURNEYDRIVE_BROKER_SCHEME", "http")
    broker_cert_fingerprint = os.environ.get("JOURNEYDRIVE_BROKER_CERT_FINGERPRINT")
    _validate_broker_scheme_and_fingerprint(broker_scheme, broker_cert_fingerprint)

    mcp_host = os.environ.get("JOURNEYDRIVE_MCP_HOST", "127.0.0.1")

    mcp_port_raw = os.environ.get("JOURNEYDRIVE_MCP_PORT", "8000")
    try:
        mcp_port = int(mcp_port_raw)
    except ValueError as e:
        raise ConfigError(f"JOURNEYDRIVE_MCP_PORT must be an integer, got {mcp_port_raw!r}") from e

    save_screenshots = os.environ.get("JOURNEYDRIVE_MCP_SAVE_SCREENSHOTS", "").lower() in ("1", "true", "yes")
    screenshot_dir = os.environ.get("JOURNEYDRIVE_MCP_SCREENSHOT_DIR", "screenshots")

    max_saved_raw = os.environ.get("JOURNEYDRIVE_MCP_MAX_SAVED_SCREENSHOTS", "100")
    try:
        max_saved_screenshots = int(max_saved_raw)
    except ValueError as e:
        raise ConfigError(
            f"JOURNEYDRIVE_MCP_MAX_SAVED_SCREENSHOTS must be an integer, got {max_saved_raw!r}"
        ) from e

    return Settings(
        broker_host=broker_host,
        broker_api_key=broker_api_key,
        broker_port=broker_port,
        broker_scheme=broker_scheme,
        broker_cert_fingerprint=broker_cert_fingerprint,
        mcp_host=mcp_host,
        mcp_port=mcp_port,
        save_screenshots=save_screenshots,
        screenshot_dir=screenshot_dir,
        max_saved_screenshots=max_saved_screenshots,
    )
