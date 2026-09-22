from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")  # controller-side-only extra; not installed for the Windows thin-client build

from journeydrive_broker.config import ConfigError, load_settings


def write_config(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data))
    return path


def test_loads_from_file(tmp_path: Path) -> None:
    path = write_config(tmp_path, {"api_key": "a" * 32, "machines": {"office-pc": "b" * 32}})
    settings = load_settings(path)
    assert settings.api_key == "a" * 32
    assert settings.machines == {"office-pc": "b" * 32}
    assert settings.http_port == 8600
    assert settings.ws_port == 8601


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_settings(tmp_path / "does-not-exist.json")


def test_missing_api_key_raises(tmp_path: Path) -> None:
    path = write_config(tmp_path, {"machines": {"office-pc": "b" * 32}})
    with pytest.raises(ConfigError, match="api_key"):
        load_settings(path)


def test_missing_machines_raises(tmp_path: Path) -> None:
    path = write_config(tmp_path, {"api_key": "a" * 32})
    with pytest.raises(ConfigError, match="machines"):
        load_settings(path)


def test_short_api_key_raises(tmp_path: Path) -> None:
    path = write_config(tmp_path, {"api_key": "tooshort", "machines": {"office-pc": "b" * 32}})
    with pytest.raises(ConfigError, match="at least 16 characters"):
        load_settings(path)


def test_short_machine_key_raises(tmp_path: Path) -> None:
    path = write_config(tmp_path, {"api_key": "a" * 32, "machines": {"office-pc": "short"}})
    with pytest.raises(ConfigError, match="at least 16 characters"):
        load_settings(path)


def test_env_vars_used_when_no_config_path(monkeypatch) -> None:
    monkeypatch.setenv("JOURNEYDRIVE_BROKER_API_KEY", "a" * 32)
    monkeypatch.setenv("JOURNEYDRIVE_BROKER_MACHINES", json.dumps({"office-pc": "b" * 32}))
    settings = load_settings()
    assert settings.api_key == "a" * 32
    assert settings.machines == {"office-pc": "b" * 32}


def test_env_vars_read_request_timeout(monkeypatch) -> None:
    monkeypatch.setenv("JOURNEYDRIVE_BROKER_API_KEY", "a" * 32)
    monkeypatch.setenv("JOURNEYDRIVE_BROKER_MACHINES", json.dumps({"office-pc": "b" * 32}))
    monkeypatch.setenv("JOURNEYDRIVE_BROKER_REQUEST_TIMEOUT", "30.0")
    settings = load_settings()
    assert settings.request_timeout == 30.0


def test_tls_requires_cert_and_key_together_file(tmp_path: Path) -> None:
    cert = tmp_path / "cert.pem"
    cert.write_text("dummy")
    path = write_config(
        tmp_path, {"api_key": "a" * 32, "machines": {"office-pc": "b" * 32}, "tls_cert_file": str(cert)}
    )
    with pytest.raises(ConfigError, match="tls_cert_file and tls_key_file must be given together"):
        load_settings(path)


def test_tls_requires_cert_and_key_together_env(monkeypatch, tmp_path: Path) -> None:
    cert = tmp_path / "cert.pem"
    cert.write_text("dummy")
    monkeypatch.setenv("JOURNEYDRIVE_BROKER_API_KEY", "a" * 32)
    monkeypatch.setenv("JOURNEYDRIVE_BROKER_MACHINES", json.dumps({"office-pc": "b" * 32}))
    monkeypatch.setenv("JOURNEYDRIVE_BROKER_TLS_CERT_FILE", str(cert))
    with pytest.raises(ConfigError, match="tls_cert_file and tls_key_file must be given together"):
        load_settings()


def test_tls_missing_cert_file_raises(tmp_path: Path) -> None:
    key = tmp_path / "key.pem"
    key.write_text("dummy")
    path = write_config(
        tmp_path,
        {
            "api_key": "a" * 32,
            "machines": {"office-pc": "b" * 32},
            "tls_cert_file": str(tmp_path / "does-not-exist.pem"),
            "tls_key_file": str(key),
        },
    )
    with pytest.raises(ConfigError, match="tls_cert_file not found"):
        load_settings(path)


def test_tls_fields_load_from_file(tmp_path: Path) -> None:
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    cert.write_text("dummy")
    key.write_text("dummy")
    path = write_config(
        tmp_path,
        {
            "api_key": "a" * 32,
            "machines": {"office-pc": "b" * 32},
            "tls_cert_file": str(cert),
            "tls_key_file": str(key),
        },
    )
    settings = load_settings(path)
    assert settings.tls_cert_file == str(cert)
    assert settings.tls_key_file == str(key)


def test_tls_fields_load_from_env(monkeypatch, tmp_path: Path) -> None:
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    cert.write_text("dummy")
    key.write_text("dummy")
    monkeypatch.setenv("JOURNEYDRIVE_BROKER_API_KEY", "a" * 32)
    monkeypatch.setenv("JOURNEYDRIVE_BROKER_MACHINES", json.dumps({"office-pc": "b" * 32}))
    monkeypatch.setenv("JOURNEYDRIVE_BROKER_TLS_CERT_FILE", str(cert))
    monkeypatch.setenv("JOURNEYDRIVE_BROKER_TLS_KEY_FILE", str(key))
    settings = load_settings()
    assert settings.tls_cert_file == str(cert)
    assert settings.tls_key_file == str(key)


def test_tls_absent_by_default(tmp_path: Path) -> None:
    path = write_config(tmp_path, {"api_key": "a" * 32, "machines": {"office-pc": "b" * 32}})
    settings = load_settings(path)
    assert settings.tls_cert_file is None
    assert settings.tls_key_file is None


def test_machine_profiles_load_from_file(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        {
            "api_key": "a" * 32,
            "machines": {"office-pc": "b" * 32},
            "machine_profiles": {"office-pc": {"screenshot": {"format": "png"}, "log_level": "DEBUG"}},
        },
    )
    settings = load_settings(path)
    assert settings.machine_profiles == {"office-pc": {"screenshot": {"format": "png"}, "log_level": "DEBUG"}}


def test_machine_profiles_unknown_machine_raises(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        {
            "api_key": "a" * 32,
            "machines": {"office-pc": "b" * 32},
            "machine_profiles": {"typo-pc": {}},
        },
    )
    with pytest.raises(ConfigError, match="no matching entry in 'machines'"):
        load_settings(path)


def test_machine_profiles_unknown_key_raises(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        {
            "api_key": "a" * 32,
            "machines": {"office-pc": "b" * 32},
            "machine_profiles": {"office-pc": {"bogus_field": 1}},
        },
    )
    with pytest.raises(ConfigError, match="unknown key"):
        load_settings(path)


def test_machine_profiles_invalid_screenshot_raises(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        {
            "api_key": "a" * 32,
            "machines": {"office-pc": "b" * 32},
            "machine_profiles": {"office-pc": {"screenshot": {"format": "bmp"}}},
        },
    )
    with pytest.raises(ConfigError, match="screenshot"):
        load_settings(path)


def test_machine_profiles_invalid_log_level_raises(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        {
            "api_key": "a" * 32,
            "machines": {"office-pc": "b" * 32},
            "machine_profiles": {"office-pc": {"log_level": "SUPER_VERBOSE"}},
        },
    )
    with pytest.raises(ConfigError, match="log_level"):
        load_settings(path)


def test_mcp_profile_loads_from_file(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        {
            "api_key": "a" * 32,
            "machines": {"office-pc": "b" * 32},
            "mcp_profile": {"save_screenshots": True, "max_saved_screenshots": 50, "timeout": 5.0},
        },
    )
    settings = load_settings(path)
    assert settings.mcp_profile == {"save_screenshots": True, "max_saved_screenshots": 50, "timeout": 5.0}


def test_mcp_profile_unknown_key_raises(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        {"api_key": "a" * 32, "machines": {"office-pc": "b" * 32}, "mcp_profile": {"bogus_field": 1}},
    )
    with pytest.raises(ConfigError, match="unknown key"):
        load_settings(path)


def test_profiles_empty_by_default(tmp_path: Path) -> None:
    path = write_config(tmp_path, {"api_key": "a" * 32, "machines": {"office-pc": "b" * 32}})
    settings = load_settings(path)
    assert settings.machine_profiles == {}
    assert settings.mcp_profile == {}


def test_machine_profiles_load_from_env(monkeypatch) -> None:
    monkeypatch.setenv("JOURNEYDRIVE_BROKER_API_KEY", "a" * 32)
    monkeypatch.setenv("JOURNEYDRIVE_BROKER_MACHINES", json.dumps({"office-pc": "b" * 32}))
    monkeypatch.setenv(
        "JOURNEYDRIVE_BROKER_MACHINE_PROFILES", json.dumps({"office-pc": {"log_level": "WARNING"}})
    )
    monkeypatch.setenv("JOURNEYDRIVE_BROKER_MCP_PROFILE", json.dumps({"save_screenshots": True}))
    settings = load_settings()
    assert settings.machine_profiles == {"office-pc": {"log_level": "WARNING"}}
    assert settings.mcp_profile == {"save_screenshots": True}
