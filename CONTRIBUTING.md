# Contributing

Thanks for helping improve the Discord Bulk Delete Tool!

## Getting started

```bash
git clone https://github.com/prafulaa/DiscordBulkDeleteTool.git
cd DiscordBulkDeleteTool
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt pytest ruff   # Windows
# .venv/bin/pip install ... on macOS/Linux
```

## Before opening a PR

- `ruff check .` passes with no warnings.
- `pytest -v` passes — add tests for any new behavior.
- No secrets: never commit a real token, `token.txt`, or log files (all are
  gitignored; double-check before pushing).
- Update `CHANGELOG.md` under an *Unreleased* heading if your change is
  user-visible.

## Ground rules

- The tool must only ever delete **the logged-in user's own messages**.
- No functionality that reads, extracts, or decrypts credentials from other
  applications (browsers included). PRs adding such behavior will be closed.
- Keep rate-limit pacing conservative — account safety beats speed here.

## Reporting bugs

Open an issue with: Python version, OS, what you did, what you expected, and
the relevant redacted output (mask tokens/IDs). Never paste your token.
