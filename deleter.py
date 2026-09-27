"""Scan via Discord's search API and bulk delete the found messages."""

import random

from tqdm import tqdm

from api_client import (
    SEARCH_OFFSET_CAP,
    SEARCH_PAGE_SIZE,
    AuthenticationError,
    DiscordAPIError,
)
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
SEARCH_DELAY_MIN = 1.0
SEARCH_DELAY_MAX = 2.0
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
    def _normalize(raw_groups, target_author_id):
        """Flatten search result groups into minimal message dicts, keeping
        only actual hits (not context padding) authored by the target."""
        normalized = []
        for group in raw_groups or []:
            messages = group if isinstance(group, list) else [group]
            for msg in messages:
                if msg.get("hit") is False:
                    continue  # context message around a real hit
                if msg.get("author", {}).get("id") != target_author_id:
                    continue
                normalized.append(
                    {
                        "id": msg["id"],
                        "channel_id": msg["channel_id"],
                        "content": msg.get("content", ""),
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
        """Collect messages authored by `author_id` (defaults to the logged-in
        user) in a guild or DM channel. Discord caps search offsets at 5000,
        so for larger result sets the scan re-windows with max_id and keeps
        going until exhausted.

        Returns (messages, errors): messages sorted newest-first, errors is a
        list of strings describing any search failures mid-scan.
        """
        target_author = author_id or self.client.user_id
        guild_id = None if is_dm else context_id
        channel_id = context_id if is_dm else None

        seen_ids = set()
        all_messages = []
        errors = []
        offset = 0
        window_max_id = max_id
        interrupted = False

        print_info("Scanning via Discord search…")

        try:
            while True:
                if stop_event is not None and stop_event.is_set():
                    print_warning("Scan cancelled by user.")
                    break

                try:
                    data = self.client.search_messages(
                        guild_id=guild_id,
                        channel_id=channel_id,
                        author_id=target_author,
                        content=content_query,
                        min_id=min_id,
                        max_id=window_max_id,
                        offset=offset,
                    )
                except AuthenticationError:
                    raise
                except DiscordAPIError as exc:
                    print_error(f"Search failed: {exc}")
                    errors.append(str(exc))
                    break

                if not data:
                    break

                page = self._normalize(data.get("messages"), target_author)
                total = int(data.get("total_results", 0))

                new_messages = [msg for msg in page if msg["id"] not in seen_ids]
                for msg in new_messages:
                    seen_ids.add(msg["id"])
                all_messages.extend(new_messages)
                if progress_callback is not None and new_messages:
                    progress_callback(new_messages)

                print_info(f"  +{len(new_messages)} (total {len(all_messages)} of {total} matches)")

                if not page:
                    break  # end of current window

                offset += SEARCH_PAGE_SIZE
                if offset >= min(total, SEARCH_OFFSET_CAP):
                    if total > SEARCH_OFFSET_CAP:
                        # Offset cap reached but more results exist — jump to
                        # the oldest message found so far and start over.
                        oldest = min(int(msg["id"]) for msg in page)
                        window_max_id = str(oldest - 1)
                        offset = 0
                        print_info("Reached the 5000-result search cap — continuing with older messages…")
                    else:
                        break

                search_lo, search_hi = self._delay_range(
                    "search_delay_min", "search_delay_max", SEARCH_DELAY_MIN, SEARCH_DELAY_MAX
                )
                sleep_with_cancel(random.uniform(search_lo, search_hi), stop_event)
        except KeyboardInterrupt:
            print_warning("Scan interrupted — keeping partial results.")
            interrupted = True
            errors.append("Scan interrupted by user (partial results).")

        all_messages.sort(key=lambda msg: int(msg["id"]), reverse=True)
        suffix = " (partial)" if interrupted or errors else ""
        print_success(f"Scan finished{suffix}: {len(all_messages)} unique messages found.")
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
