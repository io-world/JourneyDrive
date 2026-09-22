from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, model_validator


class ScreenshotConfig(BaseModel, extra="forbid"):
    format: Literal["png", "jpeg"] = "png"
    quality: int = Field(default=75, ge=1, le=100)
    monitor: int = Field(default=0, ge=0)


class Config(BaseModel, extra="forbid"):
    broker_host: str
    broker_port: int = Field(default=8601, ge=1, le=65535)
    machine_id: str = Field(min_length=1, description="Identifies this machine to the broker; must match one of the broker's configured machine IDs.")
    api_key: str = Field(min_length=16, description="Must match this machine_id's key in the broker's own config.")
    broker_tls: bool = Field(default=False, description="If true, connect to the broker over wss:// and verify its certificate against broker_cert_fingerprint (required when this is true).")
    broker_cert_fingerprint: str | None = Field(default=None, description="SHA-256 fingerprint (hex, colons optional) of the broker's TLS certificate. Required when broker_tls is true.")
    screenshot: ScreenshotConfig = Field(default_factory=ScreenshotConfig)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_file: str = "journeydrive-win.log"

    @model_validator(mode="after")
    def _check_tls_fingerprint_pair(self) -> "Config":
        if not self.broker_tls:
            return self
        if not self.broker_cert_fingerprint:
            raise ValueError("broker_cert_fingerprint is required when broker_tls is true")
        normalized = self.broker_cert_fingerprint.replace(":", "").lower()
        if len(normalized) != 64 or any(c not in "0123456789abcdef" for c in normalized):
            raise ValueError(
                "broker_cert_fingerprint must be a SHA-256 fingerprint: 64 hex characters, colons optional"
            )
        return self


class ConfigError(Exception):
    pass


def default_config_path() -> Path:
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent
    else:
        base = Path.cwd()
    return base / "config.json"


def resolve_config_path(cli_path: str | None) -> Path:
    if cli_path:
        return Path(cli_path)
    env_path = os.environ.get("JOURNEYDRIVE_CONFIG")
    if env_path:
        return Path(env_path)
    return default_config_path()


def load_config(path: Path) -> Config:
    if not path.is_file():
        raise ConfigError(
            f"Config file not found: {path}\n"
            "Copy examples/config.example.json to config.json next to the executable, "
            "set broker_host/machine_id/api_key to match the broker's config before running."
        )
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise ConfigError(f"Config file at {path} is not valid JSON: {e}") from e
    try:
        return Config.model_validate(data)
    except ValidationError as e:
        raise ConfigError(f"Config file at {path} failed validation:\n{e}") from e
