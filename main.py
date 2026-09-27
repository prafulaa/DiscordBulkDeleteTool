"""Command-line interface for the Discord Bulk Delete Tool."""

import threading

from api_client import AuthenticationError, DiscordAPIError, DiscordClient, NetworkError
from auth import get_user_token
from deleter import MessageDeleter
from utils import (
    VERSION,
    date_to_snowflake,
    display_username,
    logger,
    parse_date,
    print_error,
    print_info,
    print_success,
    print_warning,
    validate_snowflake,
)


def _ask_date(prompt):
    """Ask for an optional date; returns a snowflake or None."""
    while True:
        raw = input(prompt).strip()
        if not raw:
            return None
        if parse_date(raw) is None:
            print_error("Unrecognized date. Use YYYY-MM-DD (e.g. 2023-06-30).")
            continue
        return date_to_snowflake(raw)


def _ask_context_id():
    while True:
        context_id = input("Enter the ID (Channel ID for DM, Server ID for Server): ").strip()
        if validate_snowflake(context_id):
            return context_id
        print_error("Invalid ID. Discord IDs are long numeric snowflakes "
                    "(enable Developer Mode in Discord, then right-click → Copy ID).")


def _print_preview(messages):
    print_info(f"Found {len(messages)} messages. Newest first, preview:")
    for msg in messages[:5]:
        preview = (msg["content"] or "[attachment]").replace("\n", " ")[:70]
        print(f"  • {msg['timestamp'][:10]}  {preview}")
    if len(messages) > 5:
        print(f"  … and {len(messages) - 5} more")


def _run_deletion(deleter, messages):
    """Interactive scan→preview→(dry-run)→delete flow for one context."""
    action = input("Action — [d]ry-run preview, [y] delete now, [n] cancel: ").strip().lower()
    if action == "d":
        deleter.execute_deletion(messages, dry_run=True)
        if input(f"Proceed to really delete all {len(messages)} messages? (y/N): ").strip().lower() != "y":
            print_warning("Deletion cancelled.")
            return
    elif action == "y":
        pass
    else:
        print_warning("Deletion cancelled.")
        return

    stop_event = threading.Event()
    result = deleter.execute_deletion(messages, skip_confirm=False, stop_event=stop_event)
    if result["failed"]:
        print_warning(f"{result['failed']} messages failed to delete. Re-run the scan to retry them.")
    if result["cancelled"]:
        print_warning("The run stopped early — remaining messages were not touched.")


def main():
    print_info(f"=== Discord Bulk Message Tool v{VERSION} ===")
    print_warning("SAFETY NOTICE: Automating user accounts violates Discord's Terms of Service.")
    print_warning("Delays are built in for safety — use this tool at your own risk.")
    print("")

    token = get_user_token()
    if not token:
        return

    client = DiscordClient(token)
    try:
        user_info = client.validate_token()
    except AuthenticationError:
        print_error("Invalid token. Login failed.")
        return
    except (NetworkError, DiscordAPIError) as exc:
        print_error(f"Login failed: {exc}")
        return

    print_success(f"Logged in as {display_username(user_info)}")
    deleter = MessageDeleter(client)

    while True:
        print("\n--- Menu ---")
        print("1. Delete messages from a DM (Direct Message)")
        print("2. Delete messages from a specific Server (Guild)")
        print("3. Exit")

        choice = input("Select an option (1-3): ").strip()
        if choice == "3":
            print("Exiting.")
            break
        if choice not in ("1", "2"):
            print_error("Please pick 1, 2 or 3.")
            continue

        context_id = _ask_context_id()
        content_query = input("Optional: filter by keyword (Enter to skip): ").strip() or None
        min_id = _ask_date("Delete messages sent AFTER a date? (YYYY-MM-DD, Enter to skip): ")
        max_id = _ask_date("Delete messages sent BEFORE a date? (YYYY-MM-DD, Enter to skip): ")

        try:
            messages, errors = deleter.scan_messages(
                context_id=context_id,
                is_dm=(choice == "1"),
                content_query=content_query,
                min_id=min_id,
                max_id=max_id,
            )
            for error in errors:
                print_warning(f"Search reported: {error}")
            if not messages:
                print_info("No messages found matching the criteria.")
                continue
            _print_preview(messages)
            _run_deletion(deleter, messages)
        except AuthenticationError:
            print_error("Your token stopped working (invalidated or expired). Restart and re-login.")
            break
        except Exception as exc:  # keep the menu alive on unexpected errors
            print_error(f"An unexpected error occurred: {exc}")
            logger.error("Main loop error: %s", exc, exc_info=True)


def run_cli():
    try:
        main()
    except KeyboardInterrupt:
        print("\nExiting.")


if __name__ == "__main__":
    run_cli()
