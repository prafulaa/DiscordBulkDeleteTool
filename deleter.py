"""Scan a channel's history and bulk delete the found messages."""

import random

from tqdm import tqdm

from api_client import AuthenticationError, DiscordAPIError
from utils import (
    logger,
    print_error,
    print_info,
    print_success,
    print_warning,
    sleep_with_cancel,
)

DELETE_DELAY_MIN = 1.2
DELETE_DELAY_MAX = 2.0
SCAN_DELAY_MIN = 0.35
SCAN_DELAY_MAX = 0.7
MAX_CONSECUTIVE_FAILURES = 15


class MessageDeleter:
    def __init__(self, client, settings=None):
        self.client = client
        # Optional overrides (from settings.json); None -> module constants.
        self.settings = dict(settings) if settings else {}

    def _setting(self, key, default):
        try:
            return float(self.settings.get(key, default))
        except (TypeError, ValueError):
            return float(default)

    def _delay_range(self, min_key, max_key, default_min, default_max):
        lo = max(self._setting(min_key, default_min), 0.1)
        hi = max(self._setting(max_key, default_max), lo)
        return lo, hi

    # --- Scanning -----------------------------------------------------------

    @staticmethod
    def _normalize_history(page, channel_id, target_author_id, content_query, min_id, max_id):
        """Filter a raw history page down to the target author's messages
        inside the id window, optionally matching a keyword."""
        needle = content_query.lower() if content_query else None
        min_int = int(min_id) if min_id else None
        max_int = int(max_id) if max_id else None
        normalized = []
        for msg in page or []:
            msg_id = int(msg["id"])
            if min_int is not None and msg_id < min_int:
                continue  # older than the lower bound
            if max_int is not None and msg_id >= max_int:
                continue  # newer than the upper bound
            if msg.get("author", {}).get("id") != target_author_id:
                continue
            content = msg.get("content", "")
            if needle and needle not in content.lower():
                continue
            normalized.append(
                {
                    "id": msg["id"],
                    "channel_id": channel_id,
                    "content": content,
                    "timestamp": msg.get("timestamp", ""),
                    "attachments": bool(msg.get("attachments")),
                }
            )
        return normalized

    def scan_messages(
        self,
        context_id,
        is_dm=False,
        content_query=None,
        min_id=None,
        max_id=None,
        author_id=None,
        progress_callback=None,
        stop_event=None,
    ):
        """Collect the target author's messages in one channel by walking its
        history (100 per request — several times faster than the search
        endpoint, and uncapped). ``is_dm`` is accepted for compatibility but
        unused: the history endpoint is identical for DMs and guild channels.

        Pagination walks newest -> oldest, so scanning stops early once the
        messages fall below ``min_id`` (the time-range lower bound).

        Returns (messages, errors): messages sorted newest-first, errors is a
        list of strings describing any scan failures mid-scan.
        """
        target_author = str(author_id or self.client.user_id)
        channel_id = context_id

        all_messages = []
        errors = []
        interrupted = False
        cursor = None
        delay_lo, delay_hi = self._delay_range(
            "scan_delay_min", "scan_delay_max", SCAN_DELAY_MIN, SCAN_DELAY_MAX
        )
        pace = 1.0  # adaptive multiplier, grows on rate limits

        print_info("Scanning channel history…")

        try:
            while True:
                if stop_event is not None and stop_event.is_set():
                    print_warning("Scan cancelled by user.")
                    break

                try:
                    page = self.client.fetch_history(channel_id, before=cursor)
                except AuthenticationError:
                    raise
                except DiscordAPIError as exc:
                    print_error(f"History fetch failed: {exc}")
                    errors.append(str(exc))
                    break

                if not page:
                    break  # reached the beginning of the channel

                matches = self._normalize_history(
                    page, str(channel_id), target_author, content_query, min_id, max_id
                )
                all_messages.extend(matches)
                if progress_callback is not None and matches:
                    progress_callback(matches)

                # History is newest-first: once messages fall below the lower
                # time bound, everything further back is out of range too.
                oldest_id = int(page[-1]["id"])
                if min_id and oldest_id < int(min_id):
                    break
                if len(page) < 100:
                    break  # end of channel history

                cursor = page[-1]["id"]
                sleep_with_cancel(random.uniform(delay_lo, delay_hi) * pace, stop_event)

        except KeyboardInterrupt:
            print_warning("Scan interrupted — keeping partial results.")
            interrupted = True
            errors.append("Scan interrupted by user (partial results).")

        all_messages.sort(key=lambda msg: int(msg["id"]), reverse=True)
        suffix = " (partial)" if interrupted or errors else ""
        print_success(f"Scan finished{suffix}: {len(all_messages)} messages found.")
        return all_messages, errors

    # --- Deletion ------------------------------------------------------------

    def execute_deletion(
        self,
        messages,
        dry_run=False,
        progress_callback=None,
        skip_confirm=False,
        stop_event=None,
        show_progress=True,
    ):
        """Delete the given messages one by one with safety delays.

        Returns a result dict:
          {deleted, failed, failed_messages, cancelled, dry_run}
        `failed_messages` are kept so the caller can offer a retry.
        """
        empty = {
            "deleted": 0, "failed": 0, "failed_messages": [],
            "deleted_ids": [], "cancelled": False, "dry_run": dry_run,
        }
        if not messages:
            print_warning("No messages to delete.")
            return empty

        print_info(f"Preparing to delete {len(messages)} messages…")

        if dry_run:
            print_info("DRY RUN — nothing will be deleted. Preview:")
            for msg in messages[:10]:
                preview = (msg["content"] or "[attachment]").replace("\n", " ")[:80]
                print(f"  • {msg['timestamp'][:10]}  {preview}")
            if len(messages) > 10:
                print(f"  … and {len(messages) - 10} more")
            return empty

        if not skip_confirm:
            confirm = input(f"Type Y to PERMANENTLY delete {len(messages)} messages: ")
            if confirm.strip().lower() != "y":
                print_warning("Deletion cancelled.")
                empty["cancelled"] = True
                return empty

        deleted = 0
        failed = 0
        consecutive_failures = 0
        failed_messages = []
        deleted_ids = []
        cancelled = False
        failure_limit = max(int(self._setting("max_consecutive_failures", MAX_CONSECUTIVE_FAILURES)), 1)
        delay_lo, delay_hi = self._delay_range(
            "delete_delay_min", "delete_delay_max", DELETE_DELAY_MIN, DELETE_DELAY_MAX
        )

        iterator = tqdm(messages, desc="Deleting", unit="msg", ncols=80) if show_progress else messages

        try:
            for msg in iterator:
                if stop_event is not None and stop_event.is_set():
                    print_warning("Deletion cancelled by user.")
                    cancelled = True
                    break

                try:
                    status = self.client.delete_message(msg["channel_id"], msg["id"])
                except AuthenticationError:
                    print_error("Token was invalidated mid-run — stopping immediately.")
                    failed_messages.append(msg)
                    cancelled = True
                    break

                if status in ("deleted", "already_gone"):
                    deleted += 1
                    consecutive_failures = 0
                    deleted_ids.append(msg["id"])
                    logger.info("Deleted %s in channel %s", msg["id"], msg["channel_id"])
                else:
                    failed += 1
                    consecutive_failures += 1
                    failed_messages.append(msg)
                    logger.warning("Failed to delete %s (status=%s)", msg["id"], status)
                    if consecutive_failures >= failure_limit:
                        print_error(
                            f"{failure_limit} deletions failed in a row — "
                            "aborting to stay safe. Remaining messages were NOT touched."
                        )
                        cancelled = True
                        break

                if progress_callback is not None:
                    progress_callback(deleted, failed, len(messages))

                sleep_with_cancel(random.uniform(delay_lo, delay_hi), stop_event)
        except KeyboardInterrupt:
            print_warning("\nCtrl+C received — stopping after this message.")
            cancelled = True

        print_success(f"Deletion finished. Deleted: {deleted}, failed: {failed}.")
        return {
            "deleted": deleted,
            "failed": failed,
            "failed_messages": failed_messages,
            "deleted_ids": deleted_ids,
            "cancelled": cancelled,
            "dry_run": False,
        }
