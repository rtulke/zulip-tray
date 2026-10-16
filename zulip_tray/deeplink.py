from __future__ import annotations

import logging
import shutil

from gi.repository import Gio

from . import browser_app
from .mainloop import run_on_main, spawn

logger = logging.getLogger(__name__)

# Checked in this order via shutil.which(). xdg-open/Gio has no concept of
# "reuse an existing window for this URL"; Chromium-family browsers can
# provide that via browser_app.open_in_app_window() (plain --app=URL alone
# does NOT reuse, only installed PWAs get that from Chrome). Stable Firefox
# has no equivalent, so without a Chromium binary on PATH this falls back
# to the default-browser behaviour.
_CHROMIUM_BROWSER_CANDIDATES = (
    "google-chrome",
    "google-chrome-stable",
    "chrome",
    "chromium",
    "chromium-browser",
    "brave-browser",
    "microsoft-edge",
)


def find_chromium_browser() -> str | None:
    for name in _CHROMIUM_BROWSER_CANDIDATES:
        path = shutil.which(name)
        if path:
            return path
    return None


def build_url(server_url: str, fragment: str | None) -> str:
    """Web-client URL for a conversation fragment (see
    notifications.stream_fragment/dm_fragment), or the realm's home."""
    base = server_url.rstrip("/")
    return f"{base}/#{fragment}" if fragment else f"{base}/"


def open_url_default(url: str) -> None:
    """Default browser via GIO (the freedesktop URL-open protocol, same as
    xdg-open). Main thread only."""
    try:
        Gio.AppInfo.launch_default_for_uri(url, None)
    except Exception as exc:  # noqa: BLE001 - GLib.Error, no handler, ...
        logger.warning("Konnte %s nicht oeffnen: %s", url, exc)


class RoomOpener:
    """Opens a conversation (or the home view) in the web client. In app
    mode the Chromium DevTools calls block for up to a couple of seconds,
    so they run on a throwaway thread and only the GIO fallback hops back
    onto the main loop."""

    def open(self, server_url: str, fragment: str | None, app_mode: bool = False) -> None:
        url = build_url(server_url, fragment)
        if not app_mode:
            open_url_default(url)
            return

        def worker() -> None:
            browser = find_chromium_browser()
            if browser and browser_app.open_in_app_window(browser, url):
                return
            if not browser:
                logger.info("Kein Chromium-basierter Browser gefunden, verwende Standard-Browser")
            else:
                logger.warning("Konnte %s nicht im App-Modus oeffnen, verwende Standard-Browser", browser)
            run_on_main(open_url_default, url)

        spawn(worker, name="room-opener")
