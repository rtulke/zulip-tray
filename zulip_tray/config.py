from __future__ import annotations

import configparser
import copy
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from .sounds import DEFAULT_CHOICE as DEFAULT_SOUND_CHOICE

logger = logging.getLogger(__name__)

ADMIN_CONFIG_PATH = Path("/etc/zulip-tray/config.conf")
USER_SETTINGS_PATH = Path.home() / ".config" / "zulip-tray" / "settings.json"

# "auto": active/idle follows the idle watcher. "online"/"away": always
# report active/idle regardless of real activity. "invisible": Zulip's
# presence_enabled=false (others see you as offline). Zulip has no "busy".
STATUS_OPTIONS = ("auto", "online", "away", "invisible")

# How far back the direct-messages menu lists recent conversations below
# the favorites (see contacts.bucket_in_range).
RECENT_RANGES = ("off", "today", "yesterday", "week", "all")

# Which notifications get the Reply button: none, direct messages only,
# or channel messages too (a channel reply is public, hence not default).
REPLY_BUTTON_OPTIONS = ("off", "direct", "all")
# What a click on the notification itself does where a Reply button is on
# offer: open the web chat, or the answer window. GNOME 46 shows action
# buttons only once a notification is expanded, the click works always.
CLICK_ACTIONS = ("open", "reply")

# Minutes after the newest unread notification until the icon stops
# blinking and keeps the unread badge steady; 0 = blink until read.
BLINK_STOP_MINUTES = (0, 1, 5, 15, 60)

# Light/dark choice for the tray's own windows and for the Zulip web chat:
# "system" follows GNOME's style switch.
COLOR_SCHEMES = ("system", "light", "dark")

DEFAULT_SETTINGS = {
    "notifications": {
        "blink_enabled": True,
        "blink_stop_minutes": 0,  # BLINK_STOP_MINUTES
        "sound_enabled": False,
        "sound_volume": 0.7,
        "sound_choice": DEFAULT_SOUND_CHOICE,
        "tooltip_enabled": True,
        # One of REPLY_BUTTON_OPTIONS. "direct" by default: the answer
        # window shows one message without its thread, and a channel reply
        # is public.
        "reply_button": "direct",
        "click_action": "open",  # CLICK_ACTIONS
    },
    "presence": {
        "forced_status": "auto",
        "idle_detection_enabled": True,
        "idle_threshold_minutes": 5,
        "status_message": "",
    },
    "server": {
        "url_override": "",
        "verify_ssl_override": True,
    },
    "account": {
        # Zulip identifies users by e-mail, not by the OS login name. Filled
        # by the login flow (the OIDC redirect carries the address); the
        # API key itself lives in the keyring (see auth.py).
        "email": "",
    },
    "browser": {
        "app_mode_enabled": False,
    },
    "appearance": {
        "window_color_scheme": "system",
    },
    "contacts": {
        # Zulip user ids pinned at the top of the tray's contacts menu.
        # Local to this machine: Zulip itself has no favorite users.
        "favorites": [],
        # contacts.RECENT_RANGES: how far back recent DM partners show up
        # below the favorites.
        "recent_range": "week",
    },
}


class ConfigError(Exception):
    """Raised when the admin-provisioned configuration is missing or invalid."""


@dataclass
class AdminConfig:
    """Fleet-wide defaults from /etc/zulip-tray/config.conf. Not immutable
    at runtime: a per-user server override from the Settings dialog
    mutates this instance in place (see main.update_server), because the
    worker, presence coordinator and room opener all hold a reference to
    the same object and read it fresh on every use."""

    server_url: str
    verify_ssl: bool = True
    # Suggested domain for the login dialog's e-mail prefill
    # (<os-user>@<email_domain>); empty = no prefill.
    email_domain: str = ""

    @classmethod
    def load(cls, path: Path = ADMIN_CONFIG_PATH) -> "AdminConfig":
        if not path.exists():
            raise ConfigError(f"Admin-Konfiguration nicht gefunden: {path}")
        parser = configparser.ConfigParser()
        try:
            parser.read(path)
            server_url = parser.get("server", "url")
        except (configparser.Error, KeyError) as exc:
            raise ConfigError(f"Ungueltige Admin-Konfiguration in {path}: {exc}") from exc
        if not server_url or "REPLACE-ME" in server_url:
            raise ConfigError(
                f"{path} enthaelt noch eine Platzhalter-Server-URL; bitte vom Admin konfigurieren lassen"
            )
        verify_ssl = parser.getboolean("server", "verify_ssl", fallback=True)
        email_domain = parser.get("server", "email_domain", fallback="").strip().lstrip("@")
        return cls(server_url=server_url.rstrip("/"), verify_ssl=verify_ssl, email_domain=email_domain)

    def default_email(self) -> str:
        """<os-user>@<email_domain>, for deployments where the OS login name
        matches the local part of the Zulip address; empty if no domain is
        configured. Only a prefill -- the login dialog lets the user edit it."""
        if not self.email_domain:
            return ""
        user = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
        return f"{user}@{self.email_domain}" if user else ""


class UserSettings:
    def __init__(self, data: dict, path: Path = USER_SETTINGS_PATH):
        self._path = path
        self._data = data

    @classmethod
    def load(cls, path: Path = USER_SETTINGS_PATH) -> "UserSettings":
        data = copy.deepcopy(DEFAULT_SETTINGS)
        if path.exists():
            try:
                loaded = json.loads(path.read_text())
                for section in data:
                    data[section].update(loaded.get(section, {}))
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Konnte %s nicht lesen, verwende Standardwerte: %s", path, exc)
        settings = cls(data, path=path)
        if settings.forced_status not in STATUS_OPTIONS:
            settings.forced_status = "auto"
        return settings

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(self._data, indent=2))
        os.replace(tmp_path, self._path)

    # --- notifications -------------------------------------------------------

    @property
    def blink_enabled(self) -> bool:
        return self._data["notifications"]["blink_enabled"]

    @blink_enabled.setter
    def blink_enabled(self, value: bool) -> None:
        self._data["notifications"]["blink_enabled"] = value

    @property
    def blink_stop_minutes(self) -> int:
        value = self._data["notifications"].get("blink_stop_minutes")
        return value if value in BLINK_STOP_MINUTES else 0

    @blink_stop_minutes.setter
    def blink_stop_minutes(self, value: int) -> None:
        self._data["notifications"]["blink_stop_minutes"] = value if value in BLINK_STOP_MINUTES else 0

    @property
    def sound_enabled(self) -> bool:
        return self._data["notifications"]["sound_enabled"]

    @sound_enabled.setter
    def sound_enabled(self, value: bool) -> None:
        self._data["notifications"]["sound_enabled"] = value

    @property
    def sound_volume(self) -> float:
        return self._data["notifications"]["sound_volume"]

    @sound_volume.setter
    def sound_volume(self, value: float) -> None:
        self._data["notifications"]["sound_volume"] = max(0.0, min(1.0, value))

    @property
    def sound_choice(self) -> str:
        return self._data["notifications"]["sound_choice"]

    @sound_choice.setter
    def sound_choice(self, value: str) -> None:
        self._data["notifications"]["sound_choice"] = value

    @property
    def tooltip_enabled(self) -> bool:
        return self._data["notifications"]["tooltip_enabled"]

    @tooltip_enabled.setter
    def tooltip_enabled(self, value: bool) -> None:
        self._data["notifications"]["tooltip_enabled"] = value

    @property
    def reply_button(self) -> str:
        notifications = self._data["notifications"]
        value = notifications.get("reply_button")
        if notifications.get("reply_in_channels") is True and value == "direct":
            value = "all"  # 0.4.2 stored a boolean switch instead
        return value if value in REPLY_BUTTON_OPTIONS else "direct"

    @property
    def click_action(self) -> str:
        value = self._data["notifications"].get("click_action")
        return value if value in CLICK_ACTIONS else "open"

    @click_action.setter
    def click_action(self, value: str) -> None:
        self._data["notifications"]["click_action"] = value if value in CLICK_ACTIONS else "open"

    @reply_button.setter
    def reply_button(self, value: str) -> None:
        self._data["notifications"]["reply_button"] = value if value in REPLY_BUTTON_OPTIONS else "direct"
        self._data["notifications"].pop("reply_in_channels", None)

    # --- presence --------------------------------------------------------------

    @property
    def forced_status(self) -> str:
        return self._data["presence"]["forced_status"]

    @forced_status.setter
    def forced_status(self, value: str) -> None:
        if value not in STATUS_OPTIONS:
            raise ValueError(f"Unbekannter Status: {value!r}")
        self._data["presence"]["forced_status"] = value

    @property
    def idle_detection_enabled(self) -> bool:
        return self._data["presence"]["idle_detection_enabled"]

    @idle_detection_enabled.setter
    def idle_detection_enabled(self, value: bool) -> None:
        self._data["presence"]["idle_detection_enabled"] = value

    @property
    def idle_threshold_minutes(self) -> int:
        return self._data["presence"]["idle_threshold_minutes"]

    @idle_threshold_minutes.setter
    def idle_threshold_minutes(self, value: int) -> None:
        # Clamped to the slider's [5, 60] range and snapped to 5-minute
        # steps, so a hand-edited settings.json can't produce odd values.
        snapped = round(value / 5) * 5
        self._data["presence"]["idle_threshold_minutes"] = max(5, min(60, snapped))

    @property
    def status_message(self) -> str:
        return self._data["presence"]["status_message"]

    @status_message.setter
    def status_message(self, value: str) -> None:
        self._data["presence"]["status_message"] = value

    # --- server / account ------------------------------------------------------

    @property
    def server_url_override(self) -> str:
        """Empty string means "use the admin-provisioned server_url"."""
        return self._data["server"]["url_override"]

    @server_url_override.setter
    def server_url_override(self, value: str) -> None:
        self._data["server"]["url_override"] = value

    @property
    def verify_ssl_override(self) -> bool:
        return self._data["server"]["verify_ssl_override"]

    @verify_ssl_override.setter
    def verify_ssl_override(self, value: bool) -> None:
        self._data["server"]["verify_ssl_override"] = value

    @property
    def email(self) -> str:
        return self._data["account"]["email"]

    @email.setter
    def email(self, value: str) -> None:
        self._data["account"]["email"] = value.strip()

    # --- browser ---------------------------------------------------------------

    @property
    def app_mode_enabled(self) -> bool:
        """Open the web client in a dedicated Chromium --app window (reused
        across clicks) instead of a tab in the default browser. Silently
        falls back to the default browser when no Chromium-family browser
        is installed -- see deeplink.find_chromium_browser()."""
        return self._data["browser"]["app_mode_enabled"]

    @app_mode_enabled.setter
    def app_mode_enabled(self, value: bool) -> None:
        self._data["browser"]["app_mode_enabled"] = value

    # --- appearance ------------------------------------------------------------

    @property
    def window_color_scheme(self) -> str:
        """Light/dark for the tray's own windows (settings, login, search);
        one of COLOR_SCHEMES. The web chat's colours are a Zulip account
        setting instead (zulip_api.set_color_scheme)."""
        value = self._data["appearance"].get("window_color_scheme")
        return value if value in COLOR_SCHEMES else "system"

    @window_color_scheme.setter
    def window_color_scheme(self, value: str) -> None:
        self._data["appearance"]["window_color_scheme"] = value if value in COLOR_SCHEMES else "system"

    # --- contacts --------------------------------------------------------------

    @property
    def favorite_user_ids(self) -> list[int]:
        # Tolerates a hand-edited file: anything that isn't an int is dropped.
        return [uid for uid in self._data["contacts"]["favorites"] if isinstance(uid, int) and not isinstance(uid, bool)]

    @favorite_user_ids.setter
    def favorite_user_ids(self, value: list[int]) -> None:
        self._data["contacts"]["favorites"] = list(dict.fromkeys(int(uid) for uid in value))

    @property
    def recent_range(self) -> str:
        value = self._data["contacts"].get("recent_range")
        return value if value in RECENT_RANGES else "week"

    @recent_range.setter
    def recent_range(self, value: str) -> None:
        self._data["contacts"]["recent_range"] = value if value in RECENT_RANGES else "week"


def effective_admin_config(settings: UserSettings) -> AdminConfig:
    """AdminConfig with the user's own server override applied, or built
    from the override alone when /etc has no usable config. Raises
    ConfigError only if neither source yields a server URL."""
    try:
        admin_config = AdminConfig.load()
    except ConfigError:
        if not settings.server_url_override:
            raise
        return AdminConfig(server_url=settings.server_url_override, verify_ssl=settings.verify_ssl_override)
    if settings.server_url_override:
        admin_config.server_url = settings.server_url_override
        admin_config.verify_ssl = settings.verify_ssl_override
    return admin_config
