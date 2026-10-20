from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from . import sounds
from .config import CLICK_ACTIONS, COLOR_SCHEMES, RECENT_RANGES, REPLY_BUTTON_OPTIONS
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
        users: list[tuple[int, str]] | None = None, current_chat_color_scheme: str | None = None,
        on_set_chat_color_scheme: Callable[[str], None] | None = None, parent=None,
    ):
        super().__init__(title=tr("settings.title"), transient_for=parent, modal=False)
        self._settings = settings
        self._on_test_sound = on_test_sound
        self._on_update_server = on_update_server
        self._on_set_autostart = on_set_autostart
        self._on_set_chat_color_scheme = on_set_chat_color_scheme
        self._initial_chat_color_scheme = current_chat_color_scheme
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
        self._reply_combo = Gtk.ComboBoxText()
        for option in REPLY_BUTTON_OPTIONS:
            self._reply_combo.append(option, tr(f"settings.reply_button_{option}"))
        if not self._reply_combo.set_active_id(settings.reply_button):
            self._reply_combo.set_active(1)
        self._click_combo = Gtk.ComboBoxText()
        for option in CLICK_ACTIONS:
            self._click_combo.append(option, tr(f"settings.click_action_{option}"))
        if not self._click_combo.set_active_id(settings.click_action):
            self._click_combo.set_active(0)
        # Only meaningful while there is a Reply button to stand in for.
        self._reply_combo.connect("changed", lambda combo: self._click_combo.set_sensitive(
            combo.get_active_id() != "off"))
        self._click_combo.set_sensitive(settings.reply_button != "off")
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
            _row(_label(tr("settings.reply_button")), self._reply_combo),
            _row(_label(tr("settings.click_action")), self._click_combo),
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

        # --- colours -----------------------------------------------------------------------
        # The chat's colours are the Zulip account's own theme setting, so
        # they can only be read and changed while connected.
        self._window_colors_combo = self._color_combo(settings.window_color_scheme)
        self._chat_colors_combo = self._color_combo(current_chat_color_scheme or "system")
        self._chat_colors_combo.set_sensitive(current_chat_color_scheme is not None)
        chat_hint = _label(tr("settings.chat_colors_hint" if current_chat_color_scheme is not None
                              else "settings.chat_colors_unavailable"))
        chat_hint.get_style_context().add_class("dim-label")
        colors_card = _boxed_list([
            _row(_label(tr("settings.window_colors")), self._window_colors_combo),
            _row(_label(tr("settings.chat_colors")), self._chat_colors_combo),
            _row(chat_hint),
        ])

        # --- favorites --------------------------------------------------------------------
        # users is None while not connected: without the realm's user list
        # there are no names to show, and the stored favorites stay as they are.
        self._favorite_checks: dict[int, Gtk.CheckButton] = {}
        favorites_card = self._build_favorites_card(users, set(settings.favorite_user_ids))
        self._recent_combo = Gtk.ComboBoxText()
        for option in RECENT_RANGES:
            self._recent_combo.append(option, tr(f"settings.recent_range_{option}"))
        if not self._recent_combo.set_active_id(settings.recent_range):
            self._recent_combo.set_active_id("week")
        recent_hint = _label(tr("settings.recent_range_hint"))
        recent_hint.get_style_context().add_class("dim-label")
        recent_card = _boxed_list([
            _row(_label(tr("settings.recent_range")), self._recent_combo),
            _row(recent_hint),
        ])

        # --- layout ---------------------------------------------------------------------
        stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.NONE, hexpand=True, vexpand=True)
        for name, label, page in [
            ("defaults", tr("settings.category_defaults"), _page(tr("settings.category_defaults"), defaults_card)),
            ("webchat", tr("settings.category_webchat"), _page(tr("settings.category_webchat"), browser_card)),
            ("notifications", tr("settings.section_notifications"),
             _page(tr("settings.section_notifications"), notifications_card)),
            ("presence", tr("settings.section_presence"), _page(tr("settings.section_presence"), presence_card)),
            ("colors", tr("settings.section_colors"), _page(tr("settings.section_colors"), colors_card)),
            ("favorites", tr("settings.section_direct_messages"),
             _page(tr("settings.section_direct_messages"), recent_card, favorites_card)),
        ]:
            stack.add_titled(page, name, label)
        self._stack = stack
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

    def show_page(self, name: str) -> None:
        """Open on a given category, e.g. "favorites" from the tray's
        empty direct-messages menu."""
        if self._stack.get_child_by_name(name) is not None:
            self._stack.set_visible_child_name(name)

    @staticmethod
    def _color_combo(current: str) -> Gtk.ComboBoxText:
        combo = Gtk.ComboBoxText()
        for scheme in COLOR_SCHEMES:
            combo.append(scheme, tr(f"settings.color_{scheme}"))
        if not combo.set_active_id(current):
            combo.set_active(0)
        return combo

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
        settings.reply_button = self._reply_combo.get_active_id() or "direct"
        settings.click_action = self._click_combo.get_active_id() or "open"
        settings.app_mode_enabled = self._app_mode_switch.get_active()
        settings.idle_detection_enabled = self._idle_switch.get_active()
        settings.idle_threshold_minutes = int(self._idle_scale.get_value()) * 5
        settings.sound_choice = self._sound_combo.get_active_id() or settings.sound_choice
        settings.sound_volume = self._volume_scale.get_value() / 100
        settings.window_color_scheme = self._window_colors_combo.get_active_id() or "system"
        chat_scheme = self._chat_colors_combo.get_active_id()
        if (self._chat_colors_combo.get_sensitive() and chat_scheme != self._initial_chat_color_scheme
                and self._on_set_chat_color_scheme is not None):
            self._on_set_chat_color_scheme(chat_scheme)
        settings.recent_range = self._recent_combo.get_active_id() or "week"
        if self._favorite_checks:
            settings.favorite_user_ids = [uid for uid, check in self._favorite_checks.items() if check.get_active()]
        settings.save()
