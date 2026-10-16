from zulip_tray.cli import resolve_target


def test_resolve_target():
    assert resolve_target("#general", "example.com") == ("channel", "general")
    assert resolve_target("alice", "example.com") == ("direct", ["alice@example.com"])
    assert resolve_target("@alice", "example.com") == ("direct", ["alice@example.com"])
    assert resolve_target("x@example.org", "example.com") == ("direct", ["x@example.org"])
    assert resolve_target("alice", "") == ("direct", ["alice"])
