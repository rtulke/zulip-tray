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
python3 - <<'PYEOF'
import gi
for name, version in (("Gtk", "3.0"), ("AyatanaAppIndicator3", "0.1"), ("Notify", "0.7"), ("Secret", "1")):
    gi.require_version(name, version)
from gi.repository import Gio, GLib, Notify, Secret  # noqa: F401 -- display-free ones
import zulip_tray
from zulip_tray import config, i18n, keyring, notifications, zulip_api  # noqa: F401
print("version:", zulip_tray.__version__, "| language catalogs:", len(i18n._CATALOGS))
PYEOF
echo "verify-installed: OK"
