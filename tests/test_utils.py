import threading
import time
from datetime import datetime

import pytest

import utils


def test_snowflake_date_roundtrip():
    snowflake = utils.date_to_snowflake("2015-01-01")
    assert snowflake is not None
    dt = utils.snowflake_to_datetime(snowflake)
    assert dt.year == 2015 and dt.month == 1 and dt.day == 1
    assert dt.utcoffset().total_seconds() == 0


def test_date_to_snowflake_end_of_day():
    snowflake = utils.date_to_snowflake("2020-06-15", end_of_day=True)
    dt = utils.snowflake_to_datetime(snowflake)
    assert (dt.hour, dt.minute, dt.second) == (23, 59, 59)


def test_date_to_snowflake_invalid():
    assert utils.date_to_snowflake("not a date") is None
    assert utils.date_to_snowflake("") is None
    assert utils.date_to_snowflake(None) is None


def test_parse_date():
    parsed = utils.parse_date("2024-05-01")
    assert parsed is not None
    assert parsed.tzinfo is not None
    assert (parsed.year, parsed.month, parsed.day) == (2024, 5, 1)
    assert utils.parse_date("garbage") is None


def test_snowflake_to_datetime_invalid():
    assert utils.snowflake_to_datetime("abc") is None
    assert utils.snowflake_to_datetime(None) is None


def test_validate_snowflake():
    assert utils.validate_snowflake("123456789012345678")
    assert utils.validate_snowflake("  123456789012345678 ")
    assert not utils.validate_snowflake("123")
    assert not utils.validate_snowflake("abcdefghijklmno")
    assert not utils.validate_snowflake("")
    assert not utils.validate_snowflake(None)


def test_display_username_retired_discriminator():
    user = {"username": "alice", "discriminator": "0", "global_name": None}
    assert utils.display_username(user) == "alice"


def test_display_username_global_name():
    user = {"username": "alice", "discriminator": "0", "global_name": "Alice A"}
    assert utils.display_username(user) == "Alice A"


def test_display_username_legacy_discriminator():
    user = {"username": "alice", "discriminator": "1234"}
    assert utils.display_username(user) == "alice#1234"


def test_display_username_none():
    assert utils.display_username(None) == "Unknown User"


def test_mask_token():
    token = "a" * 30 + "." + "b" * 6 + "." + "c" * 30
    masked = utils.mask_token(token)
    assert token not in masked
    assert masked.startswith("a" * 8)
    assert "c" * 4 in masked
    assert utils.mask_token("") == "(empty)"
    assert utils.mask_token(None) == "(empty)"


def test_format_discord_timestamp_shape():
    formatted = utils.format_discord_timestamp("2024-01-05T15:30:00+00:00")
    assert formatted.endswith(("AM", "PM"))
    assert "2024" in formatted


def test_format_discord_timestamp_fallback():
    assert utils.format_discord_timestamp(None) == "unknown date"
    assert utils.format_discord_timestamp("not-a-date") == "not-a-date"


def test_sleep_with_cancel_returns_early():
    cancel = threading.Event()
    cancel.set()
    start = time.monotonic()
    utils.sleep_with_cancel(5.0, cancel)
    assert time.monotonic() - start < 1.0


def test_sleep_with_cancel_without_event_sleeps():
    start = time.monotonic()
    utils.sleep_with_cancel(0.2)
    assert time.monotonic() - start >= 0.15


def test_version_string():
    assert utils.VERSION


@pytest.mark.parametrize(
    "iso,valid",
    [
        ("2024-01-05T15:30:00+00:00", True),
        ("2024-01-05T15:30:00Z", True),
        ("2024-01-05T15:30:00.616000+00:00", True),
        ("2024-01-05", True),
    ],
)
def test_parse_date_formats(iso, valid):
    parsed = utils.parse_date(iso)
    assert (parsed is not None) == valid
    if valid:
        assert isinstance(parsed, datetime)
        assert parsed.tzinfo is not None
