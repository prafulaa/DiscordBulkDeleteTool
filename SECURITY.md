# Security Policy

## Scope

This tool runs locally on your machine and talks only to Discord's official
API. The most important security rule for users:

> **Your Discord token is a password.** Anyone who has it can control your
> account. Never share it, never commit it, never paste it into websites or
> other tools.

## Supported versions

| Version | Supported |
|---|---|
| 3.0.x | ✅ |
| < 3.0 | ❌ (please upgrade) |

## Reporting a vulnerability

Please open a [security advisory](https://github.com/prafulaa/DiscordBulkDeleteTool/security/advisories/new)
rather than a public issue. Include reproduction steps and affected versions.
You can expect an initial response within 7 days.

## Design commitments

- Tokens are accepted via environment variable, local `token.txt`, or hidden
  prompt — and are always masked in logs.
- No network requests go anywhere except `discord.com`.
- The project intentionally does **not** read or decrypt credential stores of
  other applications (this was removed in 3.0.0 as a security liability).
