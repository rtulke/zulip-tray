import json

import pytest

from zulip_tray import config


def test_admin_config_load(tmp_path, monkeypatch):
    path = tmp_path / "config.conf"
    path.write_text("[server]\nurl = https://chat.example.com/\nverify_ssl = false\nemail_domain = @example.com\n")
    cfg = config.AdminConfig.load(path)
    assert cfg.server_url == "https://chat.example.com" and cfg.verify_ssl is False and cfg.email_domain == "example.com"
    monkeypatch.setenv("USER", "alice")
    assert cfg.default_email() == "alice@example.com"
    assert config.AdminConfig("https://x").default_email() == ""


def test_admin_config_errors(tmp_path):
    with pytest.raises(config.ConfigError):
        config.AdminConfig.load(tmp_path / "missing.conf")
    bad = tmp_path / "bad.conf"
    bad.write_text("[server]\nurl = https://REPLACE-ME.example.com\n")
    with pytest.raises(config.ConfigError):
        config.AdminConfig.load(bad)


def test_user_settings_roundtrip_and_validation(tmp_path):
    path = tmp_path / "settings.json"
    s = config.UserSettings.load(path)
    assert s.forced_status == "auto" and s.email == ""
    s.forced_status = "invisible"
    s.idle_threshold_minutes = 23  # snaps to 25
    s.email = " Alice@example.com "
    s.sound_volume = 1.7
    s.save()
    again = config.UserSettings.load(path)
    assert again.forced_status == "invisible" and again.idle_threshold_minutes == 25
    assert again.email == "Alice@example.com" and again.sound_volume == 1.0
    with pytest.raises(ValueError):
        s.forced_status = "busy"
    # unknown status in a hand-edited file falls back to auto
    data = json.loads(path.read_text())
    data["presence"]["forced_status"] = "busy"
    path.write_text(json.dumps(data))
    assert config.UserSettings.load(path).forced_status == "auto"


def test_effective_admin_config_prefers_user_override(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_CONFIG_PATH", tmp_path / "none.conf")
    monkeypatch.setattr(config.AdminConfig, "load", classmethod(lambda cls, path=None: (_ for _ in ()).throw(config.ConfigError("x"))))
    s = config.UserSettings.load(tmp_path / "s.json")
    with pytest.raises(config.ConfigError):
        config.effective_admin_config(s)
    s.server_url_override = "https://other.example"
    assert config.effective_admin_config(s).server_url == "https://other.example"
