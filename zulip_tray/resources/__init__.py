from pathlib import Path

_RESOURCES_DIR = Path(__file__).parent

# Own-presence states the tray icon can show. "invisible" is Zulip's
# presence_enabled=false ("invisible mode"); the other three are the
# server's aggregated presence (active/idle/offline) mapped to the same
# colour language rocketchat-tray used (green/orange/grey).
ICON_NAMES = ("online", "away", "offline", "invisible")
ICON_DIR = _RESOURCES_DIR / "icons"
SOUND_PATH = _RESOURCES_DIR / "sounds" / "notify.wav"


def icon_name(status: str, unread: bool = False) -> str:
    """Icon *name* (not path) as AppIndicator wants it: the file
    <ICON_DIR>/<name>.svg, resolved via set_icon_theme_path(ICON_DIR)."""
    name = f"zulip-tray-{status if status in ICON_NAMES else 'offline'}"
    return f"{name}-unread" if unread else name


def icon_path(status: str, unread: bool = False) -> Path:
    return ICON_DIR / f"{icon_name(status, unread)}.svg"
