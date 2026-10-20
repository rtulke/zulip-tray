from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import quote

from .i18n import tr

# Flags Zulip attaches to a message for the receiving user. The wildcard
# ones split in Zulip 8 (feature level 224); the old name is kept as an
# alias by the server, so all three are checked.
PERSONAL_MENTION_FLAGS = ("mentioned", "group_mentioned")
WILDCARD_MENTION_FLAGS = ("wildcard_mentioned", "stream_wildcard_mentioned", "topic_wildcard_mentioned")
BODY_MAX_CHARS = 240

# user_topic visibility_policy values (Zulip 7+, feature level 170): a
# topic can be muted in an ordinary channel, unmuted in a muted channel,
# or followed; INHERIT means the channel's own mute state applies.
# Shape confirmed live against Zulip 12.3: register returns user_topics as
# [{stream_id, topic_name, visibility_policy, last_updated}].
TOPIC_INHERIT, TOPIC_MUTED, TOPIC_UNMUTED, TOPIC_FOLLOWED = 0, 1, 2, 3

_MENTION_RE = re.compile(r"@_?\*\*([^*]+)\*\*")
_CODE_FENCE_RE = re.compile(r"```[\w-]*")


@dataclass(frozen=True)
class Conversation:
    """What the tray's "missed messages" list and a notification click deal
    in: one DM thread or one channel topic, with the web-client URL
    fragment that opens it."""

    key: str
    label: str
    fragment: str
    is_dm: bool
    # Where a reply goes: the other DM participants, or channel + topic.
    recipient_ids: tuple[int, ...] = ()
    stream_id: int | None = None
    topic: str | None = None


@dataclass
class Notification:
    conversation: Conversation
    title: str
    body: str
    message_id: int
    extra_message_ids: set[int] = field(default_factory=set)


def encode_hash_component(text: str) -> str:
    """Zulip's own URL-fragment encoding: encodeURIComponent, then "." is
    escaped too and every "%" becomes "." -- so "Hello World" turns into
    "Hello.20World" exactly as the web client writes it."""
    encoded = quote(text, safe="!~*'()")
    return encoded.replace(".", "%2E").replace("%", ".")


def stream_fragment(stream_id: int, stream_name: str, topic: str | None = None) -> str:
    """#narrow/channel/<id>-<name>[/topic/<topic>] -- the server resolves
    the channel by the numeric id, the name part is decorative."""
    slug = encode_hash_component(f"{stream_id}-{stream_name.replace(' ', '-')}")
    fragment = f"narrow/channel/{slug}"
    if topic is not None:
        fragment += f"/topic/{encode_hash_component(topic)}"
    return fragment


def dm_fragment(user_ids: list[int], name: str) -> str:
    """#narrow/dm/<ids>-<slug>: the web client only reads the comma-joined
    ids before the dash, so the slug just has to be URL-safe."""
    ids = ",".join(str(uid) for uid in sorted(user_ids))
    slug = "group" if len(user_ids) > 1 else encode_hash_component(name.replace(" ", "-"))
    return f"narrow/dm/{ids}-{slug}"


def conversation_for(message: dict, own_user_id: int) -> Conversation:
    if message.get("type") == "private":
        recipients = message.get("display_recipient") or []
        others = [r for r in recipients if r.get("id") != own_user_id] or recipients
        ids = [r["id"] for r in others]
        sender = message.get("sender_full_name") or message.get("sender_email", "")
        if len(others) > 1:
            label = tr("notify.group_dm_title", sender=sender)
            name = "group"
        else:
            label = others[0].get("full_name") or sender if others else sender
            name = label
        return Conversation(key=f"dm:{','.join(str(i) for i in sorted(ids))}", label=label,
                            fragment=dm_fragment(ids, name), is_dm=True, recipient_ids=tuple(sorted(ids)))
    stream_id = message.get("stream_id")
    stream_name = message.get("display_recipient") if isinstance(message.get("display_recipient"), str) else ""
    topic = message.get("subject", "")
    return Conversation(
        key=f"topic:{stream_id}:{topic.casefold()}",
        label=tr("tray.missed_topic_entry", stream=stream_name, topic=topic),
        fragment=stream_fragment(stream_id, stream_name, topic),
        is_dm=False,
        stream_id=stream_id,
        topic=topic,
    )


def topic_key(stream_id: int | None, topic: str) -> tuple[int | None, str]:
    """Zulip treats topic names case-insensitively, and so do the
    user_topic lookups."""
    return stream_id, (topic or "").casefold()


def topic_policies_from(user_topics) -> dict[tuple[int | None, str], int]:
    """The register response's user_topics list as a lookup table;
    INHERIT entries (an undone mute/follow) carry no information."""
    policies = {}
    for entry in user_topics or []:
        policy = entry.get("visibility_policy", TOPIC_INHERIT)
        if policy != TOPIC_INHERIT:
            policies[topic_key(entry.get("stream_id"), entry.get("topic_name", ""))] = policy
    return policies


def clean_content(text: str) -> str:
    """Markdown source -> something readable in a one-line notification:
    @**Name** mentions lose their markup, code fences their backticks,
    whitespace collapses, and it's cut at BODY_MAX_CHARS."""
    text = _MENTION_RE.sub(r"@\1", text or "")
    text = _CODE_FENCE_RE.sub("", text)
    text = " ".join(text.split())
    if len(text) > BODY_MAX_CHARS:
        text = text[: BODY_MAX_CHARS - 1].rstrip() + "…"
    return text


def _stream_setting(subscription: dict, user_settings: dict, sub_key: str, global_key: str) -> bool:
    """Per-stream override if set (not None), else the account default --
    the notification_settings_null client capability makes the server
    send null for "no override"."""
    value = subscription.get(sub_key)
    if value is None:
        value = user_settings.get(global_key, False)
    return bool(value)


def evaluate_message(
    message: dict, flags: list[str], own_user_id: int, subscriptions: dict[int, dict], user_settings: dict,
    topic_policies: dict | None = None,
) -> Notification | None:
    """Zulip sends no "notify the user" event -- the client decides from
    the message plus the user's own notification settings, mirroring the
    web client's rules: direct messages and personal mentions when desktop
    notifications are on; wildcard mentions and ordinary channel messages
    according to the channel's (or the account-wide) settings, never from
    muted channels or muted topics. A topic unmuted inside a muted channel
    counts as not muted; a followed topic notifies per the account's
    followed-topic settings even where the channel would stay quiet. Own
    and already-read messages never notify."""
    if message.get("sender_id") == own_user_id or "read" in flags:
        return None

    conversation = conversation_for(message, own_user_id)
    sender = message.get("sender_full_name") or message.get("sender_email", "")
    content = clean_content(message.get("content", ""))

    if message.get("type") == "private":
        if not user_settings.get("enable_desktop_notifications", True):
            return None
        body = content if user_settings.get("pm_content_in_desktop_notifications", True) else tr("notify.new_dm")
        return Notification(conversation=conversation, title=conversation.label, body=body,
                            message_id=message["id"])

    subscription = subscriptions.get(message.get("stream_id"))
    if subscription is None:
        return None
    policy = (topic_policies or {}).get(topic_key(message.get("stream_id"), message.get("subject", "")), TOPIC_INHERIT)
    followed = policy == TOPIC_FOLLOWED
    muted = policy == TOPIC_MUTED or (
        bool(subscription.get("is_muted")) and policy not in (TOPIC_UNMUTED, TOPIC_FOLLOWED)
    )
    personal_mention = any(flag in flags for flag in PERSONAL_MENTION_FLAGS)
    wildcard_mention = any(flag in flags for flag in WILDCARD_MENTION_FLAGS)
    alert_word = "has_alert_word" in flags

    notify = False
    if alert_word or personal_mention:
        notify = user_settings.get("enable_desktop_notifications", True)
    elif muted:
        notify = False
    elif wildcard_mention:
        notify = (followed and user_settings.get("enable_followed_topic_wildcard_mentions_notify", True)) or \
            _stream_setting(subscription, user_settings, "wildcard_mentions_notify", "wildcard_mentions_notify")
    else:
        notify = (followed and user_settings.get("enable_followed_topic_desktop_notifications", True)) or \
            _stream_setting(subscription, user_settings, "desktop_notifications", "enable_stream_desktop_notifications")
    if not notify:
        return None

    title = tr(
        "notify.stream_title", sender=sender,
        stream=message.get("display_recipient", ""), topic=message.get("subject", ""),
    )
    return Notification(conversation=conversation, title=title, body=content, message_id=message["id"])
