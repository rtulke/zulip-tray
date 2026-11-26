from zulip_tray.mainloop import Signal


class Listener:
    def __init__(self):
        self.calls = 0

    def on_event(self):
        self.calls += 1


def test_disconnect_finds_a_bound_method():
    signal, first, second = Signal("x"), Listener(), Listener()
    signal.connect(first.on_event)
    signal.connect(second.on_event)
    signal.emit()
    signal.disconnect(first.on_event)
    signal.emit()
    assert (first.calls, second.calls) == (1, 2)
