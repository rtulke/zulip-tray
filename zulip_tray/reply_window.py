from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gtk  # noqa: E402

from .i18n import tr  # noqa: E402
from .notifications import Notification  # noqa: E402


class ReplyWindow(Gtk.Window):
    """Small answer box opened from a notification's "Reply" button:
    GNOME's notifications cannot host a text field, so the reply gets a
    window of its own. Enter sends, Shift+Enter starts a new line, Esc
    closes. on_send(text, on_done) runs the request off the main loop and
    calls on_done(error) back on it (None = sent)."""

    def __init__(self, notification: Notification, on_send: Callable[[str, Callable[[str | None], None]], None]):
        super().__init__(title=tr("reply.title", name=notification.conversation.label))
        self._on_send = on_send
        self.set_icon_name("zulip-tray")
        self.set_default_size(460, -1)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_type_hint(Gdk.WindowTypeHint.DIALOG)
        self.set_keep_above(True)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for side in ("top", "bottom", "start", "end"):
            getattr(box, f"set_margin_{side}")(12)
        heading = Gtk.Label(label=notification.title, xalign=0)
        heading.get_style_context().add_class("heading")
        heading.set_line_wrap(True)
        quoted = Gtk.Label(label=notification.body, xalign=0)
        quoted.get_style_context().add_class("dim-label")
        quoted.set_line_wrap(True)
        quoted.set_selectable(True)
        box.pack_start(heading, False, False, 0)
        box.pack_start(quoted, False, False, 0)

        self._text = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False)
        for side in ("top", "bottom", "left", "right"):
            getattr(self._text, f"set_{side}_margin")(6)
        self._text.connect("key-press-event", self._on_key)
        scrolled = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        scrolled.set_min_content_height(80)
        scrolled.add(self._text)
        frame = Gtk.Frame()
        frame.add(scrolled)
        box.pack_start(frame, True, True, 0)

        self._status = Gtk.Label(label=tr("reply.hint"), xalign=0)
        self._status.get_style_context().add_class("dim-label")
        self._status.set_line_wrap(True)
        box.pack_start(self._status, False, False, 0)

        buttons = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        cancel = Gtk.Button(label=tr("common.cancel"))
        cancel.connect("clicked", lambda *_: self.destroy())
        self._send_button = Gtk.Button(label=tr("reply.send"))
        self._send_button.get_style_context().add_class("suggested-action")
        self._send_button.connect("clicked", lambda *_: self._send())
        buttons.pack_start(cancel, False, False, 0)
        buttons.pack_start(self._send_button, False, False, 0)
        box.pack_start(buttons, False, False, 0)
        self.add(box)
        self.connect("key-press-event", self._on_window_key)
        self.show_all()
        self._text.grab_focus()

    def _on_key(self, _view, event) -> bool:
        if event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and not event.state & Gdk.ModifierType.SHIFT_MASK:
            self._send()
            return True
        return False

    def _on_window_key(self, _window, event) -> bool:
        if event.keyval == Gdk.KEY_Escape:
            self.destroy()
            return True
        return False

    def _send(self) -> None:
        buffer = self._text.get_buffer()
        text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False).strip()
        if not text or not self._send_button.get_sensitive():
            return
        self._send_button.set_sensitive(False)
        self._text.set_sensitive(False)
        self._status.set_text(tr("reply.sending"))
        self._on_send(text, self._on_done)

    def _on_done(self, error: str | None) -> None:
        if error is None:
            self.destroy()
            return
        # Keep the text: the user can retry or copy it elsewhere.
        self._status.set_text(tr("reply.failed", error=error))
        self._send_button.set_sensitive(True)
        self._text.set_sensitive(True)
        self._text.grab_focus()
