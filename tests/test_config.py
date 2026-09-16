from __future__ import annotations

import json
from pathlib import Path

import pytest

from journeycapture_windows_thinclient.config import ConfigError, load_config


def write_config(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data))
    return path


def valid_data() -> dict:
    return {
        "broker_host": "192.168.1.10",
        "machine_id": "office-pc",
        "api_key": "a" * 32,
    }


def test_valid_config_parses_with_defaults(tmp_path: Path) -> None:
    path = write_config(tmp_path, valid_data())
    config = load_config(path)
    assert config.broker_host == "192.168.1.10"
    assert config.broker_port == 8601
    assert config.machine_id == "office-pc"
    assert config.screenshot.format == "png"
    assert config.screenshot.quality == 75
    assert config.log_level == "INFO"


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_config(tmp_path / "does-not-exist.json")


def test_malformed_json_raises(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text("{not valid json")
    with pytest.raises(ConfigError):
        load_config(path)


def test_missing_api_key_raises(tmp_path: Path) -> None:
    data = valid_data()
    del data["api_key"]
    path = write_config(tmp_path, data)
    with pytest.raises(ConfigError):
        load_config(path)


def test_short_api_key_raises(tmp_path: Path) -> None:
    data = valid_data()
    data["api_key"] = "short"
    path = write_config(tmp_path, data)
    with pytest.raises(ConfigError):
        load_config(path)


def test_missing_broker_host_raises(tmp_path: Path) -> None:
    data = valid_data()
    del data["broker_host"]
    path = write_config(tmp_path, data)
    with pytest.raises(ConfigError):
        load_config(path)


def test_missing_machine_id_raises(tmp_path: Path) -> None:
    data = valid_data()
    del data["machine_id"]
    path = write_config(tmp_path, data)
    with pytest.raises(ConfigError):
        load_config(path)


def test_unknown_field_raises(tmp_path: Path) -> None:
    data = valid_data()
    data["unexpected_field"] = "surprise"
    path = write_config(tmp_path, data)
    with pytest.raises(ConfigError):
        load_config(path)


def test_broker_tls_without_fingerprint_raises(tmp_path: Path) -> None:
    data = valid_data()
    data["broker_tls"] = True
    path = write_config(tmp_path, data)
    with pytest.raises(ConfigError, match="broker_cert_fingerprint is required"):
        load_config(path)


def test_broker_tls_with_malformed_fingerprint_raises(tmp_path: Path) -> None:
    data = valid_data()
    data["broker_tls"] = True
    data["broker_cert_fingerprint"] = "not-a-real-fingerprint"
    path = write_config(tmp_path, data)
    with pytest.raises(ConfigError, match="SHA-256 fingerprint"):
        load_config(path)


def test_broker_tls_with_valid_fingerprint_parses(tmp_path: Path) -> None:
    data = valid_data()
    data["broker_tls"] = True
    data["broker_cert_fingerprint"] = ":".join("AB" for _ in range(32))  # 64 hex chars, colon-separated
    path = write_config(tmp_path, data)
    config = load_config(path)
    assert config.broker_tls is True
    assert config.broker_cert_fingerprint == data["broker_cert_fingerprint"]


def test_broker_tls_false_does_not_require_fingerprint(tmp_path: Path) -> None:
    data = valid_data()
    path = write_config(tmp_path, data)
    config = load_config(path)
    assert config.broker_tls is False
    assert config.broker_cert_fingerprint is None
