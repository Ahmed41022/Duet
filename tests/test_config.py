from pathlib import Path

import pytest

from duet.core.config import Config, load_config


def test_defaults():
    assert load_config() == Config(Path.home() / "Duet" / "Received", 900, "INFO")


def test_explicit_toml_config(tmp_path, monkeypatch):
    settings_dir = tmp_path / "settings"
    settings_dir.mkdir()
    config = settings_dir / "config.toml"
    config.write_text(
        'received_directory = "ملفات"\n'
        "idle_timeout_seconds = 120\n"
        'log_level = "debug"\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    assert load_config(config) == Config(settings_dir / "ملفات", 120, "DEBUG")


@pytest.mark.parametrize(
    ("contents", "message"),
    [
        ("extra = 1", "Unknown config keys: extra"),
        ("received_directory = 3", "received_directory must be"),
        ("idle_timeout_seconds = true", "idle_timeout_seconds must be"),
        ("idle_timeout_seconds = 0", "idle_timeout_seconds must be"),
        ('log_level = "verbose"', "log_level must be"),
        ("log_level = 1", "log_level must be"),
    ],
)
def test_invalid_config(tmp_path, contents, message):
    config = tmp_path / "config.toml"
    config.write_text(contents, encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_config(config)
