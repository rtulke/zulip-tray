from zulip_tray import autostart


def test_off_without_user_entry(tmp_path):
    assert not autostart.is_enabled(tmp_path / "zulip-tray.desktop")


def test_switch_writes_and_removes_user_entry(tmp_path):
    path = tmp_path / "autostart" / "zulip-tray.desktop"
    autostart.set_enabled(True, path)
    assert autostart.is_enabled(path)
    assert "Exec=/usr/bin/zulip-tray" in path.read_text()
    autostart.set_enabled(False, path)
    assert not path.exists()
    assert not autostart.is_enabled(path)


def test_old_hidden_override_counts_as_off(tmp_path):
    path = tmp_path / "zulip-tray.desktop"
    path.write_text("[Desktop Entry]\nType=Application\nExec=/usr/bin/zulip-tray\nHidden=true\n")
    assert not autostart.is_enabled(path)
    autostart.set_enabled(True, path)
    assert autostart.is_enabled(path)


def test_gnome_disabled_entry_counts_as_off(tmp_path):
    path = tmp_path / "zulip-tray.desktop"
    path.write_text("[Desktop Entry]\nExec=/usr/bin/zulip-tray\nX-GNOME-Autostart-enabled=false\n")
    assert not autostart.is_enabled(path)
