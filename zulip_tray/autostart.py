from __future__ import annotations

from pathlib import Path

# The package installs a system-wide autostart entry in
# /etc/xdg/autostart/zulip-tray.desktop, enabled for every user, so an
# admin doesn't configure each account. A per-user file of the same name
# under ~/.config/autostart takes priority (standard XDG search order) and
# with Hidden=true suppresses it without touching /etc -- the same
# mechanism GNOME's Tweaks/"Startup Applications" use, so both agree.
_OVERRIDE_PATH = Path.home() / ".config" / "autostart" / "zulip-tray.desktop"

_HIDDEN_ENTRY = """[Desktop Entry]
Type=Application
Name=Zulip Tray
Exec=/usr/bin/zulip-tray
Hidden=true
"""


def is_enabled() -> bool:
    if not _OVERRIDE_PATH.exists():
        return True
    try:
        return "Hidden=true" not in _OVERRIDE_PATH.read_text()
    except OSError:
        return True


def set_enabled(enabled: bool) -> None:
    if enabled:
        _OVERRIDE_PATH.unlink(missing_ok=True)
    else:
        _OVERRIDE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _OVERRIDE_PATH.write_text(_HIDDEN_ENTRY)
