from unittest.mock import MagicMock

import pytest
import requests

import api_client
from api_client import (
    AuthenticationError,
    DiscordAPIError,
    DiscordClient,
    NetworkError,
)


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, headers=None, text=""):
        self.status_code = status_code
        self._json = json_data
        self.headers = headers or {}
        self.text = text

    def json(self):
        if self._json is None:
            raise ValueError("No JSON body")
        return self._json


def make_client(responses):
    """A DiscordClient whose session returns the scripted responses in order."""
    client = DiscordClient("fake-token")
    session = MagicMock()
    session.request.side_effect = responses
    client.session = session
    return client


def test_validate_token_success():
    client = make_client(
        [FakeResponse(200, {"id": "12345", "username": "tester", "discriminator": "0"})]
    )
    user = client.validate_token()
    assert user["username"] == "tester"
    assert client.user_id == "12345"
    _, kwargs = client.session.request.call_args
    assert kwargs["timeout"] == api_client.REQUEST_TIMEOUT


def test_validate_token_invalid_raises_authentication_error():
    client = make_client([FakeResponse(401, {"message": "Unauthorized"})])
    with pytest.raises(AuthenticationError):
        client.validate_token()


def test_rate_limit_is_retried():
    client = make_client(
        [
            FakeResponse(429, {"retry_after": 0.01}),
            FakeResponse(429, {"retry_after": 0.01}),
            FakeResponse(200, {"messages": [], "total_results": 0}),
        ]
    )
    data = client.request_json("GET", "/guilds/1/messages/search")
    assert data["total_results"] == 0
    assert client.session.request.call_count == 3


def test_retry_after_header_preferred():
    response = FakeResponse(429, {"retry_after": 5.0}, headers={"Retry-After": "2.5"})
    assert api_client.DiscordClient._retry_after_seconds(response) == 2.5


def test_retry_after_fallback_to_body():
    response = FakeResponse(429, {"retry_after": 3.0})
    assert api_client.DiscordClient._retry_after_seconds(response) == 3.0


def test_retry_after_clamped():
    response = FakeResponse(429, {"retry_after": 9999.0})
    assert api_client.DiscordClient._retry_after_seconds(response) == 120.0


def test_network_error_retries_then_succeeds(monkeypatch):
    monkeypatch.setattr(api_client.time, "sleep", lambda _s: None)
    client = DiscordClient("fake-token")
    session = MagicMock()
    session.request.side_effect = [
        requests.ConnectionError("boom"),
        FakeResponse(200, {"id": "1"}),
    ]
    client.session = session
    assert client.validate_token() == {"id": "1"}


def test_network_error_exhausted_raises(monkeypatch):
    monkeypatch.setattr(api_client.time, "sleep", lambda _s: None)
    client = DiscordClient("fake-token")
    session = MagicMock()
    session.request.side_effect = requests.ConnectionError("boom")
    client.session = session
    with pytest.raises(NetworkError):
        client.validate_token()


def test_api_error_includes_discord_message():
    client = make_client([FakeResponse(403, {"message": "Missing Access"})])
    with pytest.raises(DiscordAPIError, match="Missing Access"):
        client.request_json("GET", "/guilds/1/messages/search")


def test_non_json_response_raises():
    client = make_client([FakeResponse(503, None, text="<html>cloudflare</html>")])
    with pytest.raises(DiscordAPIError, match="cloudflare"):
        client.request_json("GET", "/users/@me")


def test_search_params_and_endpoint_guild():
    client = make_client([FakeResponse(200, {"messages": [], "total_results": 0})])
    client.search_messages(guild_id="999", author_id="42", content="hello", offset=25)
    args, kwargs = client.session.request.call_args
    assert args[1].endswith("/guilds/999/messages/search")
    params = kwargs["params"]
    assert params["author_id"] == "42"
    assert params["content"] == "hello"
    assert params["offset"] == 25
    assert params["limit"] == api_client.SEARCH_PAGE_SIZE


def test_search_params_and_endpoint_channel():
    client = make_client([FakeResponse(200, {"messages": [], "total_results": 0})])
    client.search_messages(channel_id="777", min_id="111", max_id="222")
    args, kwargs = client.session.request.call_args
    assert args[1].endswith("/channels/777/messages/search")
    assert kwargs["params"]["min_id"] == "111"
    assert kwargs["params"]["max_id"] == "222"


def test_search_requires_context():
    client = make_client([])
    with pytest.raises(ValueError):
        client.search_messages()


@pytest.mark.parametrize(
    "status,expected",
    [(204, "deleted"), (404, "already_gone"), (403, "forbidden"), (500, "failed")],
)
def test_delete_message_statuses(status, expected):
    client = make_client([FakeResponse(status)])
    assert client.delete_message("chan", "msg") == expected


def test_delete_message_url():
    client = make_client([FakeResponse(204)])
    client.delete_message("chan1", "msg2")
    method, _kwargs = client.session.request.call_args
    assert method == ("DELETE", f"{api_client.API_BASE}/channels/chan1/messages/msg2")
