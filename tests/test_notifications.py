from zulip_tray.notifications import (
    TOPIC_FOLLOWED, TOPIC_MUTED, TOPIC_UNMUTED, clean_content, conversation_for, dm_fragment,
    encode_hash_component, evaluate_message, offers_reply, stream_fragment, topic_policies_from,
)

OWN = 7
ALICE = {"id": 5, "full_name": "Alice Example", "email": "alice@example.com"}
ME = {"id": OWN, "full_name": "Me", "email": "me@example.com"}
BOB = {"id": 9, "full_name": "Bob", "email": "bob@example.com"}
SETTINGS = {
    "enable_desktop_notifications": True,
    "enable_stream_desktop_notifications": False,
    "wildcard_mentions_notify": True,
    "pm_content_in_desktop_notifications": True,
}
SUBS = {
    3: {"stream_id": 3, "name": "general", "is_muted": False, "desktop_notifications": None, "wildcard_mentions_notify": None},
    4: {"stream_id": 4, "name": "loud", "is_muted": False, "desktop_notifications": True, "wildcard_mentions_notify": None},
    5: {"stream_id": 5, "name": "muted", "is_muted": True, "desktop_notifications": True, "wildcard_mentions_notify": None},
}


def dm(sender=ALICE, recipients=(ALICE, ME), content="hello", msg_id=100):
    return {"id": msg_id, "type": "private", "sender_id": sender["id"], "sender_full_name": sender["full_name"],
            "sender_email": sender["email"], "display_recipient": list(recipients), "content": content}


def stream_msg(stream_id=3, name="general", topic="Lunch", content="who's in?", msg_id=200, sender=BOB):
    return {"id": msg_id, "type": "stream", "sender_id": sender["id"], "sender_full_name": sender["full_name"],
            "stream_id": stream_id, "display_recipient": name, "subject": topic, "content": content}


def test_hash_encoding_matches_web_client():
    assert encode_hash_component("Hello World") == "Hello.20World"
    assert encode_hash_component("a.b/c") == "a.2Eb.2Fc"
    assert encode_hash_component("Grüezi") == "Gr.C3.BCezi"


def test_fragments():
    assert stream_fragment(3, "Big Room", "Lunch Plans") == "narrow/channel/3-Big-Room/topic/Lunch.20Plans"
    assert stream_fragment(3, "general") == "narrow/channel/3-general"
    assert dm_fragment([5], "Alice Example") == "narrow/dm/5-Alice-Example"
    assert dm_fragment([9, 5], "x") == "narrow/dm/5,9-group"


def test_dm_notifies_and_has_conversation():
    n = evaluate_message(dm(), [], OWN, SUBS, SETTINGS)
    assert n is not None
    assert n.conversation.key == "dm:5"
    assert n.conversation.is_dm and n.conversation.fragment == "narrow/dm/5-Alice-Example"
    assert n.title == "Alice Example" and n.body == "hello" and n.message_id == 100


def test_group_dm_key_excludes_self_and_is_sorted():
    n = evaluate_message(dm(recipients=(BOB, ME, ALICE)), [], OWN, SUBS, SETTINGS)
    assert n.conversation.key == "dm:5,9"
    assert "(" in n.conversation.label  # "<sender> (group)" in some language


def test_own_and_read_messages_never_notify():
    assert evaluate_message(dm(sender=ME), [], OWN, SUBS, SETTINGS) is None
    assert evaluate_message(dm(), ["read"], OWN, SUBS, SETTINGS) is None


def test_dm_respects_global_switch_and_content_privacy():
    assert evaluate_message(dm(), [], OWN, SUBS, {**SETTINGS, "enable_desktop_notifications": False}) is None
    n = evaluate_message(dm(), [], OWN, SUBS, {**SETTINGS, "pm_content_in_desktop_notifications": False})
    assert n.body != "hello"


def test_stream_uses_per_stream_override_then_global_default():
    assert evaluate_message(stream_msg(3), [], OWN, SUBS, SETTINGS) is None  # None -> global False
    assert evaluate_message(stream_msg(3), [], OWN, SUBS, {**SETTINGS, "enable_stream_desktop_notifications": True})
    n = evaluate_message(stream_msg(4, "loud"), [], OWN, SUBS, SETTINGS)
    assert n is not None and n.conversation.key == "topic:4:lunch"
    assert n.conversation.fragment == "narrow/channel/4-loud/topic/Lunch"
    assert "Bob" in n.title and "#loud" in n.title and "Lunch" in n.title


def test_muted_stream_only_notifies_on_personal_mention():
    assert evaluate_message(stream_msg(5, "muted"), [], OWN, SUBS, SETTINGS) is None
    assert evaluate_message(stream_msg(5, "muted"), ["stream_wildcard_mentioned"], OWN, SUBS, SETTINGS) is None
    assert evaluate_message(stream_msg(5, "muted"), ["mentioned"], OWN, SUBS, SETTINGS) is not None


def test_wildcard_mention_follows_wildcard_setting():
    assert evaluate_message(stream_msg(3), ["wildcard_mentioned"], OWN, SUBS, SETTINGS) is not None
    assert evaluate_message(stream_msg(3), ["wildcard_mentioned"], OWN, SUBS,
                            {**SETTINGS, "wildcard_mentions_notify": False}) is None


def test_unknown_stream_is_ignored():
    assert evaluate_message(stream_msg(99, "ghost"), [], OWN, SUBS, SETTINGS) is None


def test_clean_content():
    assert clean_content("hi @**Alice Example** and @_**Bob**\n\n```python\nx\n```  done") == "hi @Alice Example and @Bob x done"
    assert len(clean_content("x" * 1000)) <= 240 and clean_content("x" * 1000).endswith("…")


def test_topic_key_is_case_insensitive():
    a = conversation_for(stream_msg(3, topic="Lunch"), OWN)
    b = conversation_for(stream_msg(3, topic="lunch"), OWN)
    assert a.key == b.key


def test_muted_unmuted_and_followed_topics():
    loud = {**SETTINGS, "enable_stream_desktop_notifications": True}
    policies = topic_policies_from([
        {"stream_id": 4, "topic_name": "Noise", "visibility_policy": TOPIC_MUTED},
        {"stream_id": 5, "topic_name": "Important", "visibility_policy": TOPIC_UNMUTED},
        {"stream_id": 3, "topic_name": "Release", "visibility_policy": TOPIC_FOLLOWED},
        {"stream_id": 5, "topic_name": "Watched", "visibility_policy": TOPIC_FOLLOWED},
        {"stream_id": 3, "topic_name": "Undone", "visibility_policy": 0},
    ])
    assert (3, "undone") not in policies
    # muted topic in a loud channel: quiet, except personal mentions; case-insensitive
    assert evaluate_message(stream_msg(4, "loud", "noise"), [], OWN, SUBS, SETTINGS, policies) is None
    assert evaluate_message(stream_msg(4, "loud", "Noise"), ["wildcard_mentioned"], OWN, SUBS, loud, policies) is None
    assert evaluate_message(stream_msg(4, "loud", "Noise"), ["mentioned"], OWN, SUBS, SETTINGS, policies)
    assert evaluate_message(stream_msg(4, "loud", "Other"), [], OWN, SUBS, SETTINGS, policies)
    # unmuted topic in a muted channel behaves like an ordinary topic there
    assert evaluate_message(stream_msg(5, "muted", "Important"), [], OWN, SUBS, SETTINGS, policies)
    # followed: notifies although channel notifications are off, per the followed-topic setting
    assert evaluate_message(stream_msg(3, topic="Release"), [], OWN, SUBS, SETTINGS, policies)
    assert evaluate_message(stream_msg(5, "muted", "Watched"), [], OWN, SUBS, SETTINGS, policies)
    assert evaluate_message(stream_msg(3, topic="Release"), [], OWN, SUBS,
                            {**SETTINGS, "enable_followed_topic_desktop_notifications": False}, policies) is None


def test_conversation_carries_reply_target():
    direct = conversation_for(dm(), OWN)
    assert direct.recipient_ids == (5,) and direct.stream_id is None
    group = conversation_for(dm(recipients=(ALICE, ME, BOB)), OWN)
    assert group.recipient_ids == (5, 9)
    channel = conversation_for(stream_msg(topic="Lunch Plans"), OWN)
    assert (channel.stream_id, channel.topic, channel.recipient_ids) == (3, "Lunch Plans", ())


def test_reply_button_for_dms_always_for_channels_on_request():
    assert offers_reply(conversation_for(dm(), OWN), False)
    assert not offers_reply(conversation_for(stream_msg(), OWN), False)
    assert offers_reply(conversation_for(stream_msg(), OWN), True)
