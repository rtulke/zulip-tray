from __future__ import annotations

import logging
import os
import socket
import struct

from . import __version__

logger = logging.getLogger(__name__)

JOURNAL_SOCKET = "/run/systemd/journal/socket"
SYSLOG_IDENTIFIER = "zulip-tray"
PACKAGE_LOGGER = "zulip_tray"

_SYSLOG_PRIORITIES = ((logging.CRITICAL, 2), (logging.ERROR, 3), (logging.WARNING, 4), (logging.INFO, 6))
_DEBUG_PRIORITY = 7


def _journal_field(name: str, value: str) -> bytes:
    # journald's native protocol in its binary-safe form (name, length as
    # 64-bit little endian, value), so a multi-line traceback stays one entry.
    data = value.encode("utf-8", "replace")
    return name.encode("ascii") + b"\n" + struct.pack("<Q", len(data)) + data + b"\n"


def _syslog_priority(levelno: int) -> int:
    for threshold, priority in _SYSLOG_PRIORITIES:
        if levelno >= threshold:
            return priority
    return _DEBUG_PRIORITY


class JournalHandler(logging.Handler):
    """Writes straight to journald's socket, so `journalctl -t zulip-tray`
    finds the lines however the app was started: stderr only reaches the
    journal from a GNOME launch, not from a terminal. The socket keeps the
    package free of a python3-systemd dependency."""

    def __init__(self, path: str = JOURNAL_SOCKET):
        super().__init__()
        self._path = path
        self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            payload = b"".join((
                _journal_field("MESSAGE", f"{record.name}: {self.format(record)}"),
                _journal_field("PRIORITY", str(_syslog_priority(record.levelno))),
                _journal_field("SYSLOG_IDENTIFIER", SYSLOG_IDENTIFIER),
            ))
            self._socket.sendto(payload, self._path)
        except (OSError, ValueError):
            pass  # a diagnostic aid must never disturb the app

    def close(self) -> None:
        self._socket.close()
        super().close()


def configure(enabled: bool, socket_path: str = JOURNAL_SOCKET) -> None:
    """Applies the settings switch, also while running. Only this package's
    logger changes level: urllib3's debug output stays out of the journal.
    On a system without journald the lines go to stderr instead."""
    package = logging.getLogger(PACKAGE_LOGGER)
    for handler in list(package.handlers):
        if isinstance(handler, JournalHandler):
            package.removeHandler(handler)
            handler.close()
    use_journal = enabled and os.path.exists(socket_path)
    if use_journal:
        package.addHandler(JournalHandler(socket_path))
    # While on, the journal gets everything; propagating as well would write
    # each line a second time through the stderr stream.
    package.propagate = not use_journal
    package.setLevel(logging.DEBUG if enabled else logging.NOTSET)
    if enabled:
        logger.debug("Diagnose-Log aktiv (Version %s)", __version__)
