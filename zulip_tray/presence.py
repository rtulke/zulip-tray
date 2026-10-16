from __future__ import annotations

import logging

from gi.repository import GLib

from . import zulip_api
from .mainloop import Signal, run_on_main, spawn

logger = logging.getLogger(__name__)

# Zulip's web client pings once a minute; the server's offline threshold
# (140 s by default) is sized for that cadence.
PING_INTERVAL_SECONDS = 60

# Server-aggregated presence -> tray icon name (see resources.ICON_NAMES).
_ICON_FOR_PRESENCE = {"active": "online", "idle": "away", "offline": "offline"}


class PresenceCoordinator:
    """Keeps the server's idea of this user's presence in line with the
    local intent, and the tray icon in line with the server.

    Zulip has no manually set online/away/busy the way Rocket.Chat does:
    presence is computed server-side from every client's pings
    ("active"/"idle" plus a timestamp), and the only switch is invisible
    mode (presence_enabled=false). So the forced status maps to:
      auto      -> report active, or idle once the idle watcher says so
      online    -> always report active
      away      -> always report idle
      invisible -> presence_enabled=false on the server (pings continue,
                   nobody sees them); back to true when leaving it
    The icon follows the *aggregated* status the server reports back in
    each ping response and in presence events -- so a colleague seeing
    you "active" because you're typing in the web client shows green here
    too, exactly like rocketchat-tray mirrored status changes made
    elsewhere. The status text (Zulip "status") is independent of all
    this and simply pushed when set."""

    def __init__(self, settings, worker, idle_watcher):
        self._settings = settings
        self._worker = worker
        self._idle_watcher = idle_watcher
        self._timer_id: int | None = None
        self._server_status = "offline"  # active/idle/offline as the server sees us
        self._presence_enabled: bool | None = None  # server-side user setting; None = unknown
        self.status_applied = Signal("status_applied")  # (icon status: online/away/offline/invisible)
        self.forced_status_changed = Signal("forced_status_changed")  # (forced status) -- server-driven, for the menu

    # --- lifecycle (wired to the worker in main.py) --------------------------------------------

    def handle_connected(self, state) -> None:
        self._presence_enabled = state.user_settings.get("presence_enabled", True)
        self._sync_invisible_setting()
        self._start_pinging()
        self._emit_icon_status()

    def handle_disconnected(self, *_args) -> None:
        self._stop_pinging()
        self._server_status = "offline"
        self._emit_icon_status()

    def note_server_presence(self, status: str) -> None:
        """Own aggregated presence from a presence event or ping response."""
        if status != self._server_status:
            self._server_status = status
            self._emit_icon_status()

    def note_user_setting(self, prop: str, value) -> None:
        """presence_enabled toggled -- possibly from the web client's own
        invisible-mode switch. Keep the forced status in the tray menu
        honest about it rather than silently fighting the server."""
        if prop != "presence_enabled":
            return
        self._presence_enabled = bool(value)
        if not value and self._settings.forced_status != "invisible":
            self._settings.forced_status = "invisible"
            self._settings.save()
            self.forced_status_changed.emit("invisible")
        elif value and self._settings.forced_status == "invisible":
            self._settings.forced_status = "auto"
            self._settings.save()
            self.forced_status_changed.emit("auto")
        self._emit_icon_status()

    # --- intent (settings dialog, status menu, idle watcher) --------------------------------------

    def apply(self, *_args) -> None:
        """Re-evaluate after a local change: pushes the invisible-mode
        setting if it differs from the server's, and pings immediately
        instead of waiting for the next minute tick."""
        self._sync_invisible_setting()
        self._ping()

    def set_status_text(self, text: str) -> None:
        api = self._worker.api
        if api is None:
            return

        def push() -> None:
            try:
                api.set_status_text(text)
            except zulip_api.ApiError as exc:
                logger.warning("Statustext konnte nicht gesetzt werden: %s", exc)

        spawn(push, name="status-text")

    # --- internals -------------------------------------------------------------------------------

    def _report_status(self) -> tuple[str, bool]:
        """(status to report, new_user_input) for the next ping."""
        forced = self._settings.forced_status
        idle = self._settings.idle_detection_enabled and self._idle_watcher.is_idle
        if forced == "online":
            return "active", True
        if forced == "away":
            return "idle", False
        return ("idle" if idle else "active"), not idle

    def _sync_invisible_setting(self) -> None:
        api = self._worker.api
        if api is None or self._presence_enabled is None:
            return
        want_enabled = self._settings.forced_status != "invisible"
        if want_enabled == self._presence_enabled:
            return
        self._presence_enabled = want_enabled  # optimistic; the user_settings echo confirms it

        def push() -> None:
            try:
                api.set_presence_enabled(want_enabled)
            except zulip_api.ApiError as exc:
                logger.warning("Unsichtbar-Modus konnte nicht gesetzt werden: %s", exc)

        spawn(push, name="presence-enabled")
        self._emit_icon_status()

    def _start_pinging(self) -> None:
        if self._timer_id is None:
            self._timer_id = GLib.timeout_add_seconds(PING_INTERVAL_SECONDS, self._tick)
        self._ping()

    def _stop_pinging(self) -> None:
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)
            self._timer_id = None

    def _tick(self) -> bool:
        self._ping()
        return True  # keep the timer

    def _ping(self) -> None:
        api, state = self._worker.api, self._worker.state
        if api is None or state is None:
            return
        status, new_user_input = self._report_status()
        user_id, offline_after = state.user_id, state.offline_threshold

        def push() -> None:
            try:
                body = api.update_presence(status, new_user_input)
            except zulip_api.ApiError as exc:
                logger.warning("Praesenz-Ping fehlgeschlagen: %s", exc)
                return
            own = zulip_api.own_presence_from_response(body, user_id, offline_after)
            if own:
                run_on_main(self.note_server_presence, own)

        spawn(push, name="presence-ping")

    def _emit_icon_status(self) -> None:
        if self._presence_enabled is False or self._settings.forced_status == "invisible":
            icon = "invisible"
        else:
            icon = _ICON_FOR_PRESENCE.get(self._server_status, "offline")
        self.status_applied.emit(icon)
