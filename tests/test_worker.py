from conftest import flush_main_loop

from zulip_tray.worker import SessionState, ZulipWorker

SUB = {"stream_id": 3, "name": "general", "is_muted": False, "desktop_notifications": True}


def make():
    worker = ZulipWorker("https://chat.example.com", True, lambda: None)
    state = SessionState(user_id=7, email="me@example.com", full_name="Me", subscriptions={3: dict(SUB)},
                         user_settings={"enable_desktop_notifications": True, "presence_enabled": True})
    seen = {}
    for name in ("notification", "own_presence", "user_setting_changed", "messages_read", "contact_event"):
        getattr(worker, name).connect(lambda *args, n=name: seen.setdefault(n, []).append(args))
    return worker, state, seen


def test_message_event_becomes_notification():
    worker, state, seen = make()
    worker._handle_event({"type": "message", "flags": [], "message": {
        "id": 1, "type": "stream", "sender_id": 9, "sender_full_name": "Bob", "stream_id": 3,
        "display_recipient": "general", "subject": "t", "content": "hi"}}, state)
    flush_main_loop()
    assert seen["notification"][0][0].conversation.key == "topic:3:t"


def test_presence_event_only_for_own_user(monkeypatch):
    worker, state, seen = make()
    import time
    now = time.time()
    worker._handle_event({"type": "presence", "presences": {"8": {"active_timestamp": now}}}, state)
    worker._handle_event({"type": "presence", "presences": {"7": {"idle_timestamp": now - 5}}}, state)
    flush_main_loop()
    assert seen["own_presence"] == [("idle",)]


def test_settings_subscription_and_read_events():
    worker, state, seen = make()
    worker._handle_event({"type": "user_settings", "op": "update", "property": "presence_enabled", "value": False}, state)
    worker._handle_event({"type": "subscription", "op": "update", "stream_id": 3, "property": "is_muted", "value": True}, state)
    worker._handle_event({"type": "subscription", "op": "add", "subscriptions": [{"stream_id": 4, "name": "new"}]}, state)
    worker._handle_event({"type": "subscription", "op": "remove", "subscriptions": [{"stream_id": 3}]}, state)
    worker._handle_event({"type": "update_message_flags", "op": "add", "flag": "read", "messages": [1, 2], "all": False}, state)
    worker._handle_event({"type": "update_message_flags", "op": "add", "flag": "read", "messages": [], "all": True}, state)
    worker._handle_event({"type": "update_message_flags", "op": "remove", "flag": "read", "messages": [1], "all": False}, state)
    worker._handle_event({"type": "heartbeat"}, state)
    flush_main_loop()
    assert state.user_settings["presence_enabled"] is False
    assert seen["user_setting_changed"] == [("presence_enabled", False)]
    assert 3 not in state.subscriptions and state.subscriptions[4]["name"] == "new"
    assert seen["messages_read"] == [([1, 2],), (None,)]


def test_contact_relevant_events_are_forwarded():
    worker, state, seen = make()
    dm = {"type": "message", "flags": [], "message": {
        "id": 2, "type": "private", "sender_id": 9, "sender_full_name": "Bob", "content": "hi",
        "display_recipient": [{"id": 7, "full_name": "Me"}, {"id": 9, "full_name": "Bob"}]}}
    channel = {"type": "message", "flags": [], "message": {
        "id": 3, "type": "stream", "sender_id": 9, "stream_id": 3, "display_recipient": "general", "subject": "t"}}
    presence = {"type": "presence", "presences": {"9": {"active_timestamp": 1}}}
    realm_user = {"type": "realm_user", "op": "update", "person": {"user_id": 9, "full_name": "Robert"}}
    for event in (dm, channel, presence, realm_user):
        worker._handle_event(event, state)
    flush_main_loop()
    assert seen["contact_event"] == [(dm,), (presence,), (realm_user,)]
