"""Best-effort lookup of the token of the *locally installed Discord desktop
client* — for the "Auto-Find" convenience button.

Deliberately limited: it only scans the Discord client's own LevelDB storage
for plaintext tokens. It does NOT touch browsers, and it does NOT decrypt
DPAPI/AES-protected data — that is credential-stealer behavior and has no
place in a legitimate tool. If nothing is found (newer Discord builds store
the token encrypted), just paste your token manually.
"""

import os
import re

TOKEN_PATTERNS = [
    re.compile(r"mfa\.[\w-]{84}"),
    re.compile(r"[\w-]{24,26}\.[\w-]{6}\.[\w-]{25,110}"),
]

READABLE_SUFFIXES = (".ldb", ".log")
SKIP_FILES = ("LOCK", "LOG", "CURRENT", "MANIFEST-")


def get_discord_paths():
    """LevelDB storage paths of installed Discord client variants."""
    appdata = os.environ.get("APPDATA", "")
    variants = {
        "Discord": os.path.join(appdata, "Discord"),
        "Discord Canary": os.path.join(appdata, "discordcanary"),
        "Discord PTB": os.path.join(appdata, "discordptb"),
    }
    paths = {}
    for name, base in variants.items():
        leveldb = os.path.join(base, "Local Storage", "leveldb")
        if os.path.isdir(leveldb):
            paths[name] = leveldb
    return paths


def extract_tokens_from_path(leveldb_path):
    """Scan LevelDB files for plaintext tokens."""
    found = []
    try:
        names = os.listdir(leveldb_path)
    except OSError:
        return found

    for filename in names:
        if not filename.endswith(READABLE_SUFFIXES):
            continue
        if any(filename.startswith(prefix) for prefix in SKIP_FILES):
            continue
        try:
            with open(os.path.join(leveldb_path, filename), "rb") as handle:
                content = handle.read().decode("utf-8", errors="ignore")
        except OSError:
            continue
        for pattern in TOKEN_PATTERNS:
            for match in pattern.finditer(content):
                token = match.group(0)
                if token not in found and validate_token_format(token):
                    found.append(token)
    return found


def find_tokens():
    """Search every installed Discord client. Returns [(token, source), …]."""
    results = []
    seen = set()
    for source_name, leveldb_path in get_discord_paths().items():
        for token in extract_tokens_from_path(leveldb_path):
            if token not in seen:
                seen.add(token)
                results.append((token, source_name))
    return results


def validate_token_format(token):
    """Three dot-separated parts, or an mfa. token."""
    if not token or len(token) < 50:
        return False
    if token.startswith("mfa."):
        return True
    parts = token.split(".")
    return len(parts) >= 3 and all(parts)
