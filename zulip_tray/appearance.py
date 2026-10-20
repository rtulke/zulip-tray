from __future__ import annotations

import logging
import os
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gio, Gtk  # noqa: E402

logger = logging.getLogger(__name__)

INTERFACE_SCHEMA = "org.gnome.desktop.interface"
# Compiled into GTK 3 itself, no directory on disk.
BUILTIN_THEMES = {"Adwaita", "Adwaita-dark", "HighContrast", "HighContrastInverse"}
DARK_SUFFIX = "-dark"


def gtk3_theme_exists(name: str) -> bool:
    if name in BUILTIN_THEMES:
        return True
    data_home = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    data_dirs = (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":")
    roots = [Path.home() / ".themes", data_home / "themes", *(Path(d) / "themes" for d in data_dirs if d)]
    return any((root / name / "gtk-3.0").is_dir() for root in roots)


def theme_for(mode: str, system_theme: str, system_dark: bool | None, exists=gtk3_theme_exists) -> tuple[str, bool]:
    """(gtk-theme-name, gtk-application-prefer-dark-theme) for a mode.

    prefer-dark alone is not enough: distro themes such as Ubuntu's Yaru
    come as separate "<name>-dark" themes, and those are dark whatever
    the flag says (confirmed on Ubuntu with Yaru-sage-dark). So "light"
    drops a -dark suffix and "dark" adds one, where that sibling exists."""
    is_dark_name = system_theme.endswith(DARK_SUFFIX)
    base = system_theme[: -len(DARK_SUFFIX)] if is_dark_name else system_theme
    if mode == "light":
        return (base if is_dark_name and exists(base) else system_theme), False
    if mode == "dark":
        sibling = base + DARK_SUFFIX
        return (sibling if not is_dark_name and exists(sibling) else system_theme), True
    return system_theme, bool(system_dark) if system_dark is not None else is_dark_name


def prefers_dark(color_scheme: str) -> bool:
    return color_scheme == "prefer-dark"


class ColorScheme:
    """Light/dark for our GTK 3 windows: GNOME's style switch ("system"),
    or forced light/dark from the settings dialog.

    Since GNOME 42 the style switch only sets color-scheme; libadwaita
    apps follow it, GTK 3 apps do not -- with Adwaita (Debian) dialogs
    would stay light in dark mode. Ubuntu's Yaru also swaps gtk-theme to
    a -dark variant; theme_for() handles that pairing.
    Everything else (accent and text colours, high contrast) comes from
    the theme, so the code itself sets no colours anywhere."""

    def __init__(self, mode: str = "system"):
        self._mode = mode
        self._gtk = Gtk.Settings.get_default()
        # The theme GTK picked up from the session, used when GNOME's own
        # gtk-theme key is not available to follow.
        self._initial_theme = self._gtk.get_property("gtk-theme-name") if self._gtk else "Adwaita"
        self._interface: Gio.Settings | None = None
        source = Gio.SettingsSchemaSource.get_default()
        schema = source.lookup(INTERFACE_SCHEMA, True) if source else None
        if schema is not None and schema.has_key("color-scheme"):
            # Kept as an attribute: the change notifications stop once the
            # Gio.Settings object is garbage-collected.
            self._interface = Gio.Settings.new(INTERFACE_SCHEMA)
            for key in ("color-scheme", "gtk-theme"):
                self._interface.connect(f"changed::{key}", lambda *_: self._apply())
        self._apply()

    @property
    def system_dark(self) -> bool | None:
        if self._interface is None:
            return None
        return prefers_dark(self._interface.get_string("color-scheme"))

    @property
    def system_theme(self) -> str:
        if self._interface is not None:
            return self._interface.get_string("gtk-theme") or self._initial_theme
        return self._initial_theme

    def set_mode(self, mode: str) -> None:
        self._mode = mode
        self._apply()

    def _apply(self) -> None:
        if self._gtk is None:
            return
        # Setting the properties ourselves detaches them from the session
        # (XSETTINGS/gsettings), which is why "system" sets them too:
        # back to the system's values, kept current by the change signals.
        theme, dark = theme_for(self._mode, self.system_theme, self.system_dark)
        if self._gtk.get_property("gtk-theme-name") != theme:
            self._gtk.set_property("gtk-theme-name", theme)
        self._gtk.set_property("gtk-application-prefer-dark-theme", dark)
        logger.debug("Farbschema %s: Theme %s, dunkel=%s", self._mode, theme, dark)
