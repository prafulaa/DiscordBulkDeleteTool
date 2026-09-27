"""Shared helpers: logging, console output, snowflake/date utilities.

Discord snowflakes encode a millisecond timestamp in the top 42 bits:
    snowflake = (unix_ms - DISCORD_EPOCH_MS) << 22
"""

import logging
import re
import time
from datetime import datetime, timedelta, timezone

from colorama import Fore, Style, init

VERSION = "3.0.0"

DISCORD_EPOCH_MS = 1420070400000  # 2015-01-01T00:00:00Z
SNOWFLAKE_RE = re.compile(r"^\d{15,21}$")

init()

logger = logging.getLogger("DiscordTool")


def setup_logging(log_file="discord_tool.log"):
    """Log everything to a file. Console output is handled by print_* helpers
    (a console handler here would double-print warnings/errors)."""
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        try:
            handler = logging.FileHandler(log_file, encoding="utf-8")
            handler.setFormatter(
                logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
            )
            logger.addHandler(handler)
        except OSError as exc:
            print(f"{Fore.RED}Error setting up log file: {exc}{Style.RESET_ALL}")
    return logger


logger = setup_logging()


def print_info(message):
    print(f"{Fore.CYAN}[INFO]{Style.RESET_ALL} {message}")
    logger.info(message)


def print_success(message):
    print(f"{Fore.GREEN}[SUCCESS]{Style.RESET_ALL} {message}")
    logger.info(message)


def print_warning(message):
    print(f"{Fore.YELLOW}[WARNING]{Style.RESET_ALL} {message}")
    logger.warning(message)


def print_error(message):
    print(f"{Fore.RED}[ERROR]{Style.RESET_ALL} {message}")
    logger.error(message)


# --- Dates & snowflakes -------------------------------------------------


def parse_date(date_str):
    """Parse a date string into an aware UTC datetime. Returns None if invalid."""
    if not date_str or not str(date_str).strip():
        return None
    try:
        from dateutil import parser

        parsed = parser.parse(str(date_str).strip())
    except (ValueError, OverflowError, ImportError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def date_to_snowflake(date_str, end_of_day=False):
    """Convert a date string (YYYY-MM-DD preferred) to a Discord snowflake
    boundary string, or None if the date can't be parsed."""
    parsed = parse_date(date_str)
    if parsed is None:
        return None
    if end_of_day:
        parsed = parsed + timedelta(days=1) - timedelta(milliseconds=1)
    unix_ms = int(parsed.timestamp() * 1000)
    return str((unix_ms - DISCORD_EPOCH_MS) << 22)


def snowflake_to_datetime(snowflake):
    """Convert a Discord snowflake to an aware UTC datetime, or None."""
    try:
        value = int(snowflake)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(((value >> 22) + DISCORD_EPOCH_MS) / 1000, tz=timezone.utc)


def validate_snowflake(value):
    """Discord IDs are 15-21 digit numeric strings."""
    return bool(value) and bool(SNOWFLAKE_RE.fullmatch(str(value).strip()))


def format_discord_timestamp(iso_string):
    """Turn a Discord ISO-8601 timestamp into a friendly local string."""
    if not iso_string:
        return "unknown date"
    parsed = parse_date(iso_string)
    if parsed is None:
        return str(iso_string)
    return parsed.astimezone().strftime("%b %d, %Y at %I:%M %p")


# --- Misc ---------------------------------------------------------------


def display_username(user):
    """Render a user dict for display. Discord has retired discriminators for
    most accounts (they come back as '0'), so only show it when meaningful."""
    if not user:
        return "Unknown User"
    username = user.get("global_name") or user.get("username") or "?"
    discriminator = str(user.get("discriminator") or "0")
    if discriminator in ("0", "0000"):
        return username
    return f"{user.get('username', '?')}#{discriminator}"


def mask_token(token):
    """Never log or display a full token."""
    if not token:
        return "(empty)"
    if len(token) <= 12:
        return token[:4] + "..."
    return f"{token[:8]}...{token[-4:]} ({len(token)} chars)"


def sleep_with_cancel(seconds, cancel_event=None):
    """Sleep in small steps, returning early once cancel_event is set."""
    if cancel_event is None:
        time.sleep(seconds)
        return
    deadline = time.monotonic() + seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or cancel_event.is_set():
            return
        time.sleep(min(0.1, remaining))
