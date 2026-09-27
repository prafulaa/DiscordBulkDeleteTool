# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project adheres to [Semantic Versioning](https://semver.org/).

## [3.1.0] - 2026-09-27

### Added
- Complete Discord-themed UI redesign inspired by the
  [ClearVision](https://github.com/ClearVision/ClearVision-v6) theme
  (Apache-2.0): icon rail, channel-style sidebar with sectioned controls,
  selectable DM/Guild rows with blurple pill highlight, chat-style message
  cards with avatars, and a bottom action bar.
- Procedurally generated aurora wallpaper and initial-letter avatars
  (`theme.py`, pure Pillow) — no third-party image assets are bundled.
- Optional custom wallpaper: drop an image at `assets/background.png`.
- Click anywhere on a message card to toggle its selection.
- New About dialog (profile-card style) with theme credits and GitHub link.
- High-DPI awareness on Windows for crisp rendering on scaled displays.
- `scripts/screenshot.py` — regenerates the README screenshot with demo data.
- 8 new tests for the theme module (wallpaper determinism/darkness, avatars,
  custom background loading) — 76 total.

### Fixed
- Sidebar layout: sections now render reliably (grid column sizing no longer
  pushes labels out of view).
- Login/auto-find button rows and the user panel render on every launch.

## [3.0.0] - 2026-09-27

### Added
- Date-range filtering (*after* / *before*) in both the GUI and the CLI,
  backed by Discord snowflake conversion.
- Dry-run mode in the CLI: preview messages before a real deletion.
- STOP button in the GUI (Ctrl+C in the CLI) that cancels a running scan or
  deletion mid-way without losing progress.
- Failed deletions stay visible and selected in the GUI so they can be
  retried in one click.
- Scan results are deduplicated and sorted newest-first.
- Automatic windowed pagination past Discord's 5,000-result search cap.
- pytest suite (68 tests) covering the API client, scan/delete engine, auth,
  token finder, and date/snowflake utilities.
- CI workflow (ruff + pytest on Linux/Windows, Python 3.10 & 3.12) replacing
  the broken Conda workflow that referenced a non-existent `environment.yml`.
- Release workflow that builds Windows executables on version tags.
- `pyproject.toml` with project metadata, ruff config, and console entry
  points (`discord-tool`, `discord-tool-gui`).
- MIT LICENSE, CHANGELOG, CONTRIBUTING and SECURITY policy.

### Fixed
- Requests could hang forever — every HTTP call now has a 30-second timeout.
- `python-dateutil` was used but missing from `requirements.txt`, so date
  parsing silently failed; dependencies are now complete and version-floored.
- Warnings and errors were printed twice to the console (logger console
  handler duplicated the `print_*` output).
- The search/scan loop treated API errors as "no results", silently showing
  zero messages after a rate limit or failure — errors are now surfaced.
- GUI timestamps failed to parse ISO strings without fractional seconds and
  showed raw output; parsing is now robust (`Z` and offsets).
- The GUI delete button enabled itself with nothing selected.
- After a deletion the GUI now removes only the actually-deleted cards
  (previously nothing was removed and re-deleting caused errors).
- Retired-discriminator accounts (`username#0`) now display cleanly.
- Concurrent scans/deletions were possible by double-clicking; operations
  are now guarded.
- `429` handling: respects the `Retry-After` header, no longer burns the
  retry budget, and aborts safely if stuck in a rate-limit loop.
- Search result "context" padding messages and other authors' messages are
  excluded from deletion targets.
- Broken CI workflow (`environment.yml` did not exist in the repo).
- Deleted a committed `discord_tool.log`, `build/` directory and
  `__pycache__/`; added a proper `.gitignore` (including `token.txt`).

### Changed
- HTTP requests use a persistent `requests.Session` (connection pooling) and
  fail fast with clear, typed errors (`AuthenticationError`,
  `NetworkError`, `DiscordAPIError`).
- Token priority is now: `DISCORD_TOKEN` env var → `token.txt` → prompt, and
  tokens are validated for basic format and always masked in logs.

### Removed
- **Browser token extraction & DPAPI/AES decryption** from `token_finder.py`.
  Reading and decrypting credential stores of Chrome/Edge/Brave/Opera is
  infostealer behavior, would get the project flagged by antivirus and
  secret-scanning tools, and serves no legitimate purpose here. Auto-Find now
  only reads the local Discord desktop app and never decrypts anything.
- Debug `print` statements that cluttered deletion output.

## [2.0.0] - 2026-01-14

Initial uploaded GUI version with keyword filtering and safety delays.
