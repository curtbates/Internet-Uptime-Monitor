import json
import pytest
from pathlib import Path
import importlib


@pytest.fixture()
def cfg_file(tmp_path, monkeypatch):
    """Redirect CONFIG_FILE to a temp directory and reload config_manager."""
    import config_manager
    fake_path = tmp_path / "config.json"
    monkeypatch.setattr(config_manager, "CONFIG_FILE", fake_path)
    return fake_path


def test_load_config_defaults_when_missing(cfg_file):
    from config_manager import load_config, DEFAULT_CONFIG
    cfg = load_config()
    assert cfg["polling_interval_seconds"] == DEFAULT_CONFIG["polling_interval_seconds"]
    assert "dns_providers" in cfg


def test_load_config_reads_existing_file(cfg_file):
    from config_manager import load_config
    cfg_file.write_text(json.dumps({"polling_interval_seconds": 120}))
    cfg = load_config()
    assert cfg["polling_interval_seconds"] == 120


def test_load_config_back_fills_missing_keys(cfg_file):
    from config_manager import load_config
    # Write a minimal config missing newer keys like schema_version
    cfg_file.write_text(json.dumps({"polling_interval_seconds": 30}))
    cfg = load_config()
    # schema_version should be added from DEFAULT_CONFIG
    assert "schema_version" in cfg


def test_load_config_corrupt_file_returns_defaults(cfg_file):
    from config_manager import load_config, DEFAULT_CONFIG
    cfg_file.write_text("not valid json {{{{")
    cfg = load_config()
    assert cfg["polling_interval_seconds"] == DEFAULT_CONFIG["polling_interval_seconds"]


def test_save_and_reload_roundtrip(cfg_file):
    from config_manager import load_config, save_config
    original = load_config()
    original["polling_interval_seconds"] = 99
    save_config(original)
    reloaded = load_config()
    assert reloaded["polling_interval_seconds"] == 99


def test_schema_version_in_defaults():
    from config_manager import DEFAULT_CONFIG
    assert "schema_version" in DEFAULT_CONFIG
    assert isinstance(DEFAULT_CONFIG["schema_version"], int)
