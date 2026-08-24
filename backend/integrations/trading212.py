"""
Trading 212 API client.

Pure HTTP: no SQLAlchemy in this module, so it can be exercised against a mock
transport. Persistence lives in t212_sync.py.

Rate limits are per-account and the API punishes bursts even when a burst stays
under the documented per-minute ceiling, so every request goes through a
sleep-before-send gate and accounts are synced sequentially.
"""
import base64
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable

import httpx

logger = logging.getLogger(__name__)

API_PREFIX = "/api/v0"

# Minimum seconds between consecutive calls to the same endpoint, from the
# published limits. 50 req/min endpoints are paced at 1.2s rather than fired
# in a burst.
RATE_LIMITS: dict[str, float] = {
    "/equity/account/summary": 5.0,
    "/equity/positions": 1.0,
    "/equity/history/transactions": 1.2,
    "/equity/history/dividends": 1.2,
    "/equity/history/orders": 1.2,
    "/equity/metadata/exchanges": 30.0,
    "/equity/metadata/instruments": 50.0,
}
DEFAULT_MIN_INTERVAL = 1.2

# Rate limits are per ACCOUNT, not per endpoint, and Trading 212 throttles
# bursts even when each endpoint is individually within budget. Without this
# floor, a sync firing summary -> positions -> transactions -> dividends
# back-to-back hits four different endpoints with no gap and gets a 403.
# 50 req/min works out to exactly 1.2s, which is too tight to sustain across
# many pages -- observed 429s at that rate. A little headroom avoids them.
GLOBAL_MIN_INTERVAL = 1.4

# The docs describe x-ratelimit-reset as a unix timestamp, but that is not
# verified; clamp whatever we compute so a misread header cannot sleep forever.
_MAX_BACKOFF_SECONDS = 60.0
_FALLBACK_BACKOFF_SECONDS = 5.0
_MAX_RETRIES = 3


class T212Error(RuntimeError):
    """Any Trading 212 API failure."""


class T212AuthError(T212Error):
    """401/403 - bad credentials, wrong environment, or missing permissions."""


class T212RateLimitError(T212Error):
    """429 that survived the retry budget."""


@dataclass(frozen=True)
class T212Account:
    slug: str                          # "invest" | "stocks_isa" | env-derived hint
    api_key_id: str
    api_secret: str
    account_name: str | None = None    # filled in once the API confirms it


def auth_header(key_id: str, secret: str) -> str:
    """Basic auth header value. Exposed so tests can assert the exact encoding."""
    token = base64.b64encode(f"{key_id}:{secret}".encode()).decode()
    return f"Basic {token}"


def base_url(env: str) -> str:
    env = (env or "live").strip().lower()
    if env not in {"live", "demo"}:
        raise ValueError(f"T212_ENV must be 'live' or 'demo', got {env!r}")
    return f"https://{env}.trading212.com"


def _min_interval(path: str) -> float:
    """Longest-prefix match so sub-paths still pace correctly."""
    best = DEFAULT_MIN_INTERVAL
    best_len = -1
    for prefix, interval in RATE_LIMITS.items():
        if path.startswith(prefix) and len(prefix) > best_len:
            best, best_len = interval, len(prefix)
    return best


class Trading212Client:
    def __init__(
        self,
        account: T212Account,
        env: str = "live",
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.account = account
        self.env = env
        self._sleep = sleep
        self._last_call: dict[str, float] = {}
        self._last_any_call: float | None = None
        self._client = httpx.Client(
            base_url=base_url(env),
            timeout=timeout,
            transport=transport,
            headers={
                "Authorization": auth_header(account.api_key_id, account.api_secret)
            },
        )

    def __enter__(self) -> "Trading212Client":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # -- internals ---------------------------------------------------------

    def _pace(self, path: str) -> None:
        """
        Sleep so requests stay evenly spaced.

        Two constraints, both required: the per-endpoint interval, and a global
        floor between any two requests. The limits are per account, so hitting
        four different endpoints in quick succession is still a burst.
        """
        key = path.split("?")[0]
        interval = _min_interval(key.replace(API_PREFIX, "", 1))

        waits = []
        previous = self._last_call.get(key)
        if previous is not None:
            waits.append(interval - (time.monotonic() - previous))
        if self._last_any_call is not None:
            waits.append(GLOBAL_MIN_INTERVAL - (time.monotonic() - self._last_any_call))

        wait = max(waits) if waits else 0
        if wait > 0:
            self._sleep(wait)

        now = time.monotonic()
        self._last_call[key] = now
        self._last_any_call = now

    def _retry_after(self, response: httpx.Response) -> float:
        reset = response.headers.get("x-ratelimit-reset")
        if reset:
            try:
                delta = float(reset) - time.time()
            except ValueError:
                delta = _FALLBACK_BACKOFF_SECONDS
            # A sane value is a short wait. Anything else means we misread the
            # header's units, so fall back rather than trusting it.
            if 0 <= delta <= _MAX_BACKOFF_SECONDS:
                return delta
        return _FALLBACK_BACKOFF_SECONDS

    def _get(self, path: str, params: dict | None = None) -> Any:
        last_error: Exception | None = None

        for attempt in range(_MAX_RETRIES):
            self._pace(path)
            response = self._client.get(path, params=params)

            if response.status_code == 401:
                raise T212AuthError(
                    f"Trading 212 rejected the credentials for {self.account.slug!r} "
                    f"(HTTP 401). API keys are environment-specific: a {self.env!r} "
                    f"key cannot be used against the other environment. Check the key "
                    "id and secret are both from the same generation."
                )

            if response.status_code == 403:
                raise T212AuthError(
                    f"Trading 212 refused {path} for {self.account.slug!r} (HTTP 403). "
                    "Either the API key lacks the permission this endpoint needs "
                    "(check the key's Permissions list in the app), or requests were "
                    "sent too fast - limits are per account, so bursts across "
                    "different endpoints count too."
                )

            if response.status_code == 429:
                wait = self._retry_after(response)
                logger.warning(
                    "Trading 212 rate limited %s; waiting %.1fs (attempt %d/%d)",
                    path, wait, attempt + 1, _MAX_RETRIES,
                )
                last_error = T212RateLimitError(f"429 on {path}")
                self._sleep(wait)
                continue

            if response.status_code >= 500:
                wait = min(2.0 ** attempt, _MAX_BACKOFF_SECONDS)
                logger.warning(
                    "Trading 212 %d on %s; retrying in %.1fs (attempt %d/%d)",
                    response.status_code, path, wait, attempt + 1, _MAX_RETRIES,
                )
                last_error = T212Error(f"HTTP {response.status_code} on {path}")
                self._sleep(wait)
                continue

            if response.status_code >= 400:
                raise T212Error(
                    f"Trading 212 returned HTTP {response.status_code} on {path}: "
                    f"{response.text[:300]}"
                )

            # Proactively wait out an exhausted window rather than earning a 429.
            remaining = response.headers.get("x-ratelimit-remaining")
            if remaining is not None:
                try:
                    if int(remaining) <= 0:
                        self._sleep(self._retry_after(response))
                except ValueError:
                    pass

            return response.json()

        raise last_error or T212Error(
            f"Gave up on {path} after {_MAX_RETRIES} attempts"
        )

    def _paginate(
        self,
        path: str,
        limit: int = 50,
        max_pages: int = 20,
        stop_when: Callable[[dict], bool] | None = None,
    ) -> list[dict]:
        """
        Follow cursor pagination via nextPagePath.

        The transactions endpoint rejects a cursor on the first request - only
        `limit` is allowed - so page one is sent bare.

        nextPagePath is not consistent across endpoints: history/dividends
        returns a full path ("/api/v0/equity/history/dividends?limit=50&..."),
        while history/transactions returns only the query string
        ("limit=50&cursor=...&time=..."). Both forms are handled rather than
        assuming either, because assuming the full-path form sends the query
        string as if it were a path and earns a 403.
        """
        items: list[dict] = []
        next_path: str | None = None

        for page in range(max_pages):
            if page == 0:
                payload = self._get(f"{API_PREFIX}{path}", params={"limit": limit})
            else:
                assert next_path is not None
                if next_path.startswith("/"):
                    request_path = next_path
                else:
                    query = next_path.lstrip("?")
                    request_path = f"{API_PREFIX}{path}?{query}"
                payload = self._get(request_path)

            if not isinstance(payload, dict):
                break

            for item in payload.get("items", []):
                if stop_when is not None and stop_when(item):
                    return items
                items.append(item)

            next_path = payload.get("nextPagePath")
            if not next_path:
                return items

        if next_path:
            logger.warning(
                "Stopped paginating %s at the %d-page cap; older records were not "
                "fetched. Re-run with a higher max_pages for a full backfill.",
                path, max_pages,
            )
        return items

    # -- public ------------------------------------------------------------

    def account_summary(self) -> dict:
        return self._get(f"{API_PREFIX}/equity/account/summary")

    def positions(self) -> list[dict]:
        result = self._get(f"{API_PREFIX}/equity/positions")
        return result if isinstance(result, list) else []

    def history_transactions(self, max_pages: int = 20, stop_when=None) -> list[dict]:
        return self._paginate(
            "/equity/history/transactions", max_pages=max_pages, stop_when=stop_when
        )

    def history_dividends(self, max_pages: int = 20, stop_when=None) -> list[dict]:
        return self._paginate(
            "/equity/history/dividends", max_pages=max_pages, stop_when=stop_when
        )

    def history_orders(self, max_pages: int = 20, stop_when=None) -> list[dict]:
        """
        Executed and unexecuted orders, newest first.

        Each item is `{"order": {...}, "fill": {...}}`, and the `fill` key is
        absent for anything that never executed -- so its presence, not the
        status string, is what marks a real trade. One order can yield several
        fills, which is why callers key on the fill id.
        """
        return self._paginate(
            "/equity/history/orders", max_pages=max_pages, stop_when=stop_when
        )


def candidate_accounts(settings=None) -> list[T212Account]:
    """Every complete key+secret pair found in the environment."""
    if settings is None:
        from backend.config import settings as _settings

        settings = _settings
    return [
        T212Account(slug=slug, api_key_id=key_id, api_secret=secret)
        for slug, key_id, secret in settings.t212_key_pairs
    ]
