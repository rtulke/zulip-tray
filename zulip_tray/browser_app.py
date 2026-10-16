from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import requests

logger = logging.getLogger(__name__)

# Fixed, dedicated port + profile for the app-mode browser instance this app
# launches and controls -- deliberately NOT the user's regular Chrome
# profile, both so we don't interfere with their browsing and because a
# second process pointed at an already-running undebugged profile mostly
# ignores its command-line flags (including --remote-debugging-port).
DEBUG_PORT = 9446
PROFILE_DIR = Path.home() / ".config" / "zulip-tray" / "chrome-app-profile"
_HTTP_TIMEOUT = 2.0


def _cdp_get(path: str) -> object | None:
    try:
        response = requests.get(f"http://127.0.0.1:{DEBUG_PORT}{path}", timeout=_HTTP_TIMEOUT)
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.debug("CDP GET %s fehlgeschlagen: %s", path, exc)
        return None


def _cdp_mutate(path: str) -> bool:
    # /json/new and /json/activate/* require PUT since Chrome 111 (a bare
    # GET was a CSRF vector); a 405 means an older browser expecting GET.
    try:
        response = requests.put(f"http://127.0.0.1:{DEBUG_PORT}{path}", timeout=_HTTP_TIMEOUT)
        if response.status_code == 405:
            response = requests.get(f"http://127.0.0.1:{DEBUG_PORT}{path}", timeout=_HTTP_TIMEOUT)
        if not response.ok:
            logger.info("CDP-Aufruf %s -> HTTP %s: %s", path, response.status_code, response.text[:200])
        return response.ok
    except requests.RequestException as exc:
        logger.info("CDP-Aufruf %s fehlgeschlagen: %s", path, exc)
        return False


def _find_matching_target(origin: str) -> dict | None:
    targets = _cdp_get("/json/list")
    if not targets:
        return None
    pages = [t for t in targets if t.get("type") == "page"]
    for target in pages:
        if target.get("url", "").startswith(origin):
            return target
    # Our dedicated profile only ever hosts one site, so any leftover page
    # target (e.g. a transient about:blank) is still the window to reuse.
    return pages[0] if pages else None


def _navigate_target(target: dict, url: str) -> bool:
    ws_url = target.get("webSocketDebuggerUrl")
    if not ws_url:
        return False
    try:
        import websocket  # optional dependency (python3-websocket); only needed for app mode
    except ImportError:
        logger.info("python3-websocket fehlt, Fenster-Wiederverwendung per CDP nicht moeglich")
        return False
    try:
        # Chrome 111+ rejects CDP WebSocket connections carrying a
        # browser-style Origin header unless allow-listed; suppress_origin
        # skips it, the same way Puppeteer/Selenium connect.
        ws = websocket.create_connection(ws_url, timeout=_HTTP_TIMEOUT, suppress_origin=True)
        try:
            ws.send(json.dumps({"id": 1, "method": "Page.navigate", "params": {"url": url}}))
            ws.recv()
        finally:
            ws.close()
        return True
    except (OSError, ValueError, websocket.WebSocketException) as exc:
        logger.info("Page.navigate ueber %s fehlgeschlagen: %s", ws_url, exc)
        return False


def _launch_new_instance(browser_path: str, url: str) -> bool:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    args = [
        browser_path,
        f"--app={url}",
        f"--remote-debugging-port={DEBUG_PORT}",
        f"--user-data-dir={PROFILE_DIR}",
    ]
    logger.info("Starte neue Browser-Instanz: %s", " ".join(args))
    try:
        subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as exc:
        logger.warning("Browser-Start fehlgeschlagen: %s", exc)
        return False
    return True


def open_in_app_window(browser_path: str, url: str) -> bool:
    """Opens `url` in a dedicated, isolated Chromium profile, reusing and
    navigating an already-open window from a previous call instead of
    spawning a new one. Chrome only auto-reuses windows for *installed*
    PWAs, not ad-hoc --app invocations, so the reuse is driven here via
    the DevTools protocol on the debugging port."""
    parts = urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}"

    if _cdp_get("/json/version") is not None:
        target = _find_matching_target(origin)
        if target and _navigate_target(target, url) and _cdp_mutate(f"/json/activate/{target['id']}"):
            return True
        # Instance running but reuse failed -- still avoid a second
        # *process*, just open a fresh tab in the one that's running.
        return _cdp_mutate(f"/json/new?{url}")

    return _launch_new_instance(browser_path, url)
