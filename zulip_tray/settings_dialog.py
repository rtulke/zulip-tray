from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from . import sounds
from .i18n import tr

ROW_MARGIN = 10


def _row(*widgets: Gtk.Widget, expand_last: bool = False) -> Gtk.ListBoxRow:
    """One settings row: widgets left to right, the last one pushed to the
    far right (or stretched across, for an entry)."""
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    for side in ("top", "bottom", "start", "end"):
        getattr(box, f"set_margin_{side}")(ROW_MARGIN if side in ("top", "bottom") else 14)
    for index, widget in enumerate(widgets):
        last = index == len(widgets) - 1
        if last and not expand_last:
            widget.set_halign(Gtk.Align.END)
            box.pack_end(widget, False, False, 0)
        else:
            box.pack_start(widget, last and expand_last, last and expand_last, 0)
    row = Gtk.ListBoxRow(activatable=False, selectable=False)
    row.add(box)
    return row


def _label(text: str) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=0)
    label.set_line_wrap(True)
    return label


def _boxed_list(rows: list[Gtk.ListBoxRow]) -> Gtk.Frame:
    """GNOME's "boxed list" look: rows grouped in one bordered card."""
    listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
    for row in rows:
        listbox.add(row)
    frame = Gtk.Frame()
    frame.add(listbox)
    return frame


def _page(heading: str, *cards: Gtk.Frame) -> Gtk.Box:
    page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
    for side in ("top", "bottom", "start", "end"):
        getattr(page, f"set_margin_{side}")(18)
    title = Gtk.Label(label=heading, xalign=0)
    title.get_style_context().add_class("dim-label")
    page.pack_start(title, False, False, 0)
    for card in cards:
        page.pack_start(card, False, False, 0)
    return page


class SettingsDialog(Gtk.Dialog):
    """Compact nav dialog: category list on the left (Gtk.StackSidebar),
    one page of boxed-list rows on the right. Mirrors rocketchat-tray's
    settings minus the native-chat category. Nothing is applied until
    Save; "Test" previews the sound choice/volume immediately."""

    def __init__(
        self, settings, current_server_url: str, current_verify_ssl: bool, current_autostart_enabled: bool,
        current_email: str, on_test_sound: Callable[[], None], on_update_server: Callable[[str, bool], None],
        on_reset_login: Callable[[], None], on_set_autostart: Callable[[bool], None],
        users: list[tuple[int, str]] | None = None, parent=None,
    ):
        super().__init__(title=tr("settings.title"), transient_for=parent, modal=False)
        self._settings = settings
        self._on_test_sound = on_test_sound
        self._on_update_server = on_update_server
        self._on_set_autostart = on_set_autostart
        self.set_default_size(720, 440)
        self.set_icon_name("zulip-tray")

        # --- Zulip defaults ------------------------------------------------------
        self._autostart_switch = Gtk.Switch(active=current_autostart_enabled)
        email_label = Gtk.Label(label=current_email or "—", xalign=1)
        email_label.set_selectable(True)
        email_label.get_style_context().add_class("dim-label")
        self._server_url_entry = Gtk.Entry(text=current_server_url, hexpand=True)
        self._verify_ssl_switch = Gtk.Switch(active=current_verify_ssl)
        reset_login_button = Gtk.Button(label=tr("settings.reset_login"))
        reset_login_button.connect("clicked", lambda *_: on_reset_login())
        defaults_card = _boxed_list([
            _row(_label(tr("settings.autostart")), self._autostart_switch),
            _row(_label(tr("settings.email")), email_label),
            _row(_label(tr("settings.server_url")), self._server_url_entry, expand_last=True),
            _row(_label(tr("settings.verify_ssl")), self._verify_ssl_switch),
            _row(reset_login_button),
        ])

        # --- web chat ----------------------------------------------------------------
        self._app_mode_switch = Gtk.Switch(active=settings.app_mode_enabled)
        browser_card = _boxed_list([_row(_label(tr("settings.app_mode")), self._app_mode_switch)])

        # --- notifications -------------------------------------------------------------
        self._blink_switch = Gtk.Switch(active=settings.blink_enabled)
        self._sound_switch = Gtk.Switch(active=settings.sound_enabled)
        self._tooltip_switch = Gtk.Switch(active=settings.tooltip_enabled)
        self._sound_combo = Gtk.ComboBoxText()
        for key, label, _path in sounds.available_choices():
            self._sound_combo.append(key, label)
        if not self._sound_combo.set_active_id(settings.sound_choice):
            self._sound_combo.set_active(0)
        self._volume_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self._volume_scale.set_value(round(settings.sound_volume * 100))
        self._volume_scale.set_draw_value(False)
        self._volume_scale.set_size_request(160, -1)
        self._volume_scale.set_hexpand(True)
        test_button = Gtk.Button(label=tr("settings.test"))
        test_button.connect("clicked", lambda *_: self._handle_test_sound())

        def _update_sound_controls(*_args) -> None:
            enabled = self._sound_switch.get_active()
            for widget in (self._sound_combo, self._volume_scale, test_button):
                widget.set_sensitive(enabled)

        self._sound_switch.connect("notify::active", _update_sound_controls)
        _update_sound_controls()
        volume_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        volume_box.pack_start(self._volume_scale, True, True, 0)
        volume_box.pack_start(test_button, False, False, 0)
        notifications_card = _boxed_list([
            _row(_label(tr("settings.blink")), self._blink_switch),
            _row(_label(tr("settings.sound_enabled")), self._sound_switch),
            _row(_label(tr("settings.sound_choice")), self._sound_combo),
            _row(_label(tr("settings.volume")), volume_box, expand_last=True),
            _row(_label(tr("settings.tooltip")), self._tooltip_switch),
        ])

        # --- away -----------------------------------------------------------------------
        self._idle_switch = Gtk.Switch(active=settings.idle_detection_enabled)
        # 1..12 "5-minute steps" so every slider position is a valid value.
        self._idle_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 1, 12, 1)
        self._idle_scale.set_value(round(settings.idle_threshold_minutes / 5))
        self._idle_scale.set_draw_value(False)
        self._idle_scale.set_size_request(160, -1)
        self._idle_scale.set_hexpand(True)
        idle_value_label = Gtk.Label(xalign=1)
        idle_value_label.set_width_chars(7)

        def _update_idle_label(*_args) -> None:
            idle_value_label.set_text(tr("settings.idle_minutes_value", minutes=int(self._idle_scale.get_value()) * 5))

        self._idle_scale.connect("value-changed", _update_idle_label)
        _update_idle_label()
        duration_label = _label(tr("settings.idle_duration"))

        def _update_idle_controls(*_args) -> None:
            enabled = self._idle_switch.get_active()
            for widget in (duration_label, self._idle_scale, idle_value_label):
                widget.set_sensitive(enabled)

        self._idle_switch.connect("notify::active", _update_idle_controls)
        _update_idle_controls()
        idle_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        idle_box.pack_start(self._idle_scale, True, True, 0)
        idle_box.pack_start(idle_value_label, False, False, 0)
        presence_card = _boxed_list([
            _row(_label(tr("settings.idle_away")), self._idle_switch),
            _row(duration_label, idle_box, expand_last=True),
        ])

        # --- favorites --------------------------------------------------------------------
        # users is None while not connected: without the realm's user list
        # there are no names to show, and the stored favorites stay as they are.
        self._favorite_checks: dict[int, Gtk.CheckButton] = {}
        favorites_card = self._build_favorites_card(users, set(settings.favorite_user_ids))

        # --- layout ---------------------------------------------------------------------
        stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.NONE, hexpand=True, vexpand=True)
        for name, label, page in [
            ("defaults", tr("settings.category_defaults"), _page(tr("settings.category_defaults"), defaults_card)),
            ("webchat", tr("settings.category_webchat"), _page(tr("settings.category_webchat"), browser_card)),
            ("notifications", tr("settings.section_notifications"),
             _page(tr("settings.section_notifications"), notifications_card)),
            ("presence", tr("settings.section_presence"), _page(tr("settings.section_presence"), presence_card)),
            ("favorites", tr("settings.section_favorites"),
             _page(tr("settings.section_favorites"), favorites_card)),
        ]:
            stack.add_titled(page, name, label)
        sidebar = Gtk.StackSidebar(stack=stack)
        sidebar.set_size_request(200, -1)
        content_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        content_row.pack_start(sidebar, False, False, 0)
        content_row.pack_start(stack, True, True, 0)
        content = self.get_content_area()
        content.pack_start(content_row, True, True, 0)

        self.add_button(tr("common.cancel"), Gtk.ResponseType.CANCEL)
        save_button = self.add_button(tr("settings.save"), Gtk.ResponseType.OK)
        save_button.get_style_context().add_class("suggested-action")
        self.set_default_response(Gtk.ResponseType.OK)
        self.connect("response", self._handle_response)
        content.show_all()

    def _build_favorites_card(self, users: list[tuple[int, str]] | None, favorites: set[int]) -> Gtk.Frame:
        if users is None:
            return _boxed_list([_row(_label(tr("settings.favorites_unavailable")))])
        search = Gtk.SearchEntry(placeholder_text=tr("settings.favorites_search"), hexpand=True)
        listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        for user_id, full_name in users:
            check = Gtk.CheckButton(label=full_name, active=user_id in favorites)
            for side in ("top", "bottom", "start", "end"):
                getattr(check, f"set_margin_{side}")(4 if side in ("top", "bottom") else 14)
            self._favorite_checks[user_id] = check
            row = Gtk.ListBoxRow(activatable=False, selectable=False)
            row.add(check)
            row.full_name = full_name.casefold()
            listbox.add(row)
        listbox.set_filter_func(lambda row: search.get_text().strip().casefold() in row.full_name)
        search.connect("search-changed", lambda *_: listbox.invalidate_filter())
        scrolled = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        scrolled.set_min_content_height(240)
        scrolled.add(listbox)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for side in ("top", "bottom", "start", "end"):
            getattr(box, f"set_margin_{side}")(ROW_MARGIN)
        box.pack_start(_label(tr("settings.favorites_hint")), False, False, 0)
        box.pack_start(search, False, False, 0)
        box.pack_start(scrolled, True, True, 0)
        frame = Gtk.Frame()
        frame.add(box)
        return frame

    def _handle_test_sound(self) -> None:
        # Apply the current volume/choice first so "Test" previews exactly
        # what's about to be saved.
        self._settings.sound_volume = self._volume_scale.get_value() / 100
        self._settings.sound_choice = self._sound_combo.get_active_id() or self._settings.sound_choice
        self._on_test_sound()

    def _handle_response(self, _dialog, response_id: int) -> None:
        if response_id != Gtk.ResponseType.OK:
            return
        self._on_set_autostart(self._autostart_switch.get_active())
        url = self._server_url_entry.get_text().strip().rstrip("/")
        if url:
            self._on_update_server(url, self._verify_ssl_switch.get_active())
        settings = self._settings
        settings.blink_enabled = self._blink_switch.get_active()
        settings.sound_enabled = self._sound_switch.get_active()
        settings.tooltip_enabled = self._tooltip_switch.get_active()
        settings.app_mode_enabled = self._app_mode_switch.get_active()
        settings.idle_detection_enabled = self._idle_switch.get_active()
        settings.idle_threshold_minutes = int(self._idle_scale.get_value()) * 5
        settings.sound_choice = self._sound_combo.get_active_id() or settings.sound_choice
        settings.sound_volume = self._volume_scale.get_value() / 100
        if self._favorite_checks:
            settings.favorite_user_ids = [uid for uid, check in self._favorite_checks.items() if check.get_active()]
        settings.save()
