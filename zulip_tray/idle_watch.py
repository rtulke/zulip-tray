from __future__ import annotations

import logging

from gi.repository import Gio, GLib

from .mainloop import Signal

logger = logging.getLogger(__name__)

IDLE_MONITOR_SERVICE = "org.gnome.Mutter.IdleMonitor"
IDLE_MONITOR_PATH = "/org/gnome/Mutter/IdleMonitor/Core"
IDLE_MONITOR_INTERFACE = "org.gnome.Mutter.IdleMonitor"
POLL_INTERVAL_MS = 15_000


class IdleWatcher:
    """Polls GNOME's (Mutter) idle monitor over D-Bus for OS-level
    inactivity. Polling GetIdletime() every 15 s rather than registering
    an AddIdleWatch: minute-scale precision is all this needs, and a poll
    has no watch-id bookkeeping to redo after a gnome-shell restart."""

    def __init__(self):
        self.became_idle = Signal("became_idle")
        self.became_active = Signal("became_active")
        self._threshold_ms = 5 * 60 * 1000
        self._is_idle = False
        self._enabled = False
        self._timer_id: int | None = None
        self._proxy: Gio.DBusProxy | None = None
        try:
            self._proxy = Gio.DBusProxy.new_for_bus_sync(
                Gio.BusType.SESSION, Gio.DBusProxyFlags.DO_NOT_LOAD_PROPERTIES, None,
                IDLE_MONITOR_SERVICE, IDLE_MONITOR_PATH, IDLE_MONITOR_INTERFACE, None,
            )
        except GLib.Error as exc:
            logger.warning("GNOME-Idle-Monitor nicht erreichbar: %s", exc.message)

    @property
    def is_idle(self) -> bool:
        return self._enabled and self._is_idle

    def set_threshold_minutes(self, minutes: int) -> None:
        self._threshold_ms = minutes * 60 * 1000
        if self._enabled:
            self._poll()

    def set_enabled(self, enabled: bool) -> None:
        if enabled == self._enabled:
            return
        if enabled and self._proxy is None:
            logger.warning("GNOME-Idle-Monitor nicht verfuegbar; Leerlauferkennung bleibt aus")
            return
        self._enabled = enabled
        if enabled:
            self._is_idle = False
            self._timer_id = GLib.timeout_add(POLL_INTERVAL_MS, self._tick)
            self._poll()
        else:
            if self._timer_id is not None:
                GLib.source_remove(self._timer_id)
                self._timer_id = None
            if self._is_idle:
                self._is_idle = False
                self.became_active.emit()

    def _tick(self) -> bool:
        self._poll()
        return True

    def _poll(self) -> None:
        try:
            result = self._proxy.call_sync("GetIdletime", None, Gio.DBusCallFlags.NONE, 2000, None)
        except GLib.Error as exc:
            logger.warning("GetIdletime fehlgeschlagen: %s", exc.message)
            return
        idle_ms = result.unpack()[0]
        now_idle = idle_ms >= self._threshold_ms
        if now_idle and not self._is_idle:
            self._is_idle = True
            self.became_idle.emit()
        elif not now_idle and self._is_idle:
            self._is_idle = False
            self.became_active.emit()
