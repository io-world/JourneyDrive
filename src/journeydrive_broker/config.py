from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from journeydrive_windows_thinclient.config import ScreenshotConfig


class ConfigError(Exception):
    pass


_VALID_MCP_PROFILE_KEYS = {"save_screenshots", "screenshot_dir", "max_saved_screenshots", "timeout"}


_MIN_KEY_LENGTH = 16  # matches journeydrive_windows_thinclient.config.Config.api_key's min_length


def _validate_key_lengths(api_key: str, machines: dict[str, str]) -> None:
    if len(api_key) < _MIN_KEY_LENGTH:
        raise ConfigError(f"'api_key' must be at least {_MIN_KEY_LENGTH} characters")
    for machine_id, key in machines.items():
        if len(key) < _MIN_KEY_LENGTH:
            raise ConfigError(f"machines[{machine_id!r}]'s key must be at least {_MIN_KEY_LENGTH} characters")


@dataclass(frozen=True)
class Settings:
    api_key: str  # MCP-facing secret for the HTTP API
    machines: dict[str, str] = field(default_factory=dict)  # machine_id -> api_key
    host: str = "0.0.0.0"
    http_port: int = 8600
    ws_host: str = "0.0.0.0"
    ws_port: int = 8601
    request_timeout: float = 15.0
    # Both required together to enable TLS on both listeners (one cert serves both
    # — same process/machine identity); absent (the default) means plaintext, same
    # as before TLS support existed.
    tls_cert_file: str | None = None
    tls_key_file: str | None = None
    # Operational config the broker owns and pushes to clients, instead of every
    # thin client / the MCP server needing its own local copy — see
    # docs/BROKER.md's "Broker-pushed config" section. machine_id keys here must
    # also be keys in `machines`; values are applied on top of whatever a client's
    # own local config already has (a client with no matching profile, or fields a
    # profile doesn't mention, just keeps behaving exactly as it did before this
    # existed).
    machine_profiles: dict[str, dict] = field(default_factory=dict)  # machine_id -> {"screenshot": {...}, "log_level": ...}
    mcp_profile: dict = field(default_factory=dict)  # {"save_screenshots": ..., "screenshot_dir": ..., "max_saved_screenshots": ...}


def _validate_tls_files(tls_cert_file: str | None, tls_key_file: str | None) -> None:
    if (tls_cert_file is None) != (tls_key_file is None):
        raise ConfigError("tls_cert_file and tls_key_file must be given together")
    if tls_cert_file is not None and not Path(tls_cert_file).is_file():
        raise ConfigError(f"tls_cert_file not found: {tls_cert_file}")
    if tls_key_file is not None and not Path(tls_key_file).is_file():
        raise ConfigError(f"tls_key_file not found: {tls_key_file}")


def _validate_machine_profiles(machine_profiles: dict[str, dict], machines: dict[str, str]) -> None:
    for machine_id, profile in machine_profiles.items():
        if machine_id not in machines:
            raise ConfigError(f"machine_profiles[{machine_id!r}] has no matching entry in 'machines'")
        if not isinstance(profile, dict):
            raise ConfigError(f"machine_profiles[{machine_id!r}] must be an object")
        unknown = set(profile) - {"screenshot", "log_level"}
        if unknown:
            raise ConfigError(f"machine_profiles[{machine_id!r}] has unknown key(s): {sorted(unknown)}")
        if "screenshot" in profile:
            try:
                ScreenshotConfig.model_validate(profile["screenshot"])
            except ValidationError as e:
                raise ConfigError(f"machine_profiles[{machine_id!r}]['screenshot']: {e}") from e
        if "log_level" in profile and profile["log_level"] not in logging.getLevelNamesMapping():
            raise ConfigError(
                f"machine_profiles[{machine_id!r}]['log_level'] must be a valid logging level, "
                f"got {profile['log_level']!r}"
            )


def _validate_mcp_profile(mcp_profile: dict) -> None:
    if not isinstance(mcp_profile, dict):
        raise ConfigError("mcp_profile must be an object")
    unknown = set(mcp_profile) - _VALID_MCP_PROFILE_KEYS
    if unknown:
        raise ConfigError(f"mcp_profile has unknown key(s): {sorted(unknown)}")


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

    api_key = data.get("api_key")
    if not api_key:
        raise ConfigError(f"{path}: 'api_key' is required (the MCP-facing secret)")

    machines = data.get("machines")
    if not machines:
        raise ConfigError(f"{path}: 'machines' is required — an object of machine_id: api_key pairs")

    try:
        _validate_key_lengths(api_key, machines)
    except ConfigError as e:
        raise ConfigError(f"{path}: {e}") from e

    tls_cert_file = data.get("tls_cert_file")
    tls_key_file = data.get("tls_key_file")
    try:
        _validate_tls_files(tls_cert_file, tls_key_file)
    except ConfigError as e:
        raise ConfigError(f"{path}: {e}") from e

    machine_profiles = data.get("machine_profiles", {})
    mcp_profile = data.get("mcp_profile", {})
    try:
        _validate_machine_profiles(machine_profiles, machines)
        _validate_mcp_profile(mcp_profile)
    except ConfigError as e:
        raise ConfigError(f"{path}: {e}") from e

    try:
        return Settings(
            api_key=api_key,
            machines=dict(machines),
            host=data.get("host", "0.0.0.0"),
            http_port=int(data.get("http_port", 8600)),
            ws_host=data.get("ws_host", "0.0.0.0"),
            ws_port=int(data.get("ws_port", 8601)),
            request_timeout=float(data.get("request_timeout", 15.0)),
            tls_cert_file=tls_cert_file,
            tls_key_file=tls_key_file,
            machine_profiles=dict(machine_profiles),
            mcp_profile=dict(mcp_profile),
        )
    except (TypeError, ValueError) as e:
        raise ConfigError(f"{path}: invalid value: {e}") from e


def _load_settings_from_env() -> Settings:
    api_key = os.environ.get("JOURNEYDRIVE_BROKER_API_KEY")
    if not api_key:
        raise ConfigError("JOURNEYDRIVE_BROKER_API_KEY is required")

    machines_raw = os.environ.get("JOURNEYDRIVE_BROKER_MACHINES")
    if not machines_raw:
        raise ConfigError(
            "JOURNEYDRIVE_BROKER_MACHINES is required — a JSON object of machine_id: api_key pairs"
        )
    try:
        machines = json.loads(machines_raw)
    except json.JSONDecodeError as e:
        raise ConfigError(f"JOURNEYDRIVE_BROKER_MACHINES is not valid JSON: {e}") from e

    _validate_key_lengths(api_key, machines)

    host = os.environ.get("JOURNEYDRIVE_BROKER_HOST", "0.0.0.0")
    ws_host = os.environ.get("JOURNEYDRIVE_BROKER_WS_HOST", "0.0.0.0")

    try:
        http_port = int(os.environ.get("JOURNEYDRIVE_BROKER_HTTP_PORT", "8600"))
        ws_port = int(os.environ.get("JOURNEYDRIVE_BROKER_WS_PORT", "8601"))
    except ValueError as e:
        raise ConfigError(f"JOURNEYDRIVE_BROKER_HTTP_PORT/_WS_PORT must be integers: {e}") from e

    request_timeout_raw = os.environ.get("JOURNEYDRIVE_BROKER_REQUEST_TIMEOUT", "15.0")
    try:
        request_timeout = float(request_timeout_raw)
    except ValueError as e:
        raise ConfigError(f"JOURNEYDRIVE_BROKER_REQUEST_TIMEOUT must be a number: {e}") from e

    tls_cert_file = os.environ.get("JOURNEYDRIVE_BROKER_TLS_CERT_FILE")
    tls_key_file = os.environ.get("JOURNEYDRIVE_BROKER_TLS_KEY_FILE")
    _validate_tls_files(tls_cert_file, tls_key_file)

    machine_profiles_raw = os.environ.get("JOURNEYDRIVE_BROKER_MACHINE_PROFILES", "{}")
    try:
        machine_profiles = json.loads(machine_profiles_raw)
    except json.JSONDecodeError as e:
        raise ConfigError(f"JOURNEYDRIVE_BROKER_MACHINE_PROFILES is not valid JSON: {e}") from e

    mcp_profile_raw = os.environ.get("JOURNEYDRIVE_BROKER_MCP_PROFILE", "{}")
    try:
        mcp_profile = json.loads(mcp_profile_raw)
    except json.JSONDecodeError as e:
        raise ConfigError(f"JOURNEYDRIVE_BROKER_MCP_PROFILE is not valid JSON: {e}") from e

    _validate_machine_profiles(machine_profiles, machines)
    _validate_mcp_profile(mcp_profile)

    return Settings(
        api_key=api_key,
        machines=machines,
        host=host,
        http_port=http_port,
        ws_host=ws_host,
        ws_port=ws_port,
        request_timeout=request_timeout,
        tls_cert_file=tls_cert_file,
        tls_key_file=tls_key_file,
        machine_profiles=machine_profiles,
        mcp_profile=mcp_profile,
    )
