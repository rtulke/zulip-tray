from conftest import flush_main_loop

from zulip_tray.contacts import ContactBook
from zulip_tray.presence import PresenceCoordinator
from zulip_tray.worker import SessionState


class Settings:
    def __init__(self):
        self.forced_status = "auto"
        self.idle_detection_enabled = True
        self.saved = 0

    def save(self):
        self.saved += 1


class Idle:
    is_idle = False


class FakeApi:
    def __init__(self):
        self.calls = []

    def set_presence_enabled(self, enabled):
        self.calls.append(("presence_enabled", enabled))

    def update_presence(self, status, new_user_input):
        self.calls.append(("ping", status, new_user_input))
        return {"server_timestamp": 1000, "presences": {"7": {"active_timestamp": 999 if status == "active" else 0,
                                                              "idle_timestamp": 999}}}


class FakeWorker:
    def __init__(self):
        self.api = FakeApi()
        self.state = SessionState(user_id=7, email="me@example.com", full_name="Me")


def wait_threads():
    import threading
    for t in threading.enumerate():
        if t is not threading.current_thread() and t.daemon:
            t.join(timeout=2)
    flush_main_loop()


def test_report_status_mapping():
    settings, idle = Settings(), Idle()
    pc = PresenceCoordinator(settings, FakeWorker(), idle)
    assert pc._report_status() == ("active", True)
    idle.is_idle = True
    assert pc._report_status() == ("idle", False)
    settings.forced_status = "online"
    assert pc._report_status() == ("active", True)
    settings.forced_status = "away"
    idle.is_idle = False
    assert pc._report_status() == ("idle", False)
    settings.idle_detection_enabled = False
    settings.forced_status = "auto"
    idle.is_idle = True
    assert pc._report_status() == ("active", True)


def test_ping_response_keeps_others_presence_current_in_contact_book():
    # No presence event arrives while someone stays active, so only the ping
    # response keeps them from going stale in the DM menu.
    worker = FakeWorker()
    book = ContactBook(own_user_id=7, offline_threshold=140)
    book._add_person({"user_id": 8, "full_name": "Alice"})
    book.apply_event({"type": "presence", "presences": {"8": {"active_timestamp": 1000}}})
    assert book.contacts([8], now=1200)[0].status == "offline"

    worker.api.update_presence = lambda *_args: {
        "server_timestamp": 1200, "presences": {"8": {"active_timestamp": 1190}},
    }
    pc = PresenceCoordinator(Settings(), worker, Idle())
    pc.presences_received.connect(book.apply_event)
    pc._ping()
    wait_threads()
    assert book.contacts([8], now=1200)[0].status == "active"


def test_icon_follows_server_and_invisible_mode():
    settings, worker = Settings(), FakeWorker()
    pc = PresenceCoordinator(settings, worker, Idle())
    icons = []
    pc.status_applied.connect(icons.append)
    pc.handle_connected(worker.state)
    wait_threads()
    assert ("ping", "active", True) in worker.api.calls
    assert icons[-1] == "online"
    pc.note_server_presence("idle")
    assert icons[-1] == "away"
    # invisible chosen locally -> pushed to the server, icon grey
    settings.forced_status = "invisible"
    pc.apply()
    wait_threads()
    assert ("presence_enabled", False) in worker.api.calls and icons[-1] == "invisible"
    # web client turns presence back on -> forced status follows
    forced = []
    pc.forced_status_changed.connect(forced.append)
    pc.note_user_setting("presence_enabled", True)
    assert forced == ["auto"] and settings.forced_status == "auto" and settings.saved >= 1
    pc.handle_disconnected()
    assert icons[-1] == "offline"
    pc._stop_pinging()
