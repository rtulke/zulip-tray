from __future__ import annotations

import time
import unicodedata
from dataclasses import dataclass
from datetime import date

from . import zulip_api
from .i18n import tr

# How many 1:1 DM partners the tray's "Contacts" submenu lists below the
# favorites, and for how many of them the worker fetches the timestamp of
# the last message at connect time (one GET /messages call).
RECENT_LIMIT = 10
TIMESTAMP_FETCH_LIMIT = 50

# Day groups the recent DM partners are split into, newest first.
RECENCY_BUCKETS = ("today", "yesterday", "week", "older")


# DBusMenu labels are plain text, so presence is shown with colour emoji
# (rendered by GNOME Shell via Noto Color Emoji); the search window uses
# the same marks so both look alike.
PRESENCE_MARKS = {"active": "🟢", "idle": "🟠", "offline": "⚪"}


@dataclass(frozen=True)
class Contact:
    user_id: int
    full_name: str
    status: str  # "active" | "idle" | "offline"
    last_message_ts: float | None

    @property
    def presence_mark(self) -> str:
        return PRESENCE_MARKS.get(self.status, PRESENCE_MARKS["offline"])


def fold(text: str) -> str:
    """Case- and accent-insensitive form for the contact search, so
    "muller" finds "Müller" and "jose" finds "José"."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()


def recency_bucket(ts: float | None, now: float) -> str:
    """Which RECENCY_BUCKETS group a last DM falls into, by local calendar
    day: "week" covers the five days before yesterday. Without a timestamp
    (beyond TIMESTAMP_FETCH_LIMIT) the conversation is old by definition."""
    if not ts:
        return "older"
    days = (date.fromtimestamp(now) - date.fromtimestamp(ts)).days
    if days <= 0:  # a clock slightly behind the server counts as today
        return "today"
    if days == 1:
        return "yesterday"
    return "week" if days < 7 else "older"


def format_last_message(ts: float | None, now: float, day_in_header: bool = False) -> str:
    """Short local date of the last DM: "13:18" today, "gestern" (in the
    UI language) yesterday, "17.10." this year, "17.10.2025" before.
    With day_in_header the menu section already says today/yesterday, so
    those get the time of day instead."""
    if not ts:
        return ""
    then, today = time.localtime(ts), time.localtime(now)
    if then[:3] == today[:3]:
        return time.strftime("%H:%M", then)
    yesterday = time.localtime(now - 86400)
    if then[:3] == yesterday[:3]:
        return time.strftime("%H:%M", then) if day_in_header else tr("contacts.yesterday")
    day, month = f"{then.tm_mday:02d}", f"{then.tm_mon:02d}"
    if then.tm_year == today.tm_year:
        return tr("contacts.date_short", day=day, month=month)
    return tr("contacts.date_long", day=day, month=month, year=then.tm_year)


class ContactBook:
    """People the tray can offer as DM shortcuts: the realm's active human
    users (realm_user), their presence, and the 1:1 DM partners ordered by
    the newest message exchanged. Seeded from the register response
    (recent_private_conversations), then kept current by message,
    presence and realm_user events. Group DMs and DMs with oneself are
    deliberately left out -- the menu is about people, not threads.

    Built on the worker thread, then handed over to the main thread with
    the contacts_loaded signal; from then on only the main thread touches
    it (later events arrive through contact_event)."""

    def __init__(self, own_user_id: int, offline_threshold: float = zulip_api.DEFAULT_OFFLINE_THRESHOLD_SECONDS):
        self.own_user_id = own_user_id
        self.offline_threshold = offline_threshold
        self._users: dict[int, str] = {}  # active humans: user_id -> full_name
        self._last_message_id: dict[int, int] = {}  # 1:1 DM partner -> newest message id
        self._last_message_ts: dict[int, float] = {}
        self._presences: dict[int, dict] = {}

    @classmethod
    def from_registration(cls, registration: dict, own_user_id: int, offline_threshold: float) -> "ContactBook":
        book = cls(own_user_id, offline_threshold)
        for person in registration.get("realm_users") or []:
            book._add_person(person)
        for conversation in registration.get("recent_private_conversations") or []:
            user_ids = conversation.get("user_ids") or []
            if len(user_ids) == 1:  # [] = DMs with oneself, >1 = group DM
                uid = user_ids[0]
                book._last_message_id[uid] = max(book._last_message_id.get(uid, 0), conversation["max_message_id"])
        for uid, entry in (registration.get("presences") or {}).items():
            book._presences[int(uid)] = entry
        return book

    def _add_person(self, person: dict) -> None:
        if person.get("is_bot") or not person.get("is_active", True) or "user_id" not in person:
            return
        self._users[person["user_id"]] = person.get("full_name") or person.get("email", "")

    # --- timestamps -------------------------------------------------------------

    def message_ids_needing_timestamps(self, limit: int = TIMESTAMP_FETCH_LIMIT) -> list[int]:
        """The newest-message ids of the most recent DM partners, for one
        GET /messages?message_ids=... at connect time:
        recent_private_conversations carries ids only, no timestamps."""
        ids = sorted(self._last_message_id.values(), reverse=True)
        return ids[:limit]

    def note_timestamps(self, messages: list[dict]) -> None:
        by_id = {m["id"]: m.get("timestamp") for m in messages if "id" in m}
        for uid, message_id in self._last_message_id.items():
            if by_id.get(message_id):
                self._last_message_ts[uid] = by_id[message_id]

    # --- events --------------------------------------------------------------------

    def apply_event(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "message":
            self._note_message(event.get("message") or {})
        elif kind == "presence":
            for uid, entry in (event.get("presences") or {}).items():
                self._presences[int(uid)] = entry
        elif kind == "realm_user":
            person = event.get("person") or {}
            uid = person.get("user_id")
            op = event.get("op")
            if op == "add":
                self._add_person(person)
            elif op == "remove":
                self._users.pop(uid, None)
            elif op == "update":
                if person.get("is_active") is False:
                    self._users.pop(uid, None)
                elif "full_name" in person and uid in self._users:
                    self._users[uid] = person["full_name"]
                # A reactivation carries no name; the user reappears with
                # the next reconnect's realm_users.

    def _note_message(self, message: dict) -> None:
        # Our own outgoing DMs arrive as message events too, so writing to
        # someone moves them up just like receiving from them does.
        if message.get("type") != "private":
            return
        others = [r.get("id") for r in message.get("display_recipient") or [] if r.get("id") != self.own_user_id]
        if len(others) != 1:
            return
        uid, message_id = others[0], message.get("id", 0)
        if message_id >= self._last_message_id.get(uid, 0):
            self._last_message_id[uid] = message_id
            if message.get("timestamp"):
                self._last_message_ts[uid] = message["timestamp"]

    # --- queries -------------------------------------------------------------------

    def _contact(self, uid: int, now: float) -> Contact:
        return Contact(
            user_id=uid,
            full_name=self._users[uid],
            status=zulip_api.presence_status(self._presences.get(uid), now, self.offline_threshold),
            last_message_ts=self._last_message_ts.get(uid),
        )

    def recent(self, limit: int = RECENT_LIMIT, exclude=(), now: float | None = None) -> list[Contact]:
        """1:1 DM partners, newest conversation first (message ids grow
        monotonically, so they order as well as timestamps would)."""
        now = time.time() if now is None else now
        ordered = sorted(self._last_message_id.items(), key=lambda item: item[1], reverse=True)
        result = []
        for uid, _message_id in ordered:
            if uid in self._users and uid != self.own_user_id and uid not in exclude:
                result.append(self._contact(uid, now))
                if len(result) >= limit:
                    break
        return result

    def contacts(self, user_ids, now: float | None = None) -> list[Contact]:
        """The given users (favorites) sorted by name; deactivated or
        unknown ids are skipped."""
        now = time.time() if now is None else now
        found = [self._contact(uid, now) for uid in dict.fromkeys(user_ids) if uid in self._users]
        return sorted(found, key=lambda c: c.full_name.casefold())

    def search(self, query: str, favorites=(), now: float | None = None) -> list[Contact]:
        """Every active human except oneself whose name contains query
        (see fold). Names with a word starting with the query come first,
        then favorites, then the most recent DM partners, then the rest by
        name -- so an empty query lists favorites and recent people on top."""
        now = time.time() if now is None else now
        needle = fold(query.strip())
        favorites = set(favorites)
        ranked = []
        for uid, name in self._users.items():
            folded = fold(name)
            if uid == self.own_user_id or needle not in folded:
                continue
            word_start = not needle or any(word.startswith(needle) for word in folded.split())
            ranked.append(((not word_start, uid not in favorites, -self._last_message_id.get(uid, 0), folded), uid))
        return [self._contact(uid, now) for _key, uid in sorted(ranked)]

    def users(self) -> list[tuple[int, str]]:
        """Every active human except oneself, sorted by name -- the choice
        offered in the settings dialog's favorites page."""
        others = ((uid, name) for uid, name in self._users.items() if uid != self.own_user_id)
        return sorted(others, key=lambda item: item[1].casefold())
