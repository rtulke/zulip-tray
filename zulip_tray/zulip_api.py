from __future__ import annotations

import json
import logging
import time

import requests

from . import __version__

logger = logging.getLogger(__name__)

# Zulip reports a user as "offline" once no client has pinged for this
# long; the register response carries the server's actual value
# (server_presence_offline_threshold_seconds), this is only the fallback.
DEFAULT_OFFLINE_THRESHOLD_SECONDS = 140
DEFAULT_LONGPOLL_TIMEOUT_SECONDS = 90
CONNECT_TIMEOUT_SECONDS = 15
REQUEST_TIMEOUT_SECONDS = 20
USER_AGENT = f"ZulipTray/{__version__}"

# Event types this client consumes (see worker.py). "heartbeat" events
# arrive regardless; presence pings are a separate endpoint, not events.
EVENT_TYPES = [
    "message", "presence", "user_settings", "subscription", "update_message_flags", "realm_user", "user_topic",
]
# Initial state only: recent_private_conversations has no events of its
# own, the client keeps it current from message events (contacts.py).
FETCH_EVENT_TYPES = [*EVENT_TYPES, "recent_private_conversations"]
# Zulip's user setting color_scheme, part of user_settings (present in the
# register response, confirmed live on Zulip 12.3).
COLOR_SCHEME_IDS = {"system": 1, "dark": 2, "light": 3}
COLOR_SCHEME_NAMES = {value: name for name, value in COLOR_SCHEME_IDS.items()}
CLIENT_CAPABILITIES = {
    # desktop_notifications on a subscription may then be null, meaning
    # "use the account-wide default" -- see notifications.py.
    "notification_settings_null": True,
    # user_settings as one object in the register response (and
    # user_settings events for later changes) rather than the legacy
    # scattering of top-level fields.
    "user_settings_object": True,
    # Presence events carry the modern {active_timestamp, idle_timestamp}
    # format keyed by user id (Zulip 11+, feature level 419).
    "simplified_presence_events": True,
    "empty_topic_name": True,
}


class ApiError(Exception):
    """Any failed Zulip API call. `code` is Zulip's machine-readable error
    code (e.g. BAD_EVENT_QUEUE_ID) when the server sent one, "CONNECTION"
    for network-level failures, otherwise "HTTP_<status>"."""

    def __init__(self, message: str, code: str | None = None, status: int | None = None):
        super().__init__(message)
        self.code = code
        self.status = status


class AuthError(ApiError):
    """The server rejected the credentials (HTTP 401, or a wrong password
    on fetch_api_key)."""


def _check(response: requests.Response) -> dict:
    """Turn a Zulip JSON response into a dict or raise ApiError/AuthError.
    Zulip always answers {"result": "success"|"error", "msg": ..., ...}."""
    try:
        body = response.json()
    except ValueError as exc:
        raise ApiError(
            f"Unerwartete Antwort ({response.status_code}, kein JSON)", code=f"HTTP_{response.status_code}",
            status=response.status_code,
        ) from exc
    if response.status_code == 401:
        raise AuthError(body.get("msg", "Nicht autorisiert"), code=body.get("code", "UNAUTHORIZED"), status=401)
    if response.status_code >= 400 or body.get("result") != "success":
        raise ApiError(
            body.get("msg", f"HTTP {response.status_code}"), code=body.get("code", f"HTTP_{response.status_code}"),
            status=response.status_code,
        )
    return body


def get_server_settings(server_url: str, verify_ssl: bool = True) -> dict:
    """Unauthenticated GET /api/v1/server_settings: which login methods
    exist (password? external OIDC/SAML providers and their login URLs),
    realm name, Zulip version/feature level."""
    try:
        response = requests.get(
            f"{server_url}/api/v1/server_settings", verify=verify_ssl,
            timeout=(CONNECT_TIMEOUT_SECONDS, REQUEST_TIMEOUT_SECONDS), headers={"User-Agent": USER_AGENT},
        )
    except requests.RequestException as exc:
        raise ApiError(str(exc), code="CONNECTION") from exc
    return _check(response)


def fetch_api_key(server_url: str, email: str, password: str, verify_ssl: bool = True) -> tuple[str, str]:
    """POST /api/v1/fetch_api_key -- only works on servers with a password
    backend (EmailAuthBackend/LDAP). Servers that only offer external
    (OIDC/SAML) login use the mobile flow in auth.py instead. Returns (email, api_key) with
    the server's canonical spelling of the address."""
    try:
        response = requests.post(
            f"{server_url}/api/v1/fetch_api_key", data={"username": email, "password": password},
            verify=verify_ssl, timeout=(CONNECT_TIMEOUT_SECONDS, REQUEST_TIMEOUT_SECONDS),
            headers={"User-Agent": USER_AGENT},
        )
    except requests.RequestException as exc:
        raise ApiError(str(exc), code="CONNECTION") from exc
    if response.status_code == 403:
        # "Your username or password is incorrect" comes back as 403, not 401.
        raise AuthError("invalid_credentials", code="INVALID_CREDENTIALS", status=403)
    body = _check(response)
    return body.get("email", email), body["api_key"]


class ZulipApi:
    """Authenticated REST client for one account. Plain blocking calls --
    never use from the GUI thread (see mainloop.spawn / worker.py)."""

    def __init__(self, server_url: str, email: str, api_key: str, verify_ssl: bool = True):
        self.server_url = server_url.rstrip("/")
        self.email = email
        self._session = requests.Session()
        self._session.auth = (email, api_key)
        self._session.verify = verify_ssl
        self._session.headers["User-Agent"] = USER_AGENT

    def _request(self, method: str, path: str, *, timeout: float | tuple = REQUEST_TIMEOUT_SECONDS, **kwargs) -> dict:
        try:
            response = self._session.request(
                method, f"{self.server_url}/api/v1/{path}", timeout=(CONNECT_TIMEOUT_SECONDS, timeout)
                if not isinstance(timeout, tuple) else timeout, **kwargs,
            )
        except requests.RequestException as exc:
            raise ApiError(str(exc), code="CONNECTION") from exc
        return _check(response)

    # --- account ---------------------------------------------------------------

    def get_me(self) -> dict:
        """GET /users/me: user_id, email, full_name, ... Also the cheapest
        way to validate an API key (401 -> AuthError)."""
        return self._request("GET", "users/me")

    # --- event queue -------------------------------------------------------------

    def register(self) -> dict:
        """POST /register with exactly what this client needs. The response
        carries the initial state for the requested event types:
        subscriptions (per-stream notification flags), user_settings,
        user_topics (muted/followed topics), presences, realm_users and recent_private_conversations (the
        tray's contacts menu), plus queue_id/last_event_id and the
        server's own presence/long-poll timing constants."""
        return self._request(
            "POST", "register",
            data={
                "event_types": json.dumps(EVENT_TYPES),
                "fetch_event_types": json.dumps(FETCH_EVENT_TYPES),
                "client_capabilities": json.dumps(CLIENT_CAPABILITIES),
                # Markdown source, not rendered HTML: notification bodies are
                # plain text anyway, and the source reads better there than
                # stripped HTML would.
                "apply_markdown": "false",
                "client_gravatar": "true",
                "include_subscribers": "false",
                "slim_presence": "true",
            },
        )

    def get_events(self, queue_id: str, last_event_id: int, longpoll_timeout: float) -> list[dict]:
        """Long-poll GET /events. Blocks until events arrive or the server's
        heartbeat fires (well under `longpoll_timeout`, which the register
        response says to use as the HTTP read timeout)."""
        body = self._request(
            "GET", "events", params={"queue_id": queue_id, "last_event_id": last_event_id},
            timeout=(CONNECT_TIMEOUT_SECONDS, longpoll_timeout + 10),
        )
        return body.get("events", [])

    def get_messages_by_id(self, message_ids: list[int]) -> list[dict]:
        """GET /messages?message_ids=[...] (Zulip 10+, feature level 300):
        the contacts menu needs the timestamps behind the bare ids in
        recent_private_conversations."""
        if not message_ids:
            return []
        body = self._request(
            "GET", "messages",
            params={"message_ids": json.dumps(message_ids), "apply_markdown": "false", "client_gravatar": "true"},
        )
        return body.get("messages", [])

    def delete_queue(self, queue_id: str) -> None:
        """Best-effort DELETE /events. Also the trick for waking a thread
        blocked in get_events(): the pending long-poll then returns a
        BAD_EVENT_QUEUE_ID error immediately."""
        try:
            self._request("DELETE", "events", params={"queue_id": queue_id}, timeout=5)
        except ApiError as exc:
            logger.debug("Event-Queue %s konnte nicht geloescht werden: %s", queue_id, exc)

    # --- presence / status -------------------------------------------------------

    def update_presence(self, status: str, new_user_input: bool) -> dict:
        """POST /users/me/presence. `status` is "active" or "idle". The
        response (ping_only=false) includes every user's presence in the
        modern format plus server_timestamp -- that is how this client
        learns its *own* aggregated status across all its sessions."""
        return self._request(
            "POST", "users/me/presence",
            data={
                "status": status,
                "new_user_input": "true" if new_user_input else "false",
                "ping_only": "false",
                "slim_presence": "true",
            },
        )

    def set_presence_enabled(self, enabled: bool) -> None:
        """PATCH /settings presence_enabled -- false is Zulip's "invisible
        mode" (everyone else sees you as offline)."""
        self._request("PATCH", "settings", data={"presence_enabled": "true" if enabled else "false"})

    def set_color_scheme(self, scheme: str) -> None:
        """PATCH /settings color_scheme -- the web client's own theme
        setting (Personal settings > Preferences), so it applies to every
        browser this account uses, not only the one the tray opens."""
        self._request("PATCH", "settings", data={"color_scheme": COLOR_SCHEME_IDS[scheme]})

    def set_status_text(self, text: str) -> None:
        """POST /users/me/status. Zulip caps status_text at 60 characters;
        an empty string clears it."""
        self._request("POST", "users/me/status", data={"status_text": text[:60]})

    # --- messages ----------------------------------------------------------------

    def send_message(self, recipient_type: str, to, content: str, topic: str | None = None) -> int:
        """POST /messages. recipient_type "direct" (to = list of emails or
        user ids) or "channel" (to = stream name or id, plus topic).
        Returns the new message id."""
        data = {"type": recipient_type, "to": json.dumps(to) if isinstance(to, list) else to, "content": content}
        if recipient_type == "channel":
            data["topic"] = topic or ""
        body = self._request("POST", "messages", data=data)
        return body["id"]


def presence_status(entry: dict | None, now: float, offline_after: float = DEFAULT_OFFLINE_THRESHOLD_SECONDS) -> str:
    """Collapse one user's modern-format presence entry
    ({active_timestamp, idle_timestamp}, each the newest ping of that kind
    across all their clients) into active/idle/offline the same way the
    Zulip web client does it: the most recent "active" ping within the
    offline threshold wins, then "idle", else offline."""
    if not entry:
        return "offline"
    active_ts = entry.get("active_timestamp") or 0
    idle_ts = entry.get("idle_timestamp") or 0
    if active_ts and now - active_ts < offline_after:
        return "active"
    if idle_ts and now - idle_ts < offline_after:
        return "idle"
    return "offline"


def own_presence_from_response(body: dict, user_id: int, offline_after: float) -> str | None:
    """Own aggregated status from a presence-ping response or the register
    response (both: "presences" keyed by user id as a string, and
    "server_timestamp"). None if the server didn't include us."""
    presences = body.get("presences") or {}
    entry = presences.get(str(user_id))
    if entry is None:
        return None
    return presence_status(entry, body.get("server_timestamp") or time.time(), offline_after)
