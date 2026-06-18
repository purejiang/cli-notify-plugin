"""Relay HTTP client: POST + retry + JWT refresh + offline cache.

Handles communication with the cloud relay server. Supports:
  - Fire-and-forget POST for event messages
  - Synchronous POST-and-wait for request messages (approval flow)
  - Exponential backoff with jitter on retryable errors
  - Concurrent-safe JWT refresh (re-reads config before refreshing)
  - Optional offline caching when the relay is unreachable
"""

import os
import random
import sys
import time
from typing import Optional

import httpx

from config_manager import ConfigManager

NON_RETRYABLE = {400, 403, 404, 405, 409, 410, 422}
MAX_RETRIES = 3


class RelayClient:
    """HTTP client for the CLI-Notify relay server."""

    def __init__(self, config):
        self.config = config
        self.client = httpx.Client(timeout=httpx.Timeout(30.0))

    def post(self, envelope: dict) -> bool:
        """POST an envelope to /hook/relay (fire-and-forget).

        Returns True if the relay accepted the envelope, False otherwise.
        On persistent failure with offline_cache enabled, writes to the
        offline cache.
        """
        url = f"{self.config.relay_url}/hook/relay"
        headers = {"Authorization": f"Bearer {self.config.jwt}"}

        for attempt in range(MAX_RETRIES):
            try:
                resp = self.client.post(url, json=envelope, headers=headers)
                if resp.status_code == 200:
                    return True
                if resp.status_code == 401 and self._try_refresh():
                    headers["Authorization"] = f"Bearer {self.config.jwt}"
                    continue
                if resp.status_code in NON_RETRYABLE:
                    _log(f"Non-retryable: {resp.status_code}")
                    return False
            except (httpx.RequestError, httpx.TimeoutException) as e:
                _log(f"Network error (attempt {attempt + 1}): {e}")

            if attempt < MAX_RETRIES - 1:
                delay = min((2 ** attempt) + random.random(), 30)
                time.sleep(delay)

        # All retries failed: offline cache
        if self.config.get("offline_cache"):
            from offline_cache import OfflineCache  # late import to avoid cycle
            data_dir = ConfigManager.get_data_dir()
            cache_path = os.path.join(data_dir, "offline_cache.jsonl")
            cache = OfflineCache(cache_path, self.config.get("offline_cache_max", 1000))
            cache.append(envelope)
            _log(f"Cached offline ({cache.size()} total)")

        return False

    def post_and_wait(self, envelope: dict, timeout: int) -> Optional[dict]:
        """POST an approval request and wait for the relay HTTP response.

        Blocks until the relay responds (which may wait for mobile input).
        Returns the response dict on success, or None on timeout/failure
        (caller should fall back to the configured fallback_action).
        """
        url = f"{self.config.relay_url}/hook/relay"
        headers = {"Authorization": f"Bearer {self.config.jwt}"}

        try:
            resp = self.client.post(
                url, json=envelope, headers=headers,
                timeout=httpx.Timeout(timeout / 1000.0 + 5),
            )
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 401 and self._try_refresh():
                headers["Authorization"] = f"Bearer {self.config.jwt}"
                resp = self.client.post(
                    url, json=envelope, headers=headers,
                    timeout=httpx.Timeout(timeout / 1000.0 + 5),
                )
                if resp.status_code == 200:
                    return resp.json()
        except (httpx.RequestError, httpx.TimeoutException) as e:
            _log(f"Approval request failed: {e}")
        return None

    def _try_refresh(self) -> bool:
        """Try to refresh the JWT.

        Before refreshing, re-reads config.json in case another hook process
        already refreshed the token. This makes the refresh concurrent-safe
        across multiple Claude Code hook invocations.

        Returns True if the JWT was refreshed (either by us or another process).
        """
        if not self.config.get("refresh_token"):
            return False

        # Re-read config — another process may have already refreshed
        fresh = ConfigManager.load()
        if fresh.get("jwt") != self.config.get("jwt"):
            self.config["jwt"] = fresh["jwt"]
            self.config["refresh_token"] = fresh.get("refresh_token")
            return True

        # We need to refresh ourselves
        try:
            resp = self.client.post(
                f"{self.config.relay_url}/auth/refresh",
                json={"refresh_token": self.config.refresh_token},
            )
            if resp.status_code == 200:
                data = resp.json()
                self.config["jwt"] = data["jwt"]
                self.config["refresh_token"] = data["refresh_token"]
                ConfigManager.save(self.config)
                return True
        except Exception:
            pass

        _log("JWT refresh failed — please re-run /cli-notify:setup")
        return False


def _log(msg: str) -> None:
    """Write a log message to stderr (prefixed with [cli-notify])."""
    print(f"[cli-notify] {msg}", file=sys.stderr)
