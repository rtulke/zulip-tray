#!/bin/sh
set -e

# Byte-compile like dh_python3 would, so startup doesn't re-parse every
# module (the package directory isn't writable for users).
if command -v py3compile >/dev/null 2>&1; then
    py3compile -p zulip-tray /usr/lib/python3/dist-packages/zulip_tray || true
fi
# Register the zulip:// scheme handler and the app icon with the desktop.
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database -q /usr/share/applications || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor 2>/dev/null || true
fi

echo "zulip-tray: Bitte /etc/zulip-tray/config.conf mit der Server-URL konfigurieren (oder das die Nutzer selbst in den App-Einstellungen tun lassen)."
echo "zulip-tray: Falls das Tray-Icon unter GNOME nicht erscheint, die Erweiterung 'AppIndicator and KStatusNotifierItem Support' aktivieren (gnome-extensions enable appindicatorsupport@rgcjonas.gmail.com)."

# Ask every already-running instance (one per logged-in user) to relaunch
# itself with the just-installed version. The app is a GApplication, so
# this is a D-Bus action on each user's session bus -- reached from root
# via the user's bus socket under /run/user/<uid>/. Silent no-op where
# nothing is listening.
for dir in /run/user/*; do
    [ -S "$dir/bus" ] || continue
    uid="${dir##*/}"
    user="$(id -nu "$uid" 2>/dev/null)" || continue
    runuser -u "$user" -- env DBUS_SESSION_BUS_ADDRESS="unix:path=$dir/bus" \
        gapplication action io.github.rtulke.ZulipTray restart >/dev/null 2>&1 || true
done

exit 0
