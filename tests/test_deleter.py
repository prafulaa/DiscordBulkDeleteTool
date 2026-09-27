import threading

import pytest

import deleter as deleter_module
from api_client import DiscordAPIError
from deleter import MessageDeleter


def raw_msg(i, author_id="111", content="hello"):
    """A raw history-API message dict (no channel_id — the API omits it)."""
    return {
        "id": str(i),
        "channel_id": "555",
        "content": content,
        "timestamp": "2024-01-01T00:00:00+00:00",
        "attachments": [],
        "author": {"id": author_id},
    }


def raw_page(start, count, author_id="111"):
    """One history page: `count` messages, newest-first ids from `start`."""
    return [raw_msg(start - i, author_id=author_id) for i in range(count)]


class FakeClient:
    """Scripted history/delete double for MessageDeleter tests."""

    def __init__(self, pages=None, delete_statuses=None, errors=None):
        self.user_id = "111"
        self._pages = list(pages or [])
        self._page_index = 0
        self._errors = list(errors or [])
        self.delete_statuses = list(delete_statuses or [])
        self.delete_calls = []
        self.history_calls = []

    def fetch_history(self, channel_id, before=None, after=None, limit=100):
        self.history_calls.append(
            {"channel_id": channel_id, "before": before, "limit": limit}
        )
        if self._page_index < len(self._pages):
            page = self._pages[self._page_index]
            self._page_index += 1
            return page
        if self._errors:
            raise self._errors.pop(0)
        return []

    def delete_message(self, channel_id, message_id):
        self.delete_calls.append((channel_id, message_id))
        if self.delete_statuses:
            return self.delete_statuses.pop(0)
        return "deleted"


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(deleter_module, "sleep_with_cancel", lambda *a, **k: None)


def test_scan_single_page():
    client = FakeClient(pages=[raw_page(1000, 3)])
    messages, errors = MessageDeleter(client).scan_messages("999")
    assert errors == []
    assert len(messages) == 3
    assert len(client.history_calls) == 1
    assert client.history_calls[0]["channel_id"] == "999"
    assert client.history_calls[0]["before"] is None
    assert client.history_calls[0]["limit"] == 100


def test_scan_stamps_channel_id_and_filters_author():
    page = [raw_msg(1002), raw_msg(1001, author_id="999"), raw_msg(1000)]
    client = FakeClient(pages=[page])
    messages, _errors = MessageDeleter(client).scan_messages("777")
    assert [m["id"] for m in messages] == ["1002", "1000"]
    assert all(m["channel_id"] == "777" for m in messages)


def test_scan_multi_page_pagination():
    client = FakeClient(
        pages=[raw_page(1000, 100), raw_page(900, 100)]  # each exactly full
    )
    messages, errors = MessageDeleter(client).scan_messages("999")
    assert errors == []
    assert len(messages) == 200
    assert client.history_calls[1]["before"] == "901"  # oldest id of page 1


def test_scan_stops_when_page_not_full():
    client = FakeClient(pages=[raw_page(1000, 100), raw_page(900, 10)])
    MessageDeleter(client).scan_messages("999")
    assert len(client.history_calls) == 2  # short page = end of history


def test_scan_early_exit_below_min_id():
    client = FakeClient(pages=[raw_page(1000, 100), raw_page(900, 100)])
    messages, errors = MessageDeleter(client).scan_messages("999", min_id="950")
    # ids >= 950 kept (1000..950 = 51), and the second page is never fetched
    assert len(messages) == 51
    assert len(client.history_calls) == 1


def test_scan_max_id_upper_bound():
    client = FakeClient(pages=[raw_page(1002, 3)])  # ids 1002, 1001, 1000
    messages, _errors = MessageDeleter(client).scan_messages("999", max_id="1002")
    assert [m["id"] for m in messages] == ["1001", "1000"]


def test_scan_keyword_filter():
    page = [
        raw_msg(1002, content="game night tonight"),
        raw_msg(1001, content="random thought"),
        raw_msg(1000, content="GAME NIGHT reminder"),
    ]
    client = FakeClient(pages=[page])
    messages, _errors = MessageDeleter(client).scan_messages(
        "999", content_query="game night"
    )
    assert [m["id"] for m in messages] == ["1002", "1000"]


def test_scan_newest_first_sorting():
    client = FakeClient(pages=[raw_page(1000, 100), raw_page(900, 100)])
    messages, _errors = MessageDeleter(client).scan_messages("999")
    ids = [int(m["id"]) for m in messages]
    assert ids == sorted(ids, reverse=True)


def test_scan_records_api_errors_and_keeps_partial_results():
    client = FakeClient(pages=[raw_page(1000, 100)], errors=[DiscordAPIError("boom", 500)])
    messages, errors = MessageDeleter(client).scan_messages("999")
    assert len(messages) == 100
    assert len(errors) == 1
    assert "boom" in errors[0]


def test_scan_progress_callback_receives_matches():
    client = FakeClient(pages=[raw_page(1000, 5)])
    batches = []
    MessageDeleter(client).scan_messages("999", progress_callback=batches.append)
    assert len(batches) == 1
    assert len(batches[0]) == 5


def test_scan_stop_event_cancels_before_requests():
    client = FakeClient(pages=[raw_page(1000, 25)])
    stop = threading.Event()
    stop.set()
    messages, _errors = MessageDeleter(client).scan_messages("999", stop_event=stop)
    assert messages == []
    assert client.history_calls == []


def test_scan_keyboard_interrupt_keeps_partial(monkeypatch):
    client = FakeClient(pages=[raw_page(1000, 100), raw_page(900, 100)])

    def fake_sleep(*_a, **_k):
        raise KeyboardInterrupt

    monkeypatch.setattr(deleter_module, "sleep_with_cancel", fake_sleep)
    messages, errors = MessageDeleter(client).scan_messages("999")
    assert len(messages) == 100
    assert any("interrupted" in e.lower() for e in errors)


def test_scan_settings_override_scan_delay():
    client = FakeClient(pages=[raw_page(1000, 3)])
    deleter = MessageDeleter(client, {"scan_delay_min": 0.4, "scan_delay_max": 0.8})
    lo, hi = deleter._delay_range(
        "scan_delay_min", "scan_delay_max",
        deleter_module.SCAN_DELAY_MIN, deleter_module.SCAN_DELAY_MAX,
    )
    assert lo == 0.4 and hi == 0.8


def test_delete_dry_run_touches_nothing():
    client = FakeClient()
    result = MessageDeleter(client).execute_deletion(
        [{"id": "1", "channel_id": "555", "content": "x", "timestamp": "t"}], dry_run=True
    )
    assert result["dry_run"] is True
    assert client.delete_calls == []


def test_delete_success_counts():
    client = FakeClient(delete_statuses=["deleted", "already_gone", "deleted"])
    msgs = [{"id": str(i), "channel_id": "555", "content": "x", "timestamp": "t"} for i in range(3)]
    result = MessageDeleter(client).execute_deletion(msgs, skip_confirm=True)
    assert result["deleted"] == 3
    assert result["failed"] == 0
    assert result["cancelled"] is False
    assert len(result["deleted_ids"]) == 3


def test_delete_failures_tracked():
    client = FakeClient(delete_statuses=["deleted", "forbidden"])
    msgs = [
        {"id": "1", "channel_id": "555", "content": "x", "timestamp": "t"},
        {"id": "2", "channel_id": "555", "content": "x", "timestamp": "t"},
    ]
    result = MessageDeleter(client).execute_deletion(msgs, skip_confirm=True)
    assert result["deleted"] == 1
    assert result["failed"] == 1
    assert [m["id"] for m in result["failed_messages"]] == ["2"]


def test_delete_aborts_after_consecutive_failures():
    client = FakeClient(delete_statuses=["failed"] * 50)
    msgs = [{"id": str(i), "channel_id": "555", "content": "x", "timestamp": "t"} for i in range(50)]
    result = MessageDeleter(client).execute_deletion(msgs, skip_confirm=True)
    assert result["cancelled"] is True
    assert len(client.delete_calls) == deleter_module.MAX_CONSECUTIVE_FAILURES


def test_delete_respects_failure_limit_setting():
    client = FakeClient(delete_statuses=["failed"] * 50)
    msgs = [{"id": str(i), "channel_id": "555", "content": "x", "timestamp": "t"} for i in range(50)]
    deleter = MessageDeleter(client, {"max_consecutive_failures": 3})
    result = deleter.execute_deletion(msgs, skip_confirm=True)
    assert len(client.delete_calls) == 3
    assert result["cancelled"] is True


def test_delete_stop_event_cancels():
    client = FakeClient()
    stop = threading.Event()
    stop.set()
    result = MessageDeleter(client).execute_deletion(
        [{"id": "1", "channel_id": "555", "content": "x", "timestamp": "t"}],
        skip_confirm=True, stop_event=stop,
    )
    assert result["cancelled"] is True
    assert client.delete_calls == []


def test_delete_progress_callback():
    client = FakeClient(delete_statuses=["deleted", "deleted"])
    updates = []
    msgs = [{"id": str(i), "channel_id": "555", "content": "x", "timestamp": "t"} for i in range(2)]
    MessageDeleter(client).execute_deletion(
        msgs, skip_confirm=True,
        progress_callback=lambda d, f, t: updates.append((d, f, t)),
    )
    assert updates == [(1, 0, 2), (2, 0, 2)]


def test_delete_confirmation_declined():
    client = FakeClient()
    monkey = pytest.MonkeyPatch()
    monkey.setattr("builtins.input", lambda _prompt: "n")
    try:
        result = MessageDeleter(client).execute_deletion(
            [{"id": "1", "channel_id": "555", "content": "x", "timestamp": "t"}]
        )
    finally:
        monkey.undo()
    assert result["cancelled"] is True
    assert client.delete_calls == []


def test_empty_deletion_list():
    client = FakeClient()
    result = MessageDeleter(client).execute_deletion([], skip_confirm=True)
    assert result["deleted"] == 0
    assert client.delete_calls == []
