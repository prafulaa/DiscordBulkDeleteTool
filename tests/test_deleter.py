import threading

import pytest

import deleter as deleter_module
from api_client import DiscordAPIError
from deleter import MessageDeleter


def make_msg(i, author_id="111", hit=True):
    return {
        "id": str(100000 + i),
        "channel_id": "555",
        "content": f"message number {i}",
        "timestamp": "2024-01-01T00:00:00+00:00",
        "attachments": [],
        "author": {"id": author_id},
        "hit": hit,
    }


def make_page(ids, total):
    return {"messages": [[make_msg(i)] for i in ids], "total_results": total}


class FakeClient:
    """Scripted search/delete double for MessageDeleter tests."""

    def __init__(self, pages=None, delete_statuses=None, search_errors=None):
        self.user_id = "111"
        self._pages = list(pages or [])
        self._page_index = 0
        self._search_errors = list(search_errors or [])
        self.delete_statuses = list(delete_statuses or [])
        self.delete_calls = []
        self.search_calls = []

    def search_messages(self, **kwargs):
        self.search_calls.append(kwargs)
        if self._page_index < len(self._pages):
            page = self._pages[self._page_index]
            self._page_index += 1
            return page
        if self._search_errors:
            raise self._search_errors.pop(0)
        return {"messages": [], "total_results": 0}

    def delete_message(self, channel_id, message_id):
        self.delete_calls.append((channel_id, message_id))
        if self.delete_statuses:
            return self.delete_statuses.pop(0)
        return "deleted"


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(deleter_module, "sleep_with_cancel", lambda *a, **k: None)


def test_scan_basic_pagination():
    client = FakeClient(pages=[make_page(range(25), 25)])
    messages, errors = MessageDeleter(client).scan_messages("999", is_dm=False)
    assert errors == []
    assert len(messages) == 25
    assert len(client.search_calls) == 1
    assert client.search_calls[0]["guild_id"] == "999"
    assert client.search_calls[0]["author_id"] == "111"  # defaults to self


def test_scan_dm_uses_channel_endpoint():
    client = FakeClient(pages=[make_page(range(25), 25)])
    MessageDeleter(client).scan_messages("777", is_dm=True)
    assert client.search_calls[0]["channel_id"] == "777"
    assert client.search_calls[0]["guild_id"] is None


def test_scan_multi_page_until_total_reached():
    client = FakeClient(
        pages=[make_page(range(0, 25), 50), make_page(range(25, 50), 50)]
    )
    messages, errors = MessageDeleter(client).scan_messages("999", is_dm=False)
    assert len(messages) == 50
    assert errors == []
    assert client.search_calls[1]["offset"] == deleter_module.SEARCH_PAGE_SIZE


def test_scan_dedupes_overlapping_pages():
    client = FakeClient(
        pages=[make_page(range(0, 25), 40), make_page(range(15, 40), 40)]
    )
    messages, _errors = MessageDeleter(client).scan_messages("999", is_dm=False)
    ids = {m["id"] for m in messages}
    assert len(ids) == 40
    assert len(messages) == 40


def test_scan_window_jump_on_offset_cap(monkeypatch):
    monkeypatch.setattr(deleter_module, "SEARCH_OFFSET_CAP", 50)
    client = FakeClient(
        pages=[
            make_page(range(0, 25), 60),      # offset 0
            make_page(range(25, 50), 60),     # offset 25
            # after page 2: offset 50 >= min(60, 50) and total > cap → window jump
            make_page(range(60, 70), 10),     # fresh, older window at offset 0
        ]
    )
    messages, errors = MessageDeleter(client).scan_messages("999", is_dm=False)
    assert errors == []
    assert len(messages) == 60  # 50 from first window + 10 from second
    third_call = client.search_calls[2]
    assert third_call["offset"] == 0
    assert int(third_call["max_id"]) == 100024  # strictly before the oldest seen


def test_scan_filters_context_and_foreign_messages():
    page = {
        "total_results": 2,
        "messages": [
            [make_msg(1), make_msg(2, hit=False), make_msg(3, author_id="999")],
            [make_msg(4)],
        ],
    }
    client = FakeClient(pages=[page])
    messages, _errors = MessageDeleter(client).scan_messages("999", is_dm=False)
    assert sorted(m["id"] for m in messages) == ["100001", "100004"]


def test_scan_records_api_errors_and_keeps_partial_results():
    client = FakeClient(
        pages=[make_page(range(25), 50)],
        search_errors=[DiscordAPIError("boom", 500)],
    )
    messages, errors = MessageDeleter(client).scan_messages("999", is_dm=False)
    assert len(messages) == 25
    assert len(errors) == 1
    assert "boom" in errors[0]


def test_scan_date_filters_passed_through():
    client = FakeClient(pages=[make_page(range(25), 25)])
    MessageDeleter(client).scan_messages(
        "999", is_dm=False, min_id="111", max_id="222", content_query="kw"
    )
    call = client.search_calls[0]
    assert call["min_id"] == "111"
    assert call["max_id"] == "222"
    assert call["content"] == "kw"


def test_scan_newest_first_sorting():
    client = FakeClient(pages=[make_page(range(5), 5)])
    messages, _errors = MessageDeleter(client).scan_messages("999", is_dm=False)
    ids = [int(m["id"]) for m in messages]
    assert ids == sorted(ids, reverse=True)


def test_scan_progress_callback_receives_new_batches():
    client = FakeClient(pages=[make_page(range(0, 25), 50), make_page(range(10, 35), 50)])
    batches = []
    MessageDeleter(client).scan_messages("999", is_dm=False, progress_callback=batches.append)
    assert len(batches) == 2
    assert len(batches[0]) == 25
    assert len(batches[1]) == 10  # duplicates from page 1 removed


def test_scan_stop_event_cancels_before_requests():
    client = FakeClient(pages=[make_page(range(25), 25)])
    stop = threading.Event()
    stop.set()
    messages, _errors = MessageDeleter(client).scan_messages("999", is_dm=False, stop_event=stop)
    assert messages == []
    assert client.search_calls == []


def test_scan_keyboard_interrupt_keeps_partial(monkeypatch):
    client = FakeClient(pages=[make_page(range(25), 50)])
    calls = {"n": 0}

    def fake_sleep(*_a, **_k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise KeyboardInterrupt

    monkeypatch.setattr(deleter_module, "sleep_with_cancel", fake_sleep)
    messages, errors = MessageDeleter(client).scan_messages("999", is_dm=False)
    assert len(messages) == 25
    assert any("interrupted" in e.lower() for e in errors)


def test_delete_dry_run_touches_nothing():
    client = FakeClient()
    result = MessageDeleter(client).execute_deletion(
        [make_msg(1), make_msg(2)], dry_run=True
    )
    assert result["dry_run"] is True
    assert client.delete_calls == []


def test_delete_success_counts():
    client = FakeClient(delete_statuses=["deleted", "already_gone", "deleted"])
    result = MessageDeleter(client).execute_deletion(
        [make_msg(i) for i in range(3)], skip_confirm=True
    )
    assert result["deleted"] == 3
    assert result["failed"] == 0
    assert result["cancelled"] is False
    assert len(result["deleted_ids"]) == 3


def test_delete_failures_tracked():
    client = FakeClient(delete_statuses=["deleted", "forbidden"])
    result = MessageDeleter(client).execute_deletion(
        [make_msg(i) for i in range(2)], skip_confirm=True
    )
    assert result["deleted"] == 1
    assert result["failed"] == 1
    assert [m["id"] for m in result["failed_messages"]] == ["100001"]


def test_delete_aborts_after_consecutive_failures():
    client = FakeClient(delete_statuses=["failed"] * 50)
    result = MessageDeleter(client).execute_deletion(
        [make_msg(i) for i in range(50)], skip_confirm=True
    )
    assert result["cancelled"] is True
    assert len(client.delete_calls) == deleter_module.MAX_CONSECUTIVE_FAILURES


def test_delete_stop_event_cancels():
    client = FakeClient()
    stop = threading.Event()
    stop.set()
    result = MessageDeleter(client).execute_deletion(
        [make_msg(i) for i in range(5)], skip_confirm=True, stop_event=stop
    )
    assert result["cancelled"] is True
    assert client.delete_calls == []


def test_delete_progress_callback():
    client = FakeClient(delete_statuses=["deleted", "deleted"])
    updates = []
    MessageDeleter(client).execute_deletion(
        [make_msg(i) for i in range(2)],
        skip_confirm=True,
        progress_callback=lambda d, f, t: updates.append((d, f, t)),
    )
    assert updates == [(1, 0, 2), (2, 0, 2)]


def test_delete_confirmation_declined():
    client = FakeClient()
    monkey = pytest.MonkeyPatch()
    monkey.setattr("builtins.input", lambda _prompt: "n")
    try:
        result = MessageDeleter(client).execute_deletion([make_msg(1)])
    finally:
        monkey.undo()
    assert result["cancelled"] is True
    assert client.delete_calls == []


def test_empty_deletion_list():
    client = FakeClient()
    result = MessageDeleter(client).execute_deletion([], skip_confirm=True)
    assert result["deleted"] == 0
    assert client.delete_calls == []
