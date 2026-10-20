import types

import pytest

from zulip_tray.notifications import Conversation, Notification

notifier = pytest.importorskip("zulip_tray.notifier")


def make(click_action, reply_button="direct"):
    manager = notifier.NotificationManager.__new__(notifier.NotificationManager)
    manager._settings = types.SimpleNamespace(click_action=click_action, reply_button=reply_button)
    seen = []
    manager._on_conversation_clicked = lambda key: seen.append(("open", key))
    manager._on_reply = lambda n, token: seen.append(("reply", n.conversation.key))
    return manager, seen


def note(is_dm):
    conv = Conversation(key="dm:5" if is_dm else "topic:3:t", label="x", fragment="", is_dm=is_dm)
    return Notification(conversation=conv, title="t", body="b", message_id=1)


def test_click_opens_reply_window_only_where_a_reply_is_offered():
    manager, seen = make("reply")
    notice = types.SimpleNamespace()
    manager._handle_action(notice, "default", note(True))
    manager._handle_action(notice, "default", note(False))  # channel, reply only for DMs
    assert seen == [("reply", "dm:5"), ("open", "topic:3:t")]


def test_click_opens_chat_by_default_and_when_replies_are_off():
    for click_action, reply_button in (("open", "all"), ("reply", "off")):
        manager, seen = make(click_action, reply_button)
        manager._handle_action(types.SimpleNamespace(), "default", note(True))
        assert seen == [("open", "dm:5")]
