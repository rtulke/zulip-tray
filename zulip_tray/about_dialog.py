from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GdkPixbuf, GLib, Gtk  # noqa: E402

from . import __version__
from .i18n import tr
from .resources import icon_path

APP_NAME = "Zulip Tray"
REPO_URL = "https://github.com/rtulke/zulip-tray/"


def show_about(parent=None) -> None:
    dialog = Gtk.AboutDialog(transient_for=parent, modal=False)
    dialog.set_program_name(APP_NAME)
    dialog.set_version(__version__)
    dialog.set_comments(tr("about.license_label", license="MIT License"))
    dialog.set_license_type(Gtk.License.MIT_X11)
    dialog.set_website(REPO_URL)
    dialog.set_website_label(REPO_URL)
    dialog.set_copyright("© 2026 Robert Tulke")
    try:
        dialog.set_logo(GdkPixbuf.Pixbuf.new_from_file_at_size(str(icon_path("online")), 64, 64))
    except GLib.Error:
        dialog.set_logo_icon_name("zulip-tray")
    dialog.connect("response", lambda d, _r: d.destroy())
    dialog.show()
