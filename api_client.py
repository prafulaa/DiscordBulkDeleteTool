"""Discord REST client: session reuse, timeouts, rate-limit handling."""

import random
import time

import requests

from utils import logger, print_warning

API_BASE = "https://discord.com/api/v9"
REQUEST_TIMEOUT = 30
MAX_ATTEMPTS = 4          # network failures / hard errors
MAX_RATE_LIMIT_WAITS = 25  # 429s are normal here; they don't burn the retry budget
SEARCH_PAGE_SIZE = 25
SEARCH_OFFSET_CAP = 5000   # Discord rejects search offsets above this

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
)


class DiscordAPIError(Exception):
    """A request failed after Discord answered (or the network gave up)."""

    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


class AuthenticationError(DiscordAPIError):
    """401 — the token is invalid or was revoked."""


class NetworkError(DiscordAPIError):
    """The request could not be completed (connection failures, timeouts)."""


class DiscordClient:
    def __init__(self, token, base_url=API_BASE):
        self.token = token
        self.base_url = base_url
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": token,
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            }
        )
        self.user_id = None

    def close(self):
        self.session.close()

    # --- Core request plumbing -------------------------------------------

    def _request(self, method, endpoint, params=None, json_data=None):
        """Perform a request, transparently handling rate limits and
        transient network errors. Returns the final response or raises."""
        url = f"{self.base_url}{endpoint}"
        attempts = 0
        rate_limit_waits = 0

        while True:
            try:
                response = self.session.request(
                    method, url, params=params, json=json_data, timeout=REQUEST_TIMEOUT
                )
            except requests.RequestException as exc:
                attempts += 1
                if attempts >= MAX_ATTEMPTS:
                    raise NetworkError(
                        f"{method} {endpoint} failed after {attempts} network errors: {exc}"
                    ) from exc
                logger.warning(
                    "Network error on %s %s (attempt %d/%d): %s",
                    method, endpoint, attempts, MAX_ATTEMPTS, exc,
                )
                time.sleep(min(1.5 * attempts, 6))
                continue

            if response.status_code == 429:
                rate_limit_waits += 1
                if rate_limit_waits > MAX_RATE_LIMIT_WAITS:
                    raise DiscordAPIError(
                        f"Rate limited {rate_limit_waits} times in a row on {endpoint} — "
                        "stopping to stay safe.",
                        429,
                    )
                retry_after = self._retry_after_seconds(response)
                print_warning(f"Rate limited — waiting {retry_after:.1f}s before retrying…")
                time.sleep(retry_after + random.uniform(0.25, 0.75))
                continue

            if response.status_code == 401:
                raise AuthenticationError("Invalid or expired token (401). Get a fresh one.")

            return response

    @staticmethod
    def _retry_after_seconds(response):
        """Prefer the Retry-After header, fall back to the JSON body."""
        header = response.headers.get("Retry-After")
        if header:
            try:
                return min(max(float(header), 0.1), 120.0)
            except ValueError:
                pass
        try:
            body = response.json()
            retry_after = float(body.get("retry_after", 1.0))
            return min(max(retry_after, 0.1), 120.0)
        except (ValueError, AttributeError):
            return 1.0

    def request_json(self, method, endpoint, params=None, json_data=None):
        """Request wrapper that returns parsed JSON and raises DiscordAPIError
        with a useful message for anything other than 2xx."""
        response = self._request(method, endpoint, params=params, json_data=json_data)
        if 200 <= response.status_code < 300:
            try:
                return response.json()
            except ValueError:
                raise DiscordAPIError(
                    f"{method} {endpoint} returned non-JSON "
                    f"(status {response.status_code}) — possibly blocked by Cloudflare.",
                    response.status_code,
                ) from None
        detail = self._error_detail(response)
        raise DiscordAPIError(
            f"{method} {endpoint} failed with status {response.status_code}: {detail}",
            response.status_code,
        )

    @staticmethod
    def _error_detail(response):
        try:
            body = response.json()
            if isinstance(body, dict):
                return body.get("message", str(body)[:200])
            return str(body)[:200]
        except ValueError:
            return response.text[:120] or "(empty body)"

    # --- API operations ----------------------------------------------------

    def validate_token(self):
        """Fetch the authenticated user; also stores self.user_id."""
        data = self.request_json("GET", "/users/@me")
        if data and "id" in data:
            self.user_id = data["id"]
        return data

    # --- Discovery (browse without typing IDs) ------------------------------

    def fetch_guilds(self):
        """Servers the account is in (first page — 200 covers the max a
        non-bot account can join). Returns [{id, name, icon}, …]."""
        return self.request_json("GET", "/users/@me/guilds", params={"limit": 200}) or []

    def fetch_guild_channels(self, guild_id):
        """Text-like channels (type 0 text, type 5 announcement) of a guild,
        ordered by position."""
        channels = self.request_json("GET", f"/guilds/{guild_id}/channels") or []
        text_like = [c for c in channels if c.get("type") in (0, 5)]
        text_like.sort(key=lambda c: (c.get("position", 0), c.get("id", "")))
        return text_like

    def fetch_dm_channels(self):
        """DM and group-DM channels (type 1 / 3) with a normalized shape:
        [{id, name, recipient}], name falls back to the first recipient."""
        channels = self.request_json("GET", "/users/@me/channels") or []
        normalized = []
        for channel in channels:
            if channel.get("type") not in (1, 3):
                continue
            recipients = channel.get("recipients") or []
            first = recipients[0] if recipients else {}
            name = first.get("global_name") or first.get("username") or "Unknown"
            if channel.get("type") == 3 and len(recipients) > 1:
                name = f"Group ({len(recipients)} members)"
            normalized.append(
                {
                    "id": channel["id"],
                    "type": channel.get("type"),
                    "name": name,
                    "recipient": first,
                }
            )
        return normalized

    def fetch_history(self, channel_id, before=None, after=None, limit=100):
        """Fetch raw channel history (newest-first), 100 messages per request.

        This is the fast scan path: 4× more messages per request than the
        search endpoint and far friendlier rate limits. The caller filters
        for its own messages client-side."""
        params = {"limit": min(limit, 100)}
        if before:
            params["before"] = before
        if after:
            params["after"] = after
        return self.request_json("GET", f"/channels/{channel_id}/messages", params=params) or []

    def search_messages(
        self,
        guild_id=None,
        channel_id=None,
        author_id=None,
        content=None,
        min_id=None,
        max_id=None,
        offset=0,
    ):
        """Search messages (Discord's search endpoint, 25 results per page)."""
        if not guild_id and not channel_id:
            raise ValueError("search_messages requires guild_id or channel_id")

        params = {"offset": offset, "limit": SEARCH_PAGE_SIZE}
        if author_id:
            params["author_id"] = author_id
        if content:
            params["content"] = content
        if min_id:
            params["min_id"] = min_id
        if max_id:
            params["max_id"] = max_id

        if guild_id:
            endpoint = f"/guilds/{guild_id}/messages/search"
        else:
            endpoint = f"/channels/{channel_id}/messages/search"

        return self.request_json("GET", endpoint, params=params)

    def delete_message(self, channel_id, message_id):
        """Delete a single own message.

        Returns one of: "deleted" (204), "already_gone" (404),
        "forbidden" (403), "failed" (anything else).
        Raises AuthenticationError if the token stopped working.
        """
        endpoint = f"/channels/{channel_id}/messages/{message_id}"
        response = self._request("DELETE", endpoint)

        if response.status_code == 204:
            return "deleted"
        if response.status_code == 404:
            return "already_gone"
        if response.status_code == 403:
            logger.warning("Delete of %s forbidden (403)", message_id)
            return "forbidden"

        logger.error(
            "Delete of %s failed: status %s — %s",
            message_id, response.status_code, self._error_detail(response),
        )
        return "failed"
