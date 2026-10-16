from __future__ import annotations

import logging
import threading
from typing import Callable

from gi.repository import GLib

logger = logging.getLogger(__name__)


def run_on_main(fn: Callable, *args) -> None:
    """Schedule fn(*args) on the GLib main loop. The GTK equivalent of
    Qt's queued connection: every network thread in this app reports back
    through this, so widget code only ever runs on the main thread."""
    def _invoke() -> bool:
        try:
            fn(*args)
        except Exception:  # noqa: BLE001 - a handler bug must not kill the main loop
            logger.exception("Fehler im Main-Loop-Handler %r", fn)
        return False  # GLib.SOURCE_REMOVE: run once

    GLib.idle_add(_invoke)


class Signal:
    """Minimal multi-subscriber callback list. emit() runs the handlers
    inline (call it from the main thread); emit_from_thread() hops onto
    the main loop first, for use from worker threads."""

    def __init__(self, name: str = "") -> None:
        self._name = name
        self._handlers: list[Callable] = []

    def connect(self, handler: Callable) -> None:
        self._handlers.append(handler)

    def disconnect(self, handler: Callable) -> None:
        self._handlers = [h for h in self._handlers if h is not handler]

    def emit(self, *args) -> None:
        for handler in list(self._handlers):
            try:
                handler(*args)
            except Exception:  # noqa: BLE001
                logger.exception("Fehler im Handler fuer Signal %s", self._name)

    def emit_from_thread(self, *args) -> None:
        run_on_main(self.emit, *args)


def spawn(target: Callable, *args, name: str | None = None) -> threading.Thread:
    """Fire-and-forget daemon thread for blocking I/O initiated from the
    GUI side. Results come back via Signal.emit_from_thread/run_on_main."""
    thread = threading.Thread(target=target, args=args, daemon=True, name=name)
    thread.start()
    return thread
