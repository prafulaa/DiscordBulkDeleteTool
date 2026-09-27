"""Persistent user settings (settings.json next to the scripts).

Settings are safety-relevant pacing options, deliberately NOT secrets:
the Discord token is never written here. Values are clamped to safe ranges
on load — an edited/corrupt file can't speed up deletions beyond limits.
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger("DiscordTool")

SETTINGS_FILE = Path(__file__).resolve().parent / "settings.json"

# (key, default, minimum, maximum)
FLOAT_SETTINGS = {
    "delete_delay_min": (1.2, 0.5, 10.0),
    "delete_delay_max": (2.0, 0.5, 10.0),
    "scan_delay_min": (0.35, 0.2, 5.0),
    "scan_delay_max": (0.7, 0.2, 5.0),
}
INT_SETTINGS = {
    "max_consecutive_failures": (15, 1, 100),
}
BOOL_SETTINGS = {
    "confirm_before_delete": True,
}

DEFAULTS = {
    **{key: default for key, (default, _lo, _hi) in FLOAT_SETTINGS.items()},
    **{key: default for key, (default, _lo, _hi) in INT_SETTINGS.items()},
    **BOOL_SETTINGS,
}


def _clamp(value, lo, hi):
    return max(lo, min(hi, value))


def load_settings():
    """Load settings.json, falling back to safe defaults for anything
    missing or invalid."""
    settings = dict(DEFAULTS)
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as handle:
            stored = json.load(handle)
    except (OSError, ValueError):
        return settings

    if not isinstance(stored, dict):
        return settings

    for key, (default, lo, hi) in FLOAT_SETTINGS.items():
        try:
            settings[key] = _clamp(float(stored.get(key, default)), lo, hi)
        except (TypeError, ValueError):
            settings[key] = default

    for key, (default, lo, hi) in INT_SETTINGS.items():
        try:
            settings[key] = int(_clamp(float(stored.get(key, default)), lo, hi))
        except (TypeError, ValueError):
            settings[key] = default

    for key, default in BOOL_SETTINGS.items():
        value = stored.get(key, default)
        settings[key] = bool(value) if isinstance(value, bool) else default

    # Keep the pacing window sane: min must not exceed max.
    for lo_key, hi_key in (
        ("delete_delay_min", "delete_delay_max"),
        ("scan_delay_min", "scan_delay_max"),
    ):
        if settings[lo_key] > settings[hi_key]:
            settings[lo_key], settings[hi_key] = settings[hi_key], settings[lo_key]

    return settings


def save_settings(settings):
    """Validate, clamp, persist and return the effective settings."""
    clean = dict(DEFAULTS)

    for key, (_default, lo, hi) in FLOAT_SETTINGS.items():
        try:
            clean[key] = _clamp(float(settings.get(key, DEFAULTS[key])), lo, hi)
        except (TypeError, ValueError):
            clean[key] = DEFAULTS[key]

    for key, (_default, lo, hi) in INT_SETTINGS.items():
        try:
            clean[key] = int(_clamp(float(settings.get(key, DEFAULTS[key])), lo, hi))
        except (TypeError, ValueError):
            clean[key] = DEFAULTS[key]

    for key, default in BOOL_SETTINGS.items():
        value = settings.get(key, default)
        clean[key] = bool(value) if isinstance(value, bool) else default

    for lo_key, hi_key in (
        ("delete_delay_min", "delete_delay_max"),
        ("scan_delay_min", "scan_delay_max"),
    ):
        if clean[lo_key] > clean[hi_key]:
            clean[lo_key], clean[hi_key] = clean[hi_key], clean[lo_key]

    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as handle:
            json.dump(clean, handle, indent=2)
    except OSError as exc:
        logger.error("Could not write settings.json: %s", exc)
    return clean
