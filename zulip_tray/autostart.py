from __future__ import annotations

from pathlib import Path

# Autostart is opt-in per user: the package ships no system-wide entry, so
# installing it on every desktop of a fleet does not push a sign-in dialog
# onto people who never use Zulip. The settings switch writes or removes a
# standard XDG entry under ~/.config/autostart -- the same file GNOME's
# Tweaks/"Startup Applications" manage, so both agree.
_OVERRIDE_PATH = Path.home() / ".config" / "autostart" / "zulip-tray.desktop"

_ENTRY = """[Desktop Entry]
Type=Application
Name=Zulip Tray
Comment=Zulip notification tray icon
Exec=/usr/bin/zulip-tray
Icon=zulip-tray
NoDisplay=true
X-GNOME-Autostart-enabled=true
"""


def is_enabled(path: Path | None = None) -> bool:
    path = path or _OVERRIDE_PATH
    try:
        text = path.read_text()
    except OSError:
        return False
    # Older versions wrote Hidden=true here to suppress the system entry;
    # GNOME's own switch writes X-GNOME-Autostart-enabled=false.
    return "Hidden=true" not in text and "X-GNOME-Autostart-enabled=false" not in text


def set_enabled(enabled: bool, path: Path | None = None) -> None:
    path = path or _OVERRIDE_PATH
    if enabled:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_ENTRY)
    else:
        path.unlink(missing_ok=True)
