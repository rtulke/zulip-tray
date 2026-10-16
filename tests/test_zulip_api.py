import pytest

from zulip_tray import zulip_api


class FakeResponse:
    def __init__(self, status, body=None, text=""):
        self.status_code = status
        self._body = body
        self.text = text

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


def test_check_success_and_errors():
    assert zulip_api._check(FakeResponse(200, {"result": "success", "x": 1}))["x"] == 1
    with pytest.raises(zulip_api.AuthError):
        zulip_api._check(FakeResponse(401, {"result": "error", "msg": "Invalid API key", "code": "INVALID_API_KEY"}))
    with pytest.raises(zulip_api.ApiError) as info:
        zulip_api._check(FakeResponse(400, {"result": "error", "msg": "Bad event queue id", "code": "BAD_EVENT_QUEUE_ID"}))
    assert info.value.code == "BAD_EVENT_QUEUE_ID"
    with pytest.raises(zulip_api.ApiError) as info:
        zulip_api._check(FakeResponse(502, None, "<html>bad gateway"))
    assert info.value.code == "HTTP_502"


def test_presence_status_aggregation():
    now = 1_000_000
    assert zulip_api.presence_status(None, now) == "offline"
    assert zulip_api.presence_status({"active_timestamp": now - 30, "idle_timestamp": now - 10}, now) == "active"
    assert zulip_api.presence_status({"active_timestamp": now - 300, "idle_timestamp": now - 10}, now) == "idle"
    assert zulip_api.presence_status({"active_timestamp": now - 300, "idle_timestamp": now - 300}, now) == "offline"
    assert zulip_api.presence_status({"active_timestamp": now - 100}, now, offline_after=60) == "offline"


def test_own_presence_from_response():
    body = {"server_timestamp": 5000.5, "presences": {"7": {"active_timestamp": 4990, "idle_timestamp": 4000}}}
    assert zulip_api.own_presence_from_response(body, 7, 140) == "active"
    assert zulip_api.own_presence_from_response(body, 8, 140) is None
    assert zulip_api.own_presence_from_response({}, 7, 140) is None
