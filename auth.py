"""Discord token input: DISCORD_TOKEN env var → token.txt → secure prompt.

The token never gets logged in full — only masked previews.
"""

import getpass
import os

from utils import logger, mask_token, print_error, print_info, print_warning

TOKEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "token.txt")
MIN_TOKEN_LENGTH = 50


def looks_like_token(token):
    """Basic format check to catch obvious paste errors early."""
    if not token or len(token) < MIN_TOKEN_LENGTH:
        return False
    return token.startswith("mfa.") or token.count(".") >= 2


def _read_token_file():
    try:
        with open(TOKEN_FILE, encoding="utf-8") as handle:
            token = handle.read().strip()
    except OSError as exc:
        logger.error("Failed to read token.txt: %s", exc)
        return None
    if not token:
        return None
    if not looks_like_token(token):
        print_warning("token.txt exists but its contents don't look like a Discord token — ignoring it.")
        return None
    return token


def get_user_token():
    """Return a user token, or None if the user gave up."""
    print_info("Please enter your Discord User Token.")
    print_info(
        "To find it: Discord → Ctrl+Shift+I → Network tab → filter 'api' → "
        "refresh → click any request → Request Headers → 'authorization'."
    )

    # Priority 1: environment variable (explicit, per-session)
    env_token = (os.environ.get("DISCORD_TOKEN") or "").strip()
    if env_token:
        print_info("Using token from DISCORD_TOKEN environment variable.")
        if not looks_like_token(env_token):
            print_warning("DISCORD_TOKEN doesn't look like a Discord token — trying it anyway.")
        logger.info("Token source: env var (%s)", mask_token(env_token))
        return env_token

    # Priority 2: local token.txt (gitignored — never commit it)
    if os.path.exists(TOKEN_FILE):
        token = _read_token_file()
        if token:
            print_info("Loaded token from token.txt.")
            logger.info("Token source: token.txt (%s)", mask_token(token))
            return token

    # Priority 3: secure prompt (up to 3 tries)
    for _ in range(3):
        token = getpass.getpass("Token: ").strip()
        if not token:
            print_error("Token cannot be empty.")
            continue
        if not looks_like_token(token):
            print_error("That doesn't look like a Discord token. Please check and try again.")
            continue
        return token

    print_error("No valid token provided.")
    return None
