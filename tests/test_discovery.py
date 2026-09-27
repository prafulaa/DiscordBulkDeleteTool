from PIL import Image
from test_api_client import FakeResponse

import theme
from api_client import DiscordClient


def make_client(responses):
    client = DiscordClient("fake-token")
    session = __import__("unittest").mock.MagicMock()
    session.request.side_effect = responses
    client.session = session
    return client


def test_fetch_guilds_returns_list_and_limit():
    client = make_client(
        [FakeResponse(200, [{"id": "1", "name": "Guild", "icon": "abc"}])]
    )
    guilds = client.fetch_guilds()
    assert guilds == [{"id": "1", "name": "Guild", "icon": "abc"}]
    _args, kwargs = client.session.request.call_args
    assert kwargs["params"]["limit"] == 200


def test_fetch_guild_channels_filters_text_like_and_sorts():
    channels = [
        {"id": "a", "type": 4, "name": "category", "position": 0},
        {"id": "b", "type": 0, "name": "general", "position": 1},
        {"id": "c", "type": 5, "name": "news", "position": 0},
        {"id": "d", "type": 2, "name": "voice", "position": 2},
    ]
    client = make_client([FakeResponse(200, channels)])
    result = client.fetch_guild_channels("999")
    assert [c["id"] for c in result] == ["c", "b"]  # position 0 first


def test_fetch_dm_channels_normalizes():
    channels = [
        {
            "id": "dm1",
            "type": 1,
            "recipients": [{"id": "u1", "username": "alice", "global_name": "Alice", "avatar": "h"}],
        },
        {
            "id": "dm2",
            "type": 3,
            "recipients": [{"id": "u2", "username": "bob", "avatar": None}, {"id": "u3"}],
        },
        {"id": "voice", "type": 2, "name": "not a dm"},
    ]
    client = make_client([FakeResponse(200, channels)])
    dms = client.fetch_dm_channels()
    assert [d["id"] for d in dms] == ["dm1", "dm2"]
    assert dms[0]["name"] == "Alice"
    assert dms[1]["name"] == "Group (2 members)"
    assert dms[0]["recipient"]["username"] == "alice"


def test_circular_mask_crops_to_transparent_corners():
    img = Image.new("RGB", (40, 20), (200, 30, 30))
    result = theme.circular(img)
    assert result.size == (20, 20)
    assert result.getpixel((1, 1))[3] == 0      # corner outside circle
    assert result.getpixel((10, 10))[3] == 255  # center inside circle


def test_fetch_discord_image_empty_url():
    assert theme.fetch_discord_image(None, 32) is None
    assert theme.fetch_discord_image("", 32) is None


def test_guild_icon_url_none_without_hash():
    assert theme.guild_icon_url("123", None) is None
    assert "icons/123/abc.png" in theme.guild_icon_url("123", "abc")
