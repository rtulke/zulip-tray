import time

from zulip_tray import contacts, i18n
from zulip_tray.contacts import ContactBook, format_last_message, recency_bucket

ME = 7
NOW = 1_800_000_000.0

REGISTRATION = {
    "realm_users": [
        {"user_id": 7, "full_name": "Me", "is_bot": False, "is_active": True},
        {"user_id": 8, "full_name": "Bob", "is_bot": False, "is_active": True},
        {"user_id": 9, "full_name": "alice", "is_bot": False, "is_active": True},
        {"user_id": 10, "full_name": "Carol", "is_bot": False, "is_active": True},
        {"user_id": 11, "full_name": "Some Bot", "is_bot": True, "is_active": True},
    ],
    # [] = notes to self, two ids = group DM, 6 = system bot not in realm_users
    "recent_private_conversations": [
        {"user_ids": [], "max_message_id": 120},
        {"user_ids": [8, 9], "max_message_id": 115},
        {"user_ids": [6], "max_message_id": 110},
        {"user_ids": [9], "max_message_id": 105},
        {"user_ids": [11], "max_message_id": 103},
        {"user_ids": [8], "max_message_id": 100},
    ],
    "presences": {"8": {"active_timestamp": NOW - 10}, "9": {"idle_timestamp": NOW - 30}},
}


def make() -> ContactBook:
    return ContactBook.from_registration(REGISTRATION, ME, 140)


def test_recent_only_single_human_partners_newest_first():
    book = make()
    recent = book.recent(now=NOW)
    assert [(c.user_id, c.status) for c in recent] == [(9, "idle"), (8, "active")]
    assert book.recent(limit=1, now=NOW)[0].user_id == 9
    assert [c.user_id for c in book.recent(exclude={9}, now=NOW)] == [8]


def test_timestamps_fetched_for_newest_ids():
    book = make()
    assert book.message_ids_needing_timestamps(limit=3) == [110, 105, 103]
    book.note_timestamps([{"id": 105, "timestamp": NOW - 60}, {"id": 999, "timestamp": 1}])
    by_id = {c.user_id: c.last_message_ts for c in book.recent(now=NOW)}
    assert by_id == {9: NOW - 60, 8: None}


def test_message_events_move_partner_up_but_ignore_groups_and_self():
    book = make()
    dm = {"type": "message", "message": {"id": 130, "type": "private", "timestamp": NOW,
                                          "display_recipient": [{"id": ME}, {"id": 8}]}}
    group = {"type": "message", "message": {"id": 131, "type": "private", "timestamp": NOW,
                                             "display_recipient": [{"id": ME}, {"id": 10}, {"id": 9}]}}
    self_note = {"type": "message", "message": {"id": 132, "type": "private", "display_recipient": [{"id": ME}]}}
    stale = {"type": "message", "message": {"id": 50, "type": "private", "timestamp": 1,
                                             "display_recipient": [{"id": ME}, {"id": 8}]}}
    for event in (dm, group, self_note, stale):
        book.apply_event(event)
    recent = book.recent(now=NOW)
    assert [c.user_id for c in recent] == [8, 9] and recent[0].last_message_ts == NOW


def test_presence_and_realm_user_events():
    book = make()
    book.apply_event({"type": "presence", "presences": {"10": {"active_timestamp": NOW}}})
    book.apply_event({"type": "realm_user", "op": "update", "person": {"user_id": 8, "full_name": "Robert"}})
    book.apply_event({"type": "realm_user", "op": "update", "person": {"user_id": 9, "is_active": False}})
    book.apply_event({"type": "realm_user", "op": "add", "person": {"user_id": 12, "full_name": "Dave", "is_bot": False}})
    assert [(c.full_name, c.status) for c in book.contacts([10, 8, 9, 99, 10], now=NOW)] == [
        ("Carol", "active"), ("Robert", "active"),
    ]
    assert book.users() == [(10, "Carol"), (12, "Dave"), (8, "Robert")]


def test_format_last_message(monkeypatch):
    monkeypatch.setattr(i18n, "LANGUAGE", "de")
    now = time.mktime((2026, 10, 20, 15, 0, 0, 0, 0, -1))
    at = lambda *ymdhm: time.mktime((*ymdhm, 0, 0, 0, -1))  # noqa: E731
    assert format_last_message(None, now) == ""
    assert format_last_message(at(2026, 10, 20, 9, 5), now) == "09:05"
    assert format_last_message(at(2026, 10, 19, 23, 59), now) == "gestern"
    assert format_last_message(at(2026, 3, 4, 12, 0), now) == "04.03."
    assert format_last_message(at(2025, 12, 31, 12, 0), now) == "31.12.2025"
    assert format_last_message(at(2026, 10, 19, 23, 59), now, day_in_header=True) == "23:59"
    assert contacts.RECENT_LIMIT == 10


def test_recency_bucket():
    now = time.mktime((2026, 10, 20, 0, 30, 0, 0, 0, -1))
    at = lambda *ymdhm: time.mktime((*ymdhm, 0, 0, 0, -1))  # noqa: E731
    assert recency_bucket(at(2026, 10, 20, 0, 1), now) == "today"
    assert recency_bucket(now + 60, now) == "today"
    assert recency_bucket(at(2026, 10, 19, 23, 59), now) == "yesterday"
    assert recency_bucket(at(2026, 10, 18, 0, 0), now) == "week"
    assert recency_bucket(at(2026, 10, 14, 0, 0), now) == "week"
    assert recency_bucket(at(2026, 10, 13, 23, 59), now) == "older"
    assert recency_bucket(None, now) == "older"


def test_search_ranks_word_starts_favorites_and_recent_first():
    people = {
        "realm_users": [
            {"user_id": 7, "full_name": "Me"},
            {"user_id": 20, "full_name": "Anna Müller"},
            {"user_id": 21, "full_name": "Hans Muster"},
            {"user_id": 22, "full_name": "Samuel Brand"},
            {"user_id": 23, "full_name": "José Mudd"},
        ],
        "recent_private_conversations": [{"user_ids": [22], "max_message_id": 9}],
    }
    book = ContactBook.from_registration(people, ME, 140)
    names = lambda query, favorites=(): [c.full_name for c in book.search(query, favorites, NOW)]  # noqa: E731
    assert names("") == ["Samuel Brand", "Anna Müller", "Hans Muster", "José Mudd"]
    assert names("", favorites=[23]) == ["José Mudd", "Samuel Brand", "Anna Müller", "Hans Muster"]
    # accent-insensitive; word starts beat matches inside a word ("saMUel")
    assert names("mu") == ["Anna Müller", "Hans Muster", "José Mudd", "Samuel Brand"]
    assert names("JOSE") == ["José Mudd"]
    assert names("me") == []  # never oneself


def test_unread_counts_per_person():
    registration = {**REGISTRATION, "unread_msgs": {"pms": [
        {"other_user_id": 9, "unread_message_ids": [101, 105]},
        {"other_user_id": ME, "unread_message_ids": [120]},  # notes to self
    ]}}
    book = ContactBook.from_registration(registration, ME, 140)
    unread = lambda: {c.user_id: c.unread for c in book.recent(now=NOW)}  # noqa: E731
    assert unread() == {9: 2, 8: 0}
    incoming = {"type": "message", "flags": [], "message": {
        "id": 130, "type": "private", "sender_id": 8, "display_recipient": [{"id": ME}, {"id": 8}]}}
    outgoing = {"type": "message", "flags": ["read"], "message": {
        "id": 131, "type": "private", "sender_id": ME, "display_recipient": [{"id": ME}, {"id": 8}]}}
    book.apply_event(incoming)
    book.apply_event(outgoing)
    assert unread() == {8: 1, 9: 2}
    book.apply_event({"type": "update_message_flags", "op": "add", "flag": "read", "messages": [105, 130], "all": False})
    assert unread() == {8: 0, 9: 1}
    book.apply_event({"type": "update_message_flags", "op": "remove", "flag": "read", "messages": [130],
                      "message_details": {"130": {"type": "private", "user_ids": [8]}}})
    assert unread() == {8: 1, 9: 1}
    book.apply_event({"type": "update_message_flags", "op": "add", "flag": "read", "messages": [], "all": True})
    assert unread() == {8: 0, 9: 0}


def test_bucket_in_range():
    from zulip_tray.contacts import bucket_in_range
    shown = lambda rng: [b for b in contacts.RECENCY_BUCKETS if bucket_in_range(b, rng)]  # noqa: E731
    assert shown("off") == []
    assert shown("today") == ["today"]
    assert shown("yesterday") == ["today", "yesterday"]
    assert shown("week") == ["today", "yesterday", "week"]
    assert shown("all") == ["today", "yesterday", "week", "older"]
