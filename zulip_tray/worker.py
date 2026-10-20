from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass, field

from . import notifications, zulip_api
from .contacts import ContactBook
from .mainloop import Signal, spawn

logger = logging.getLogger(__name__)

BACKOFF_BASE_SECONDS = 2.0
BACKOFF_CAP_SECONDS = 60.0
AUTH_RETRY_SECONDS = 300.0  # safety-net retry interval while in the auth-error state


class _AuthFailure(Exception):
    """Raised internally when the server rejects the stored credentials."""


@dataclass
class SessionState:
    """What the event loop needs to turn a message event into a
    notification decision: the subscription table (per-stream
    notification flags, mute state), muted/followed topics and the user's
    notification settings, all seeded from the register response and kept
    current by subscription/user_topic/user_settings events."""

    user_id: int
    email: str
    full_name: str
    subscriptions: dict[int, dict] = field(default_factory=dict)
    user_settings: dict = field(default_factory=dict)
    topic_policies: dict = field(default_factory=dict)  # notifications.topic_key -> visibility_policy
    offline_threshold: float = zulip_api.DEFAULT_OFFLINE_THRESHOLD_SECONDS
    longpoll_timeout: float = zulip_api.DEFAULT_LONGPOLL_TIMEOUT_SECONDS


class ZulipWorker(threading.Thread):
    """Owns the Zulip event queue on a background thread: register, then
    long-poll /events until told otherwise, reconnecting with backoff.
    Talks to the rest of the app exclusively through Signals, which hop
    onto the GLib main loop (Signal.emit_from_thread), so handlers may
    touch widgets freely.

    Presence pings are *not* done here -- they depend on main-thread
    state (idle watcher, forced status) and run from presence.py on a
    timer, using the `api` this worker exposes while connected."""

    def __init__(self, server_url: str, verify_ssl: bool, credentials_provider):
        super().__init__(daemon=True, name="zulip-worker")
        self._server_url = server_url
        self._verify_ssl = verify_ssl
        # Callable returning (email, api_key) or None -- read on every
        # (re)connect so a re-login is picked up without restarting the thread.
        self._credentials_provider = credentials_provider
        self._stop_requested = False
        self._reconfigure_requested = False
        self._wake_event = threading.Event()
        self._backoff = BACKOFF_BASE_SECONDS
        self._queue_id: str | None = None

        # Exposed while a session is fully connected (None otherwise) for
        # main-thread callers (presence pings, status text, settings).
        # Attribute assignment is atomic in CPython; readers must cope with
        # it flipping to None at any moment.
        self.api: zulip_api.ZulipApi | None = None
        self.state: SessionState | None = None

        self.connecting = Signal("connecting")
        self.connected = Signal("connected")  # (SessionState)
        self.disconnected = Signal("disconnected")  # (reason: str)
        self.reconnect_scheduled = Signal("reconnect_scheduled")  # (seconds: float)
        self.auth_failed = Signal("auth_failed")  # (message: str)
        self.notification = Signal("notification")  # (notifications.Notification)
        self.own_presence = Signal("own_presence")  # (status: "active"|"idle"|"offline")
        self.user_setting_changed = Signal("user_setting_changed")  # (property: str, value)
        self.messages_read = Signal("messages_read")  # (message_ids: list[int])
        self.contacts_loaded = Signal("contacts_loaded")  # (ContactBook), owned by the main thread from then on
        self.contact_event = Signal("contact_event")  # (event: dict), for ContactBook.apply_event

    # --- control (any thread) -------------------------------------------------------

    def stop(self) -> None:
        self._stop_requested = True
        self._wake_event.set()
        self._abort_longpoll()

    def retry_now(self) -> None:
        """Wake the worker immediately, e.g. after a re-login."""
        self._wake_event.set()

    def update_server(self, server_url: str, verify_ssl: bool) -> None:
        """Point the worker at a different server and reconnect right away,
        even mid-session."""
        self._server_url = server_url
        self._verify_ssl = verify_ssl
        self._reconfigure_requested = True
        self._wake_event.set()
        self._abort_longpoll()

    def _abort_longpoll(self) -> None:
        # A thread blocked in GET /events can't be interrupted directly;
        # deleting the queue from another thread makes that request return
        # at once with BAD_EVENT_QUEUE_ID, which the loop then treats as
        # the reconnect/stop it is.
        api, queue_id = self.api, self._queue_id
        if api is not None and queue_id:
            spawn(api.delete_queue, queue_id, name="zulip-queue-abort")

    # --- thread body --------------------------------------------------------------------

    def run(self) -> None:
        while not self._stop_requested:
            self.connecting.emit_from_thread()
            try:
                self._run_one_session()
            except _AuthFailure as exc:
                self.auth_failed.emit_from_thread(str(exc))
                if self._sleep_interruptible(AUTH_RETRY_SECONDS):
                    return
                continue
            except zulip_api.ApiError as exc:
                self.disconnected.emit_from_thread(str(exc))
            except Exception as exc:  # noqa: BLE001 - last-resort guard for a background thread
                logger.exception("Unerwarteter Fehler im Zulip-Worker")
                self.disconnected.emit_from_thread(str(exc))

            if self._stop_requested:
                return
            if self._reconfigure_requested:
                self._reconfigure_requested = False
                self._backoff = BACKOFF_BASE_SECONDS
                continue

            delay = random.uniform(0, min(BACKOFF_CAP_SECONDS, self._backoff))
            self._backoff = min(BACKOFF_CAP_SECONDS, self._backoff * 2)
            self.reconnect_scheduled.emit_from_thread(delay)
            if self._sleep_interruptible(delay):
                return

    def _sleep_interruptible(self, seconds: float) -> bool:
        """Sleep up to `seconds`, waking early on stop()/retry_now().
        Returns True if the run loop should end."""
        self._wake_event.wait(timeout=seconds)
        self._wake_event.clear()
        return self._stop_requested

    def _run_one_session(self) -> None:
        credentials = self._credentials_provider()
        if not credentials:
            raise _AuthFailure("Keine Anmeldedaten hinterlegt")
        email, api_key = credentials
        api = zulip_api.ZulipApi(self._server_url, email, api_key, self._verify_ssl)

        try:
            me = api.get_me()
            registration = api.register()
        except zulip_api.AuthError as exc:
            raise _AuthFailure(str(exc)) from exc

        state = SessionState(user_id=me["user_id"], email=me.get("email", email), full_name=me.get("full_name", ""))
        state.subscriptions = {
            sub["stream_id"]: sub for sub in registration.get("subscriptions", []) if "stream_id" in sub
        }
        state.user_settings = dict(registration.get("user_settings") or {})
        state.topic_policies = notifications.topic_policies_from(registration.get("user_topics"))
        state.offline_threshold = registration.get(
            "server_presence_offline_threshold_seconds", zulip_api.DEFAULT_OFFLINE_THRESHOLD_SECONDS,
        )
        state.longpoll_timeout = registration.get(
            "event_queue_longpoll_timeout_seconds", zulip_api.DEFAULT_LONGPOLL_TIMEOUT_SECONDS,
        )
        queue_id = registration["queue_id"]
        last_event_id = registration["last_event_id"]
        contacts = ContactBook.from_registration(registration, state.user_id, state.offline_threshold)
        try:
            contacts.note_timestamps(api.get_messages_by_id(contacts.message_ids_needing_timestamps()))
        except zulip_api.AuthError as exc:
            raise _AuthFailure(str(exc)) from exc
        except zulip_api.ApiError as exc:
            # Only the dates in the contacts menu are missing then; not
            # worth failing the session over.
            logger.warning("Zeitstempel der letzten Direktnachrichten nicht abrufbar: %s", exc)

        self._backoff = BACKOFF_BASE_SECONDS  # reset only after a fully successful registration
        self._queue_id = queue_id
        self.state = state
        self.api = api
        self.connected.emit_from_thread(state)
        self.contacts_loaded.emit_from_thread(contacts)
        own_status = zulip_api.own_presence_from_response(registration, state.user_id, state.offline_threshold)
        if own_status:
            self.own_presence.emit_from_thread(own_status)
        try:
            while not self._stop_requested and not self._reconfigure_requested:
                events = api.get_events(queue_id, last_event_id, state.longpoll_timeout)
                for event in events:
                    last_event_id = max(last_event_id, event.get("id", last_event_id))
                    self._handle_event(event, state)
        finally:
            self.api = None
            self.state = None
            self._queue_id = None
            api.delete_queue(queue_id)

    # --- events -------------------------------------------------------------------------

    def _handle_event(self, event: dict, state: SessionState) -> None:
        kind = event.get("type")
        if kind == "message":
            notification = notifications.evaluate_message(
                event.get("message", {}), event.get("flags", []), state.user_id, state.subscriptions,
                state.user_settings, state.topic_policies,
            )
            if notification:
                self.notification.emit_from_thread(notification)
            if event.get("message", {}).get("type") == "private":
                self.contact_event.emit_from_thread(event)
        elif kind == "presence":
            self.contact_event.emit_from_thread(event)
            # Modern format (simplified_presence_events): "presences" keyed
            # by user id. Compared against the local clock -- the server
            # timestamp isn't included in this format, and a 140 s window
            # tolerates ordinary clock skew.
            entry = (event.get("presences") or {}).get(str(state.user_id))
            if entry is not None:
                self.own_presence.emit_from_thread(
                    zulip_api.presence_status(entry, time.time(), state.offline_threshold)
                )
        elif kind == "user_settings" and event.get("op") == "update":
            prop, value = event.get("property"), event.get("value")
            if prop:
                state.user_settings[prop] = value
                self.user_setting_changed.emit_from_thread(prop, value)
        elif kind == "subscription":
            self._handle_subscription_event(event, state)
        elif kind == "user_topic":
            key = notifications.topic_key(event.get("stream_id"), event.get("topic_name", ""))
            policy = event.get("visibility_policy", notifications.TOPIC_INHERIT)
            if policy == notifications.TOPIC_INHERIT:
                state.topic_policies.pop(key, None)
            else:
                state.topic_policies[key] = policy
        elif kind == "realm_user":
            self.contact_event.emit_from_thread(event)
        elif kind == "update_message_flags" and event.get("flag") == "read" and event.get("op") == "add":
            if event.get("all"):
                self.messages_read.emit_from_thread(None)  # None = everything
            else:
                self.messages_read.emit_from_thread(list(event.get("messages") or []))

    @staticmethod
    def _handle_subscription_event(event: dict, state: SessionState) -> None:
        op = event.get("op")
        if op == "add":
            for sub in event.get("subscriptions", []):
                if "stream_id" in sub:
                    state.subscriptions[sub["stream_id"]] = sub
        elif op == "remove":
            for sub in event.get("subscriptions", []):
                state.subscriptions.pop(sub.get("stream_id"), None)
        elif op == "update":
            sub = state.subscriptions.get(event.get("stream_id"))
            if sub is not None and event.get("property"):
                sub[event["property"]] = event.get("value")
