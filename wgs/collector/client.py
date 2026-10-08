"""A small GitHub REST client that is polite about rate limits.

Handles:
- primary rate limits (separate quotas for "core" and "search"), by reading the
  X-RateLimit-* headers and sleeping until the reset time when the quota runs low;
- secondary rate limits (403/429), by honouring Retry-After or backing off;
- transient failures (5xx, timeouts, connection errors), with exponential backoff + jitter.

Every other status (200, 202, 204, 404, 451, ...) is returned to the caller, which
decides what it means for that endpoint.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from typing import Callable

import requests

from wgs.config import GitHubSettings

log = logging.getLogger(__name__)

RETRYABLE_STATUS = {500, 502, 503, 504}
MAX_RATE_LIMIT_WAITS = 20  # safety net against an endless rate-limit loop


class GitHubAPIError(RuntimeError):
    """Raised when a request keeps failing after all retries."""


def safe_get(client: "GitHubClient", path: str, **kwargs) -> requests.Response | None:
    """GET that turns "failed after all retries" into None, so one bad repo never stops a run."""
    try:
        return client.get(path, **kwargs)
    except GitHubAPIError as exc:
        log.warning("Giving up on %s for now: %s", path, exc)
        return None


@dataclass
class RateLimit:
    remaining: int | None = None
    reset: float | None = None  # epoch seconds
    limit: int | None = None


class GitHubClient:
    def __init__(
        self,
        token: str | None,
        settings: GitHubSettings | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
    ):
        self.settings = settings or GitHubSettings()
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "what-gets-starred-research",
            }
        )
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"
        self._sleep = sleep
        self._clock = clock
        self.limits: dict[str, RateLimit] = {"core": RateLimit(), "search": RateLimit()}
        self.request_count = 0

    # ------------------------------------------------------------------ public API
    def get(
        self,
        path: str,
        params: dict | None = None,
        *,
        accept: str | None = None,
        resource: str = "core",
        max_retries: int | None = None,
        timeout: float | None = None,
    ) -> requests.Response:
        url = path if path.startswith("http") else f"{self.settings.api_url}{path}"
        headers = {"Accept": accept} if accept else None
        retries = self.settings.max_retries if max_retries is None else max_retries
        failures = rate_limited = 0
        last_error: str = ""

        # Failures (errors, timeouts) and rate-limit waits are counted separately: waiting for
        # a quota is expected behaviour, not a sign that the request is broken.
        while failures <= retries and rate_limited <= MAX_RATE_LIMIT_WAITS:
            self._wait_for_quota(resource)
            try:
                resp = self.session.get(
                    url, params=params, headers=headers, timeout=timeout or self.settings.timeout
                )
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                failures += 1
                self._backoff(failures - 1, last_error, final=failures > retries)
                continue

            self.request_count += 1
            self._update_limits(resp, resource)

            if resp.status_code in (403, 429):
                wait = self._rate_limit_wait(resp, resource, rate_limited)
                if wait is None:
                    return resp  # a genuine 403, e.g. "Repository access blocked"
                last_error = f"rate limited ({resp.status_code})"
                rate_limited += 1
                log.warning("%s on %s, sleeping %.0fs", last_error, path, wait)
                self._sleep(wait)
                continue

            if resp.status_code in RETRYABLE_STATUS:
                last_error = f"HTTP {resp.status_code}"
                failures += 1
                self._backoff(failures - 1, last_error, final=failures > retries)
                continue

            return resp

        raise GitHubAPIError(f"GET {path} failed after {retries} retries: {last_error}")

    def rate_limit_status(self) -> dict:
        """Current quotas. Calling /rate_limit does not count against the quota."""
        resp = self.session.get(f"{self.settings.api_url}/rate_limit", timeout=self.settings.timeout)
        resp.raise_for_status()
        return resp.json()["resources"]

    # ------------------------------------------------------------------ internals
    def _update_limits(self, resp: requests.Response, resource: str) -> None:
        h = resp.headers
        if "X-RateLimit-Remaining" not in h:
            return
        name = h.get("X-RateLimit-Resource", resource)
        state = self.limits.setdefault(name, RateLimit())
        state.remaining = int(h["X-RateLimit-Remaining"])
        state.reset = float(h.get("X-RateLimit-Reset", 0)) or None
        state.limit = int(h.get("X-RateLimit-Limit", 0)) or None

    def _wait_for_quota(self, resource: str) -> None:
        state = self.limits.get(resource)
        threshold = self.settings.min_remaining.get(resource, 0)
        if state is None or state.remaining is None or state.reset is None:
            return
        if state.remaining > threshold:
            return
        wait = state.reset - self._clock() + 1
        if wait > 0:
            log.info(
                "%s quota low (%d left), sleeping %.0fs until reset", resource, state.remaining, wait
            )
            self._sleep(wait)
        state.remaining = None  # unknown until the next response tells us

    def _rate_limit_wait(self, resp: requests.Response, resource: str, attempt: int) -> float | None:
        """Seconds to wait if this 403/429 is a rate limit, otherwise None."""
        retry_after = resp.headers.get("Retry-After")
        if retry_after is not None:
            return max(float(retry_after), 1.0)

        if resp.headers.get("X-RateLimit-Remaining") == "0":
            reset = float(resp.headers.get("X-RateLimit-Reset", 0))
            return max(reset - self._clock() + 1, 1.0)

        try:
            message = resp.json().get("message", "").lower()
        except ValueError:
            message = resp.text.lower()
        if "rate limit" in message or resp.status_code == 429:
            # Secondary rate limit without guidance: GitHub asks for at least a minute.
            return max(60.0, self._backoff_seconds(attempt))
        return None

    def _backoff_seconds(self, attempt: int) -> float:
        base = self.settings.backoff_base**attempt
        return min(base + random.uniform(0, 1), self.settings.backoff_max)

    def _backoff(self, attempt: int, reason: str, final: bool = False) -> None:
        if final:
            return  # no retry follows, so there is nothing to wait for
        wait = self._backoff_seconds(attempt)
        log.warning("%s, retry %d in %.1fs", reason, attempt + 1, wait)
        self._sleep(wait)
