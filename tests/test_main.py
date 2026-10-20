import os
import subprocess
import sys
from pathlib import Path

from conftest import requires_display

ROOT = Path(__file__).resolve().parent.parent

# Runs the real Gtk.Application in a child process under a test app id:
# startup must reach the end (0.4.0 crashed in do_startup, which leaves a
# half-started primary instance on the session bus that silently swallows
# every later launch).
STARTUP_SCRIPT = """
import copy, sys
from gi.repository import GLib
from zulip_tray import main
from zulip_tray.config import DEFAULT_SETTINGS, UserSettings

main.APP_ID = "io.github.rtulke.ZulipTrayStartupTest"
main.UserSettings.load = classmethod(lambda cls: UserSettings(copy.deepcopy(DEFAULT_SETTINGS), path=None))
main.effective_admin_config = lambda _s: type("Cfg", (), {"server_url": "https://chat.example.com", "verify_ssl": True})()
main.ZulipTrayApplication._credentials = lambda self: None

def fake_login(self, first_run=False):
    for name in ("settings", "color_scheme", "tray", "notifier", "room_opener", "presence"):
        assert hasattr(self, name), name
    GLib.timeout_add(100, lambda: self.quit_app() or False)

main.ZulipTrayApplication.show_login = fake_login
sys.exit(main.ZulipTrayApplication().run([]))
"""


@requires_display
def test_application_starts_up_completely():
    result = subprocess.run(
        [sys.executable, "-c", STARTUP_SCRIPT], cwd=ROOT, capture_output=True, text=True, timeout=30,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
    )
    assert "Traceback" not in result.stderr, result.stderr
    assert result.returncode == 0, result.stderr
