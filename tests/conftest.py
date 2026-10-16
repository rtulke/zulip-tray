import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# GTK widget tests need a display (CI runs them under xvfb-run); the
# protocol/logic tests don't.
HAS_DISPLAY = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
requires_display = pytest.mark.skipif(not HAS_DISPLAY, reason="no display for GTK")


def flush_main_loop() -> None:
    """Run every pending GLib idle callback (Signal.emit_from_thread hops
    through GLib.idle_add)."""
    from gi.repository import GLib

    context = GLib.MainContext.default()
    while context.pending():
        context.iteration(False)
