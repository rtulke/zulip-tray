#!/usr/bin/env bash
# Post-install smoke test shared by build-local.sh and CI. No display or
# session bus in a container, so this stops short of starting the app: it
# proves the files are in place, every GI typelib the app needs resolves
# on this distro, and the display-free modules import.
set -euo pipefail
test -x /usr/bin/zulip-tray
test -x /usr/bin/zulip-tray-send
test -f /etc/zulip-tray/config.conf
# Autostart is opt-in per user (settings switch), never system-wide.
test ! -e /etc/xdg/autostart/zulip-tray.desktop
test -f /usr/share/applications/io.github.rtulke.ZulipTray.desktop
test -f /usr/share/icons/hicolor/scalable/apps/zulip-tray.svg
test -f /usr/lib/python3/dist-packages/zulip_tray/resources/sounds/notify.wav
# This script runs as root, so the import below can't notice a file only root
# may read; nfpm copies the source tree's modes, and one such file stops the
# app for every user (seen with a 0600 __init__.py after a version bump).
unreadable=$(find /usr/lib/python3/dist-packages/zulip_tray \( -type f ! -perm -o=r \) -o \( -type d ! -perm -o=rx \))
if [ -n "$unreadable" ]; then
    echo "verify-installed: not readable for all users:" >&2
    echo "$unreadable" >&2
    exit 1
fi
python3 - <<'PYEOF'
import gi
for name, version in (("Gtk", "3.0"), ("AyatanaAppIndicator3", "0.1"), ("Notify", "0.7"), ("Secret", "1")):
    gi.require_version(name, version)
from gi.repository import Gio, GLib, Notify, Secret  # noqa: F401 -- display-free ones
import zulip_tray
from zulip_tray import config, debug_log, i18n, keyring, notifications, zulip_api  # noqa: F401
languages = i18n.available_languages()
assert {"de", "en", "es", "fr", "gsw", "gsw_u_sd_chbe", "it"} <= set(languages), sorted(languages)
print("version:", zulip_tray.__version__, "| languages:", ", ".join(sorted(languages)))
PYEOF
echo "verify-installed: OK"
