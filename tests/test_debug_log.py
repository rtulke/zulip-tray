import logging
import socket
import struct

import pytest

from zulip_tray import debug_log


def parse_datagram(data: bytes) -> dict[str, str]:
    fields = {}
    while data:
        name, data = data.split(b"\n", 1)
        length = struct.unpack("<Q", data[:8])[0]
        fields[name.decode()] = data[8:8 + length].decode()
        data = data[8 + length + 1:]
    return fields


@pytest.fixture
def journal(tmp_path):
    path = tmp_path / "journal.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    server.bind(str(path))
    server.settimeout(2)
    yield server, str(path)
    debug_log.configure(False)
    server.close()


def test_enabled_sends_debug_lines_to_the_journal(journal):
    server, path = journal
    debug_log.configure(True, path)
    assert "Diagnose-Log aktiv" in parse_datagram(server.recv(65536))["MESSAGE"]
    logging.getLogger("zulip_tray.presence").debug("erste Zeile\nzweite Zeile")
    fields = parse_datagram(server.recv(65536))
    assert fields["MESSAGE"] == "zulip_tray.presence: erste Zeile\nzweite Zeile"
    assert fields["SYSLOG_IDENTIFIER"] == "zulip-tray" and fields["PRIORITY"] == "7"


def test_switching_off_restores_the_quiet_default(journal):
    server, path = journal
    debug_log.configure(True, path)
    server.recv(65536)
    debug_log.configure(False, path)
    package = logging.getLogger("zulip_tray")
    assert package.handlers == [] and package.propagate and package.level == logging.NOTSET
    server.settimeout(0)
    logging.getLogger("zulip_tray.presence").debug("nicht sichtbar")
    with pytest.raises(BlockingIOError):
        server.recv(65536)


def test_without_journald_the_lines_stay_on_stderr(tmp_path):
    debug_log.configure(True, str(tmp_path / "missing.sock"))
    package = logging.getLogger("zulip_tray")
    try:
        assert package.handlers == [] and package.propagate and package.level == logging.DEBUG
    finally:
        debug_log.configure(False)


def test_a_vanished_journal_does_not_raise(tmp_path):
    handler = debug_log.JournalHandler(str(tmp_path / "gone.sock"))
    handler.emit(logging.LogRecord("zulip_tray.x", logging.INFO, "", 0, "text", None, None))
    handler.close()
