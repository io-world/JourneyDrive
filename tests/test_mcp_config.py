from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("mcp")  # controller-side-only extra; not installed for the Windows thin-client build

from journeydrive_mcp.config import ConfigError, load_settings


def write_config(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data))
    return path


def test_loads_from_file(tmp_path: Path) -> None:
    path = write_config(tmp_path, {"broker_host": "192.168.1.50", "broker_api_key": "a" * 32})
    settings = load_settings(path)
    assert settings.broker_host == "192.168.1.50"
    assert settings.broker_api_key == "a" * 32
    assert settings.broker_port == 8600
    assert settings.mcp_host == "127.0.0.1"
    assert settings.mcp_port == 8000


def test_file_overrides_all_fields(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        {
            "broker_host": "10.0.0.5",
            "broker_api_key": "b" * 32,
            "broker_port": 9000,
            "broker_scheme": "https",
            "broker_cert_fingerprint": "aa" * 32,
            "timeout": 5.0,
            "mcp_host": "0.0.0.0",
            "mcp_port": 9001,
        },
    )
    settings = load_settings(path)
    assert settings.broker_port == 9000
    assert settings.broker_scheme == "https"
    assert settings.broker_cert_fingerprint == "aa" * 32
    assert settings.timeout == 5.0
    assert settings.mcp_host == "0.0.0.0"
    assert settings.mcp_port == 9001


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_settings(tmp_path / "does-not-exist.json")


def test_malformed_json_raises(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text("{not valid json")
    with pytest.raises(ConfigError, match="not valid JSON"):
        load_settings(path)


def test_missing_broker_host_raises(tmp_path: Path) -> None:
    path = write_config(tmp_path, {"broker_api_key": "a" * 32})
    with pytest.raises(ConfigError, match="broker_host"):
        load_settings(path)


def test_missing_broker_api_key_raises(tmp_path: Path) -> None:
    path = write_config(tmp_path, {"broker_host": "192.168.1.50"})
    with pytest.raises(ConfigError, match="broker_api_key"):
        load_settings(path)


def test_env_vars_used_when_no_config_path(monkeypatch) -> None:
    monkeypatch.setenv("JOURNEYDRIVE_BROKER_HOST", "192.168.1.50")
    monkeypatch.setenv("JOURNEYDRIVE_BROKER_API_KEY", "a" * 32)
    settings = load_settings()
    assert settings.broker_host == "192.168.1.50"


def test_missing_broker_host_env_var_raises(monkeypatch) -> None:
    monkeypatch.delenv("JOURNEYDRIVE_BROKER_HOST", raising=False)
    monkeypatch.delenv("JOURNEYDRIVE_BROKER_API_KEY", raising=False)
    with pytest.raises(ConfigError, match="JOURNEYDRIVE_BROKER_HOST"):
        load_settings()


def test_https_without_fingerprint_raises_file(tmp_path: Path) -> None:
    path = write_config(
        tmp_path, {"broker_host": "192.168.1.50", "broker_api_key": "a" * 32, "broker_scheme": "https"}
    )
    with pytest.raises(ConfigError, match="broker_cert_fingerprint is required"):
        load_settings(path)


def test_https_without_fingerprint_raises_env(monkeypatch) -> None:
    monkeypatch.setenv("JOURNEYDRIVE_BROKER_HOST", "192.168.1.50")
    monkeypatch.setenv("JOURNEYDRIVE_BROKER_API_KEY", "a" * 32)
    monkeypatch.setenv("JOURNEYDRIVE_BROKER_SCHEME", "https")
    monkeypatch.delenv("JOURNEYDRIVE_BROKER_CERT_FINGERPRINT", raising=False)
    with pytest.raises(ConfigError, match="broker_cert_fingerprint is required"):
        load_settings()


def test_https_with_fingerprint_loads_env(monkeypatch) -> None:
    monkeypatch.setenv("JOURNEYDRIVE_BROKER_HOST", "192.168.1.50")
    monkeypatch.setenv("JOURNEYDRIVE_BROKER_API_KEY", "a" * 32)
    monkeypatch.setenv("JOURNEYDRIVE_BROKER_SCHEME", "https")
    monkeypatch.setenv("JOURNEYDRIVE_BROKER_CERT_FINGERPRINT", "aa" * 32)
    settings = load_settings()
    assert settings.broker_scheme == "https"
    assert settings.broker_cert_fingerprint == "aa" * 32


def test_invalid_broker_scheme_raises_file(tmp_path: Path) -> None:
    path = write_config(
        tmp_path, {"broker_host": "192.168.1.50", "broker_api_key": "a" * 32, "broker_scheme": "ftp"}
    )
    with pytest.raises(ConfigError, match="broker_scheme must be"):
        load_settings(path)
