from __future__ import annotations

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gdk, Gtk, Pango  # noqa: E402

from .contacts import Contact, ContactBook, format_last_message  # noqa: E402
from .i18n import tr  # noqa: E402

# Rows shown at once; with an empty query that is favorites + recent people
# first, so the list never renders the whole realm on every keystroke.
MAX_RESULTS = 50


class ContactSearchWindow(Gtk.Window):
    """Small type-to-filter window over every active user of the realm,
    opened from the tray menu (DBusMenu cannot host a text entry, so the
    search cannot live inside the menu itself). Enter or a click opens the
    DM, Esc closes; Up/Down move the selection while the entry keeps focus,
    so typing and choosing never need the mouse."""

    def __init__(self, book: ContactBook, favorites, on_open):
        super().__init__(title=tr("search.title"))
        self._book = book
        self._favorites = list(favorites)
        self._on_open = on_open  # (user_id, full_name)
        self._shown: list | None = None
        self.set_icon_name("zulip-tray")
        self.set_default_size(420, 460)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_type_hint(Gdk.WindowTypeHint.DIALOG)
        self.set_keep_above(True)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for side in ("top", "bottom", "start", "end"):
            getattr(box, f"set_margin_{side}")(12)
        self._entry = Gtk.SearchEntry(placeholder_text=tr("search.placeholder"))
        self._entry.connect("search-changed", lambda *_: self._refill())
        self._entry.connect("activate", lambda *_: self._open_selected())
        self._entry.connect("stop-search", lambda *_: self.destroy())
        self._entry.connect("key-press-event", self._on_entry_key)
        box.pack_start(self._entry, False, False, 0)

        self._list = Gtk.ListBox()
        self._list.set_selection_mode(Gtk.SelectionMode.BROWSE)
        self._list.set_activate_on_single_click(True)
        self._list.connect("row-activated", lambda _list, row: self._open(row))
        placeholder = Gtk.Label(label=tr("search.no_results"))
        placeholder.get_style_context().add_class("dim-label")
        placeholder.show()
        self._list.set_placeholder(placeholder)
        scrolled = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        scrolled.add(self._list)
        self._list.set_adjustment(scrolled.get_vadjustment())
        frame = Gtk.Frame()
        frame.add(scrolled)
        box.pack_start(frame, True, True, 0)
        self.add(box)

        # Esc also works while a row (not the entry) has the focus.
        self.connect("key-press-event", self._on_window_key)
        self._refill()
        self.show_all()
        self._entry.grab_focus()

    def set_book(self, book: ContactBook, favorites) -> None:
        """Fresh data (presence, new DMs) while the window is open; keeps
        the query and re-filters."""
        self._book, self._favorites = book, list(favorites)
        self._refill()

    def _refill(self) -> None:
        now = GLib.get_real_time() / 1_000_000
        matches = self._book.search(self._entry.get_text(), self._favorites, now)[:MAX_RESULTS]
        # The tray refreshes on every presence event; only rebuild the rows
        # when what they show changed, so the list does not jump while
        # someone scrolls through it.
        shown = [(c.user_id, c.full_name, c.status, c.unread, format_last_message(c.last_message_ts, now)) for c in matches]
        if shown == self._shown:
            return
        self._shown = shown
        selected = self._list.get_selected_row()
        selected_uid = selected.user_id if selected is not None else None
        for child in self._list.get_children():
            self._list.remove(child)
        for contact in matches:
            self._list.add(self._row(contact, now))
        self._list.show_all()
        rows = self._list.get_children()
        keep = next((row for row in rows if row.user_id == selected_uid), None)
        if rows:
            self._list.select_row(keep or rows[0])

    def _row(self, contact: Contact, now: float) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        row.user_id, row.full_name = contact.user_id, contact.full_name
        line = Gtk.Box(spacing=10)
        for side in ("top", "bottom"):
            getattr(line, f"set_margin_{side}")(6)
        for side in ("start", "end"):
            getattr(line, f"set_margin_{side}")(8)
        line.pack_start(Gtk.Label(label=contact.presence_mark), False, False, 0)
        name = Gtk.Label(label=f"{contact.full_name}  ({contact.unread})" if contact.unread else contact.full_name,
                         xalign=0)
        name.set_ellipsize(Pango.EllipsizeMode.END)
        line.pack_start(name, True, True, 0)
        when = format_last_message(contact.last_message_ts, now)
        if when:
            when_label = Gtk.Label(label=when)
            when_label.get_style_context().add_class("dim-label")
            line.pack_end(when_label, False, False, 0)
        row.add(line)
        return row

    def _on_entry_key(self, _entry, event) -> bool:
        step = {Gdk.KEY_Down: 1, Gdk.KEY_Up: -1}.get(event.keyval)
        if step is None:
            return False
        rows = self._list.get_children()
        if not rows:
            return True
        current = self._list.get_selected_row()
        index = rows.index(current) + step if current in rows else 0
        row = rows[max(0, min(index, len(rows) - 1))]
        self._list.select_row(row)
        # Keep the selected row visible inside the scrolled list.
        adjustment = self._list.get_adjustment()
        if adjustment is not None:
            allocation = row.get_allocation()
            adjustment.clamp_page(allocation.y, allocation.y + allocation.height)
        return True

    def _on_window_key(self, _window, event) -> bool:
        if event.keyval == Gdk.KEY_Escape:
            self.destroy()
            return True
        return False

    def _open_selected(self) -> None:
        row = self._list.get_selected_row()
        if row is not None:
            self._open(row)

    def _open(self, row) -> None:
        user_id, full_name = row.user_id, row.full_name
        self.destroy()
        self._on_open(user_id, full_name)
