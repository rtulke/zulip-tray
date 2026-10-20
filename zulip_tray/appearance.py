from __future__ import annotations

import logging

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gio, Gtk  # noqa: E402

logger = logging.getLogger(__name__)

INTERFACE_SCHEMA = "org.gnome.desktop.interface"


def prefers_dark(color_scheme: str) -> bool:
    return color_scheme == "prefer-dark"


def follow_system_color_scheme() -> Gio.Settings | None:
    """Make our GTK 3 windows follow GNOME's light/dark style.

    Since GNOME 42 the style switch only sets color-scheme; libadwaita
    apps follow it, GTK 3 apps do not -- with Adwaita (Debian) dialogs
    would stay light in dark mode. Ubuntu's Yaru also swaps gtk-theme to
    a -dark variant, where this is merely redundant. Everything else
    (accent and text colours, high contrast) comes from the theme, so the
    code itself sets no colours anywhere.

    Returns the Gio.Settings object, which the caller must keep alive for
    the change notifications to keep arriving."""
    source = Gio.SettingsSchemaSource.get_default()
    schema = source.lookup(INTERFACE_SCHEMA, True) if source else None
    if schema is None or not schema.has_key("color-scheme"):
        return None  # not GNOME, or older than 42: leave the theme alone
    gtk_settings = Gtk.Settings.get_default()
    if gtk_settings is None:
        return None
    interface = Gio.Settings.new(INTERFACE_SCHEMA)

    def apply(*_args) -> None:
        dark = prefers_dark(interface.get_string("color-scheme"))
        gtk_settings.set_property("gtk-application-prefer-dark-theme", dark)
        logger.debug("Farbschema: %s", "dunkel" if dark else "hell")

    interface.connect("changed::color-scheme", apply)
    apply()
    return interface
