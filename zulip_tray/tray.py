from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum, auto

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
from gi.repository import AyatanaAppIndicator3 as AppIndicator, GLib, Gtk  # noqa: E402

from .config import STATUS_OPTIONS
from .contact_search import ContactSearchWindow
from .dialog_layout import pad_button_row
from .contacts import (
    RECENCY_BUCKETS, RECENT_LIMIT, Contact, ContactBook, bucket_in_range, format_last_message, recency_bucket,
)
from .i18n import tr
from .notifications import Notification, UnreadSnapshot, dm_fragment
from .resources import ICON_DIR, icon_name

logger = logging.getLogger(__name__)

BLINK_INTERVAL_MS = 600
# Presence of others goes stale without any event (offline = no ping for
# 140 s), and "today" turns into "yesterday" at midnight -- so the
# contacts menu is re-evaluated on a timer too, not only on events.
CONTACTS_REFRESH_SECONDS = 60
RECENCY_HEADER_KEYS = {
    "today": "tray.contacts_today",
    "yesterday": "tray.contacts_yesterday",
    "week": "tray.contacts_last_week",
    "older": "tray.contacts_older",
}
INDICATOR_ID = "zulip-tray"

STATUS_LABEL_KEYS = {
    "auto": "tray.status_auto",
    "online": "tray.status_online",
    "away": "tray.status_away",
    "invisible": "tray.status_invisible",
}



class ConnectionState(Enum):
    DISCONNECTED = auto()
    CONNECTING = auto()
    CONNECTED = auto()
    AUTH_ERROR = auto()


@dataclass
class _Unread:
    label: str
    fragment: str
    message_ids: set[int] = field(default_factory=set)


def _defer(fn, *args) -> None:
    """Run fn on the next main-loop iteration instead of inline from the
    menu item's activate handler: tray menus go through DBusMenu, and
    gnome-shell keeps the busy cursor until the click round-trip is
    acknowledged, which only happens once our handler returns."""
    GLib.idle_add(lambda: (fn(*args), False)[1])


class TrayController:
    """Owns the AppIndicator (StatusNotifierItem over D-Bus, rendered by
    GNOME's AppIndicator extension) and its menu. Icon colour = own
    presence as the server sees it; a red badge blinks while there are
    unread notifications; the "missed messages" submenu lists them per
    conversation and jumps into that conversation in the web client."""

    def __init__(
        self, settings, on_open_chat, on_open_conversation, on_open_settings, on_reenter_login,
        on_set_status, on_set_status_message, on_show_about, on_quit, on_mark_read,
    ):
        self._settings = settings
        self._on_mark_read = on_mark_read  # (message_ids) -> marks them read on the server
        self._on_open_chat = on_open_chat
        self._on_open_conversation = on_open_conversation  # (fragment)
        self._on_set_status = on_set_status
        self._on_set_status_message = on_set_status_message
        self._on_open_settings = on_open_settings  # (page name or None)
        self._state = ConnectionState.DISCONNECTED
        self._presence_status = "offline"
        self._unread: dict[str, _Unread] = {}  # conversation key -> entry, insertion-ordered
        self._blink_visible = False
        self._blink_timer: int | None = None
        # After settings.blink_stop_minutes without a new notification the
        # blinking settles into a steady unread badge.
        self._blink_settled = False
        self._settle_timer: int | None = None
        self._email = ""
        self._updating_menu = False
        self._contacts: ContactBook | None = None
        self._contacts_rendered: list | None = None
        self._search_window: ContactSearchWindow | None = None

        self._indicator = AppIndicator.Indicator.new(
            INDICATOR_ID, icon_name("offline"), AppIndicator.IndicatorCategory.APPLICATION_STATUS,
        )
        # Our status icons live inside the Python package, not in the
        # system icon theme -- point the indicator's own theme path there.
        self._indicator.set_icon_theme_path(str(ICON_DIR))
        self._indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)

        menu = Gtk.Menu()
        self._status_item = Gtk.MenuItem(label=tr("tray.disconnected"))
        self._status_item.set_sensitive(False)
        menu.append(self._status_item)

        self._missed_item = Gtk.MenuItem(label=tr("tray.missed_messages_submenu"))
        self._missed_menu = Gtk.Menu()
        self._missed_item.set_submenu(self._missed_menu)
        self._mark_all_read_item = Gtk.MenuItem(label=tr("tray.mark_all_read"))
        self._mark_all_read_item.connect("activate", lambda *_: _defer(self.mark_all_read))
        menu.append(self._missed_item)

        open_chat_item = Gtk.MenuItem(label=tr("tray.open_chat"))
        open_chat_item.connect("activate", lambda *_: _defer(self._handle_open_chat))
        menu.append(open_chat_item)

        status_item = Gtk.MenuItem(label=tr("tray.status_submenu"))
        status_item.set_submenu(self._build_status_menu(settings.forced_status))
        menu.append(status_item)

        self._contacts_item = Gtk.MenuItem(label=tr("tray.contacts_submenu"))
        self._contacts_menu = Gtk.Menu()
        self._contacts_item.set_submenu(self._contacts_menu)
        menu.append(self._contacts_item)
        # DBusMenu cannot host a text entry, so the search opens a window.
        self._search_item = Gtk.MenuItem(label=tr("tray.contacts_search"))
        self._search_item.connect("activate", lambda *_: _defer(self.open_contact_search))
        menu.append(self._search_item)

        menu.append(Gtk.SeparatorMenuItem())
        about_item = Gtk.MenuItem(label=tr("tray.about_action"))
        about_item.connect("activate", lambda *_: _defer(on_show_about))
        menu.append(about_item)
        status_message_item = Gtk.MenuItem(label=tr("tray.status_message_action"))
        status_message_item.connect("activate", lambda *_: _defer(self._handle_set_status_message))
        menu.append(status_message_item)

        menu.append(Gtk.SeparatorMenuItem())
        settings_item = Gtk.MenuItem(label=tr("tray.settings_action"))
        settings_item.connect("activate", lambda *_: _defer(on_open_settings, None))
        menu.append(settings_item)
        self._reenter_login_item = Gtk.MenuItem(label=tr("tray.reenter_login_action"))
        self._reenter_login_item.connect("activate", lambda *_: _defer(on_reenter_login))
        menu.append(self._reenter_login_item)

        menu.append(Gtk.SeparatorMenuItem())
        quit_item = Gtk.MenuItem(label=tr("tray.quit_action"))
        quit_item.connect("activate", lambda *_: _defer(on_quit))
        menu.append(quit_item)

        menu.show_all()
        self._missed_item.hide()
        self._contacts_item.hide()
        self._search_item.hide()
        self._reenter_login_item.hide()
        self._indicator.set_menu(menu)
        self._menu = menu  # keep a reference alive
        # Middle click on the indicator: direct "open chat" shortcut where
        # the host renders one (GNOME's extension opens the menu on any click).
        self._indicator.set_secondary_activate_target(open_chat_item)

        self._update_status_text(tr("tray.disconnected"))
        GLib.timeout_add_seconds(CONTACTS_REFRESH_SECONDS, lambda: (self.refresh_contacts(), True)[1])

    def _build_status_menu(self, current_status: str) -> Gtk.Menu:
        menu = Gtk.Menu()
        self._status_items: dict[str, Gtk.RadioMenuItem] = {}
        group_item: Gtk.RadioMenuItem | None = None
        for value in STATUS_OPTIONS:
            item = Gtk.RadioMenuItem(label=tr(STATUS_LABEL_KEYS[value]), group=group_item)
            group_item = group_item or item
            item.set_active(value == current_status)
            item.connect("toggled", self._handle_status_toggled, value)
            self._status_items[value] = item
            menu.append(item)
        return menu

    def _handle_status_toggled(self, item: Gtk.RadioMenuItem, value: str) -> None:
        if self._updating_menu or not item.get_active():
            return
        _defer(self._on_set_status, value)

    def set_forced_status(self, status: str) -> None:
        """Reflect a status change that didn't come through this menu
        (e.g. invisible mode toggled in the web client)."""
        item = self._status_items.get(status)
        if item is None or item.get_active():
            return
        self._updating_menu = True
        try:
            item.set_active(True)
        finally:
            self._updating_menu = False

    def _handle_set_status_message(self) -> None:
        dialog = _StatusMessageDialog(self._settings.status_message)
        dialog.connect("response", self._handle_status_message_response)
        dialog.show()

    def _handle_status_message_response(self, dialog: "_StatusMessageDialog", response_id: int) -> None:
        if response_id == Gtk.ResponseType.OK:
            self._on_set_status_message(dialog.text())
        dialog.destroy()

    # --- connection state ---------------------------------------------------

    def set_connecting(self) -> None:
        self._state = ConnectionState.CONNECTING
        self._reenter_login_item.hide()
        self._drop_contacts()
        self._stop_blink()
        self._update_status_text(tr("tray.connecting"))

    def set_connected(self, state) -> None:
        self._state = ConnectionState.CONNECTED
        self._email = state.email
        self._reenter_login_item.hide()
        self._refresh_unread_display()

    def set_disconnected(self, _reason: str = "") -> None:
        self._state = ConnectionState.DISCONNECTED
        self._stop_blink()
        self._drop_contacts()
        self._update_status_text(tr("tray.disconnected_retrying"))

    def set_reconnect_scheduled(self, seconds: float) -> None:
        if self._state != ConnectionState.AUTH_ERROR:
            self._update_status_text(tr("tray.reconnect_in", seconds=f"{seconds:.0f}"))

    def set_auth_error(self, _message: str = "") -> None:
        self._state = ConnectionState.AUTH_ERROR
        self._stop_blink()
        self._drop_contacts()
        self._reenter_login_item.show()
        self._update_status_text(tr("tray.auth_error"))

    def set_presence_status(self, status: str) -> None:
        """Icon colour from the server-confirmed presence (online/away/
        offline/invisible), see presence.PresenceCoordinator."""
        self._presence_status = status
        self._refresh_icon()

    # --- contacts ---------------------------------------------------------------

    @property
    def contacts(self) -> ContactBook | None:
        """The current session's contact book (None while not connected);
        main.py hands its user list to the settings dialog."""
        return self._contacts

    def set_contacts(self, book: ContactBook) -> None:
        self._contacts = book
        self.refresh_contacts()

    def apply_contact_event(self, event: dict) -> None:
        if self._contacts is not None:
            self._contacts.apply_event(event)
            self.refresh_contacts()

    def _drop_contacts(self) -> None:
        self._contacts = None
        if self._search_window is not None:
            self._search_window.destroy()
        self.refresh_contacts()

    def open_contact_search(self) -> None:
        if self._contacts is None:
            return
        if self._search_window is None:
            self._search_window = ContactSearchWindow(
                self._contacts, self._settings.favorite_user_ids, self._open_contact,
            )
            self._search_window.connect("destroy", self._on_search_window_destroyed)
        self._search_window.present()

    def _on_search_window_destroyed(self, window) -> None:
        if self._search_window is window:
            self._search_window = None

    def refresh_contacts(self) -> None:
        """Favorites (by name), then the most recent 1:1 DM partners not
        already listed, grouped into today / yesterday / last week / older,
        each with presence and the time or date of the last DM.
        The submenu is only rebuilt when its content actually changed --
        presence events for the whole realm arrive every minute, and
        rebuilding while the menu is open would make it flicker."""
        book = self._contacts if self._state == ConnectionState.CONNECTED else None
        now = GLib.get_real_time() / 1_000_000
        rendered: list[tuple[str, list[tuple[int, str, str]]]] = []
        if book is not None:
            favorites = book.contacts(self._settings.favorite_user_ids)
            if favorites:
                rendered.append(("tray.contacts_favorites_header", [self._contact_entry(c, now) for c in favorites]))
            buckets: dict[str, list] = {bucket: [] for bucket in RECENCY_BUCKETS}
            recent_range = self._settings.recent_range
            for contact in book.recent(RECENT_LIMIT, exclude={c.user_id for c in favorites}, now=now):
                bucket = recency_bucket(contact.last_message_ts, now)
                # Unread conversations show regardless of the range, so a
                # waiting answer is never hidden by the setting.
                if contact.unread or bucket_in_range(bucket, recent_range):
                    buckets[bucket].append(self._contact_entry(contact, now, day_in_header=True))
            rendered += [(RECENCY_HEADER_KEYS[bucket], entries) for bucket, entries in buckets.items() if entries]
        if self._search_window is not None and book is not None:
            # Same 60 s timer and events keep presence and dates current
            # in the open search window.
            self._search_window.set_book(book, self._settings.favorite_user_ids)
        self._search_item.set_visible(book is not None)
        if rendered == self._contacts_rendered:
            return
        self._contacts_rendered = rendered
        for child in self._contacts_menu.get_children():
            self._contacts_menu.remove(child)
        for index, (header, entries) in enumerate(rendered):
            if index:
                self._contacts_menu.append(Gtk.SeparatorMenuItem())
            header_item = Gtk.MenuItem(label=tr(header))
            header_item.set_sensitive(False)
            self._contacts_menu.append(header_item)
            for user_id, full_name, label in entries:
                item = Gtk.MenuItem(label=label)
                item.connect("activate", lambda _item, u=user_id, n=full_name: _defer(self._open_contact, u, n))
                self._contacts_menu.append(item)
        if book is not None and not rendered:
            # Nothing to list (no favorites, recent ones switched off or none
            # in range): point to where the menu gets filled instead of
            # hiding it.
            setup_item = Gtk.MenuItem(label=tr("tray.contacts_setup"))
            setup_item.connect("activate", lambda *_: _defer(self._on_open_settings, "favorites"))
            self._contacts_menu.append(setup_item)
        self._contacts_menu.show_all()
        self._contacts_item.set_visible(book is not None)

    @staticmethod
    def _contact_entry(contact: Contact, now: float, day_in_header: bool = False) -> tuple[int, str, str]:
        label = f"{contact.presence_mark}  {contact.full_name}"
        if contact.unread:
            label += f"  ({contact.unread})"
        when = format_last_message(contact.last_message_ts, now, day_in_header)
        return contact.user_id, contact.full_name, f"{label}  ·  {when}" if when else label

    def _open_contact(self, user_id: int, full_name: str) -> None:
        # Same key notifications.conversation_for uses for a 1:1 DM, so
        # opening the chat from here also clears it from the missed list.
        self.clear_unread(f"dm:{user_id}")
        self._on_open_conversation(dm_fragment([user_id], full_name))

    # --- unread / blink -------------------------------------------------------

    def notify_unread(self, notification: Notification) -> None:
        conversation = notification.conversation
        entry = self._unread.setdefault(conversation.key, _Unread(conversation.label, conversation.fragment))
        entry.label = conversation.label
        entry.message_ids.add(notification.message_id)
        self._restart_settle_timer()
        self._refresh_unread_display()
        self._refresh_missed_menu()

    def messages_read(self, message_ids: list[int] | None) -> None:
        """update_message_flags "read" from the server: the user read
        those messages elsewhere (web/mobile), so drop them from the missed
        list -- None means "everything"."""
        if message_ids is None:
            self._unread.clear()
        else:
            read = set(message_ids)
            for key, entry in list(self._unread.items()):
                entry.message_ids -= read
                if not entry.message_ids:
                    del self._unread[key]
        self._refresh_unread_display()
        self._refresh_missed_menu()

    def resync_unread(self, snapshot: UnreadSnapshot) -> None:
        """Drops what the server no longer counts as unread: read events
        sent while the tray had no event queue (suspend, network loss) are
        lost, and without this the icon would blink for messages read
        long ago until each one was clicked."""
        for key, entry in list(self._unread.items()):
            entry.message_ids = {mid for mid in entry.message_ids if snapshot.is_unread(mid)}
            if not entry.message_ids:
                del self._unread[key]
        self._refresh_unread_display()
        self._refresh_missed_menu()

    def mark_all_read(self) -> None:
        """Marks every message in the missed list read on the server too,
        so the web and mobile clients agree with the tray."""
        message_ids = set().union(*(entry.message_ids for entry in self._unread.values()))
        self.clear_all_unread()
        if message_ids:
            self._on_mark_read(sorted(message_ids))

    def unread_message_ids(self, key: str) -> set[int]:
        entry = self._unread.get(key)
        return set(entry.message_ids) if entry else set()

    def clear_unread(self, key: str) -> None:
        self._unread.pop(key, None)
        self._refresh_unread_display()
        self._refresh_missed_menu()

    def clear_all_unread(self) -> None:
        self._unread.clear()
        self._refresh_unread_display()
        self._refresh_missed_menu()

    def handle_conversation_click(self, key: str) -> None:
        """Opens the conversation in the web client, clearing its unread
        state first. Used by the missed-messages submenu and, from
        main.py, as the desktop-notification click handler."""
        entry = self._unread.get(key)
        fragment = entry.fragment if entry else None
        self.clear_unread(key)
        self._on_open_conversation(fragment)

    def _refresh_unread_display(self) -> None:
        if self._state != ConnectionState.CONNECTED:
            return
        if not self._unread:
            self._cancel_settle_timer()
            self._blink_settled = False
        if self._unread and self._settings.blink_enabled:
            if self._blink_settled:
                self._show_steady_unread()
            else:
                self._start_blink()
        else:
            self._stop_blink()
        if self._unread:
            self._update_status_text(tr("tray.connected_unread", count=len(self._unread)))
        else:
            self._update_status_text(tr("tray.connected_as", username=self._email))

    def _refresh_missed_menu(self) -> None:
        for child in self._missed_menu.get_children():
            self._missed_menu.remove(child)
        for key, entry in self._unread.items():
            label = entry.label if entry.label.startswith("#") else tr("tray.missed_message_entry", sender=entry.label)
            item = Gtk.MenuItem(label=label)
            item.connect("activate", lambda _item, k=key: _defer(self.handle_conversation_click, k))
            self._missed_menu.append(item)
        if self._unread:
            self._missed_menu.append(Gtk.SeparatorMenuItem())
            self._missed_menu.append(self._mark_all_read_item)
        self._missed_menu.show_all()
        self._missed_item.set_visible(bool(self._unread))

    def _start_blink(self) -> None:
        if self._blink_timer is None:
            self._blink_timer = GLib.timeout_add(BLINK_INTERVAL_MS, self._toggle_blink)

    def _stop_blink(self) -> None:
        if self._blink_timer is not None:
            GLib.source_remove(self._blink_timer)
            self._blink_timer = None
        self._blink_visible = False
        self._refresh_icon()

    def blink_settings_changed(self) -> None:
        """After the settings dialog: a changed time limit counts from now."""
        if self._unread:
            self._restart_settle_timer()
        self._refresh_unread_display()

    def _show_steady_unread(self) -> None:
        if self._blink_timer is not None:
            GLib.source_remove(self._blink_timer)
            self._blink_timer = None
        self._blink_visible = True
        self._refresh_icon()

    def _restart_settle_timer(self) -> None:
        # Every new notification blinks again for the full time.
        self._cancel_settle_timer()
        self._blink_settled = False
        minutes = self._settings.blink_stop_minutes
        if minutes:
            self._settle_timer = GLib.timeout_add_seconds(minutes * 60, self._settle_blink)

    def _cancel_settle_timer(self) -> None:
        if self._settle_timer is not None:
            GLib.source_remove(self._settle_timer)
            self._settle_timer = None

    def _settle_blink(self) -> bool:
        self._settle_timer = None
        self._blink_settled = True
        self._refresh_unread_display()
        return False

    def _toggle_blink(self) -> bool:
        self._blink_visible = not self._blink_visible
        self._refresh_icon()
        return True

    def _current_status_key(self) -> str:
        # Any not-fully-connected state shows as grey regardless of the
        # last presence seen.
        return self._presence_status if self._state == ConnectionState.CONNECTED else "offline"

    def _refresh_icon(self) -> None:
        self._indicator.set_icon_full(icon_name(self._current_status_key(), self._blink_visible), "Zulip")

    # --- menu / title ------------------------------------------------------------

    def _handle_open_chat(self) -> None:
        self.clear_all_unread()
        self._on_open_chat()

    def _update_status_text(self, text: str) -> None:
        self._refresh_icon()
        self._status_item.set_label(text)
        # StatusNotifierItem has no tooltip GNOME would show; the title is
        # the accessible name/hover text where a host renders one.
        self._indicator.set_title(f"Zulip: {text}" if self._settings.tooltip_enabled else "Zulip")


class _StatusMessageDialog(Gtk.Dialog):
    def __init__(self, current_message: str, parent=None):
        super().__init__(title=tr("tray.status_message_dialog_title"), transient_for=parent, modal=True)
        self.set_default_size(380, -1)
        self.set_resizable(False)
        content = self.get_content_area()
        content.set_spacing(8)
        for side in ("top", "bottom", "start", "end"):
            getattr(content, f"set_margin_{side}")(14)
        label = Gtk.Label(label=tr("tray.status_message_dialog_label"), xalign=0)
        label.set_line_wrap(True)
        self._entry = Gtk.Entry(text=current_message, activates_default=True)
        self._entry.set_max_length(60)  # Zulip's status_text limit
        content.pack_start(label, False, False, 0)
        content.pack_start(self._entry, False, False, 0)
        self.add_button(tr("common.cancel"), Gtk.ResponseType.CANCEL)
        ok_button = self.add_button(tr("common.ok"), Gtk.ResponseType.OK)
        ok_button.get_style_context().add_class("suggested-action")
        pad_button_row(ok_button)
        self.set_default_response(Gtk.ResponseType.OK)
        content.show_all()
        self._entry.grab_focus()
        self._entry.select_region(0, -1)

    def text(self) -> str:
        return self._entry.get_text().strip()
