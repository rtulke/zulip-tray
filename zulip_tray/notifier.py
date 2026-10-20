from __future__ import annotations

import logging
import subprocess
from typing import Callable

import gi

gi.require_version("Notify", "0.7")
from gi.repository import GLib, Notify  # noqa: E402

from . import sounds
from .i18n import tr
from .notifications import Notification

logger = logging.getLogger(__name__)

APP_NAME = "Zulip Tray"
APP_ID = "io.github.rtulke.ZulipTray"
NOTIFICATION_TIMEOUT_MS = 8000


class NotificationManager:
    """Desktop notifications through libnotify (org.freedesktop.Notifications
    over D-Bus, with proper per-notification identity so a burst from
    several conversations stays apart and each click routes to its own
    conversation). Sound goes through paplay like GNOME's own alerts --
    it handles the compressed system theme sounds and the bundled .wav
    alike, with the same volume control."""

    def __init__(self, settings, icon_name: str = "zulip-tray"):
        self._settings = settings
        self._icon_name = icon_name
        self._on_conversation_clicked: Callable[[str], None] | None = None
        self._on_reply: Callable[[Notification, str | None], None] | None = None
        # libnotify drops action callbacks once the Notification object is
        # garbage-collected -- keep every shown one alive until it closes.
        self._live: dict[int, Notify.Notification] = {}
        self._available = Notify.init(APP_NAME)
        if not self._available:
            logger.warning("Benachrichtigungsdienst nicht verfuegbar")

    def set_click_handler(self, handler: Callable[[str], None]) -> None:
        """handler(conversation_key) runs when a notification body is clicked."""
        self._on_conversation_clicked = handler

    def set_reply_handler(self, handler: Callable[[Notification, str | None], None]) -> None:
        """handler(notification, activation_token) runs on the "Reply" button."""
        self._on_reply = handler

    def notify(self, notification: Notification) -> None:
        if self._available:
            self._show(notification)
        if self._settings.sound_enabled:
            self._play_sound()

    def _show(self, notification: Notification) -> None:
        notice = Notify.Notification.new(notification.title, notification.body, self._icon_name)
        notice.set_timeout(NOTIFICATION_TIMEOUT_MS)
        # Lets GNOME Shell attribute the notification to this app's
        # .desktop entry (name + icon in the banner and the message tray).
        notice.set_hint("desktop-entry", GLib.Variant("s", APP_ID))
        key = notification.conversation.key
        notice.add_action("default", "Open", self._handle_action, key)
        notice.add_action("reply", tr("notify.reply"), self._handle_reply, notification)
        notice.connect("closed", self._handle_closed)
        try:
            notice.show()
        except GLib.Error as exc:
            logger.warning("Benachrichtigung konnte nicht angezeigt werden: %s", exc.message)
            return
        self._live[id(notice)] = notice

    def _handle_action(self, notice: Notify.Notification, action: str, key: str) -> None:
        if action == "default" and self._on_conversation_clicked:
            self._on_conversation_clicked(key)

    def _handle_reply(self, notice: Notify.Notification, _action: str, notification: Notification) -> None:
        if self._on_reply:
            # libnotify >= 0.8: the shell's xdg-activation token, so the
            # reply window may take the focus on Wayland.
            get_token = getattr(notice, "get_activation_token", None)
            self._on_reply(notification, get_token() if get_token else None)

    def _handle_closed(self, notice: Notify.Notification) -> None:
        self._live.pop(id(notice), None)

    def _play_sound(self) -> None:
        path = sounds.resolve(self._settings.sound_choice)
        volume = round(self._settings.sound_volume * 65536)
        try:
            subprocess.Popen(
                ["paplay", "--volume", str(volume), str(path)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
            )
        except OSError as exc:
            logger.warning("paplay nicht verfuegbar: %s", exc)

    def play_test_sound(self) -> None:
        self._play_sound()
