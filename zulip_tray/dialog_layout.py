from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

# Space around a dialog's Cancel/OK row: Gtk.Dialog's own action area has
# none, which puts the buttons flush into the window's bottom-right corner.
BUTTON_ROW_MARGIN = 12


def pad_button_row(button: Gtk.Widget) -> None:
    """Give the button row a dialog button sits in (the action area, via
    the button's parent -- Gtk.Dialog.get_action_area is deprecated)
    breathing room and spacing."""
    row = button.get_parent()
    if row is None:
        return
    for side in ("start", "end", "bottom"):
        getattr(row, f"set_margin_{side}")(BUTTON_ROW_MARGIN)
    row.set_margin_top(BUTTON_ROW_MARGIN // 2)
    if isinstance(row, Gtk.Box):
        row.set_spacing(8)
