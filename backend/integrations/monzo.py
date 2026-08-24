"""
Monzo API client.

The only one of this user's six UK accounts with an API an individual can
actually use. Open banking aggregators either skip the UK entirely (Enable
Banking) or have closed their free tier (GoCardless/Nordigen), so the rest stay
on statement import -- see backend/integrations/enablebanking.py.

Pure HTTP, no SQLAlchemy, so it is testable against a mock transport.

Three Monzo-specific rules that shape everything here:

1. **The history window shuts after five minutes.** Immediately following
   authentication a client can read the whole transaction history; five minutes
   later it can only read the last 90 days, and re-authenticating does not
   reopen it. So the first sync must pull everything and store it. This is not a
   rate limit to pace around -- miss it and the history is gone until the next
   consent.

2. **Only confidential clients get refresh tokens.** Register the OAuth client
   as confidential or every access token expiry means a manual re-auth.

3. **A refresh invalidates the previous refresh token.** The new one must be
   persisted before the next call, or the connection is dead and needs a full
   re-consent. `refresh_tokens` returns the whole set for exactly this reason.
"""
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)

AUTH_URL = "https://auth.monzo.com/"
API_BASE = "https://api.monzo.com"

# Monzo does not publish its rate limits. Pace conservatively rather than
# discover them the hard way; a personal sync is a handful of calls.
_MIN_INTERVAL = 0.5
_MAX_RETRIES = 3
_MAX_BACKOFF_SECONDS = 60.0

# The API caps a page at 100 regardless of what you ask for.
_PAGE_LIMIT = 100

# Access tokens last ~6 hours. Refresh early so a long sync cannot expire
# mid-run.
_EXPIRY_LEEWAY_SECONDS = 300

# 403 bodies that mean "the user has not approved this client in the app yet"
# rather than "these credentials are wrong".
_PENDING_APPROVAL_CODES = (
    "insufficient_permissions",
    "verification_required",
)


class MonzoError(RuntimeError):
    """Any Monzo API failure."""


class MonzoAuthError(MonzoError):
    """401 - the access token is expired, revoked, or wrong."""


class MonzoApprovalRequired(MonzoAuthError):
    """
    Authenticated, but the user has not yet approved data access in the app.

    Monzo sends a push notification after the OAuth round trip and refuses data
    endpoints until it is accepted. The tokens are valid, so this must not be
    reported as an auth failure -- the fix is to tap Approve on the phone, not
    to reconnect.
    """


class MonzoRateLimitError(MonzoError):
    """429 that survived the retry budget."""


@dataclass(frozen=True)
class MonzoTokens:
    access_token: str
    refresh_token: str | None
    expires_at: float          # unix seconds
    user_id: str | None = None
    client_id: str | None = None

    @classmethod
    def from_response(cls, data: dict, now: float | None = None) -> "MonzoTokens":
        issued = now if now is not None else time.time()
        return cls(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token"),
            expires_at=issued + float(data.get("expires_in") or 0),
            user_id=data.get("user_id"),
            client_id=data.get("client_id"),
        )

    def is_expired(self, now: float | None = None) -> bool:
        return (now if now is not None else time.time()) >= (
            self.expires_at - _EXPIRY_LEEWAY_SECONDS
        )

    def to_dict(self) -> dict:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "user_id": self.user_id,
            "client_id": self.client_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MonzoTokens":
        return cls(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token"),
            expires_at=float(data.get("expires_at") or 0),
            user_id=data.get("user_id"),
            client_id=data.get("client_id"),
        )


# -- OAuth ------------------------------------------------------------------
# Module functions rather than client methods: these run before any access
# token exists.

def authorize_url(client_id: str, redirect_uri: str, state: str) -> str:
    """The URL to send the user to. `state` is the CSRF guard, not optional here."""
    if not client_id:
        raise MonzoAuthError(
            "MONZO_CLIENT_ID is not set. Register a confidential OAuth client at "
            "https://developers.monzo.com/ and add its id and secret to .env."
        )
    return AUTH_URL + "?" + urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "state": state,
    })


def _token_request(payload: dict, transport=None, now: float | None = None) -> MonzoTokens:
    with httpx.Client(base_url=API_BASE, timeout=30.0, transport=transport) as client:
        response = client.post("/oauth2/token", data=payload)

    if response.status_code >= 400:
        raise MonzoAuthError(
            f"Monzo token endpoint returned HTTP {response.status_code}: "
            f"{response.text[:300]}"
        )

    tokens = MonzoTokens.from_response(response.json(), now=now)
    if tokens.refresh_token is None:
        logger.warning(
            "Monzo issued no refresh token. The OAuth client is registered as "
            "non-confidential, so access will need re-authorising by hand every "
            "few hours. Re-register it as confidential."
        )
    return tokens


def exchange_code(
    client_id: str, client_secret: str, redirect_uri: str, code: str,
    transport=None, now: float | None = None,
) -> MonzoTokens:
    return _token_request({
        "grant_type": "authorization_code",
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "code": code,
    }, transport=transport, now=now)


def refresh_tokens(
    client_id: str, client_secret: str, refresh_token: str,
    transport=None, now: float | None = None,
) -> MonzoTokens:
    """
    Swap a refresh token for a fresh pair.

    Monzo invalidates the old refresh token on success, so the caller MUST
    persist the returned set before making another request.
    """
    return _token_request({
        "grant_type": "refresh_token",
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
    }, transport=transport, now=now)


# -- Client -----------------------------------------------------------------

class MonzoClient:
    def __init__(
        self,
        access_token: str,
        base_url: str = API_BASE,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if not access_token:
            raise MonzoAuthError("No Monzo access token. Connect the account first.")
        self._sleep = sleep
        self._last_call: float | None = None
        self._client = httpx.Client(
            base_url=base_url,
            timeout=timeout,
            transport=transport,
            headers={"Authorization": f"Bearer {access_token}"},
        )

    def __enter__(self) -> "MonzoClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # -- internals ---------------------------------------------------------

    def _pace(self) -> None:
        if self._last_call is not None:
            wait = _MIN_INTERVAL - (time.monotonic() - self._last_call)
            if wait > 0:
                self._sleep(wait)
        self._last_call = time.monotonic()

    def _get(self, path: str, params: dict | None = None) -> Any:
        last_error: Exception | None = None

        for attempt in range(_MAX_RETRIES):
            self._pace()
            response = self._client.get(path, params=params)

            if response.status_code == 403:
                body = response.text[:300]
                # Pending in-app approval, not a broken token. Both arrive as a
                # bare 403, so they are told apart by the error code.
                #
                # Monzo uses two codes for this and the docs mention neither:
                # `forbidden.insufficient_permissions` is what a freshly
                # authorised client actually gets before the user taps Approve,
                # observed live. Keep `verification_required` too, which is what
                # other endpoints have been reported to return.
                if any(code in body for code in _PENDING_APPROVAL_CODES):
                    raise MonzoApprovalRequired(
                        "Monzo is waiting for you to approve data access in the "
                        "app. Open Monzo on your phone and accept the request, "
                        "then sync again. The connection itself is fine."
                    )
                raise MonzoAuthError(f"Monzo refused {path} (403): {body}")

            if response.status_code == 401:
                raise MonzoAuthError(
                    f"Monzo rejected the access token on {path}. It has expired "
                    f"or been revoked; reconnect the account. "
                    f"{response.text[:200]}"
                )

            if response.status_code == 429:
                wait = min(2.0 ** attempt, _MAX_BACKOFF_SECONDS)
                retry_after = response.headers.get("retry-after")
                if retry_after:
                    try:
                        wait = min(float(retry_after), _MAX_BACKOFF_SECONDS)
                    except ValueError:
                        pass
                logger.warning(
                    "Monzo rate limited %s; waiting %.1fs (%d/%d)",
                    path, wait, attempt + 1, _MAX_RETRIES,
                )
                last_error = MonzoRateLimitError(f"429 on {path}")
                self._sleep(wait)
                continue

            if response.status_code >= 500:
                wait = min(2.0 ** attempt, _MAX_BACKOFF_SECONDS)
                logger.warning(
                    "Monzo %d on %s; retrying in %.1fs (%d/%d)",
                    response.status_code, path, wait, attempt + 1, _MAX_RETRIES,
                )
                last_error = MonzoError(f"HTTP {response.status_code} on {path}")
                self._sleep(wait)
                continue

            if response.status_code >= 400:
                raise MonzoError(
                    f"Monzo returned HTTP {response.status_code} on {path}: "
                    f"{response.text[:300]}"
                )

            return response.json() if response.content else {}

        raise last_error or MonzoError(f"Gave up on {path} after {_MAX_RETRIES} attempts")

    # -- public ------------------------------------------------------------

    def whoami(self) -> dict:
        return self._get("/ping/whoami")

    def accounts(self, include_closed: bool = False) -> list[dict]:
        payload = self._get("/accounts")
        rows = payload.get("accounts") or []
        if include_closed:
            return rows
        return [a for a in rows if not a.get("closed")]

    def balance(self, account_id: str) -> dict:
        return self._get("/balance", params={"account_id": account_id})

    def pots(self, account_id: str) -> list[dict]:
        payload = self._get("/pots", params={"current_account_id": account_id})
        return [p for p in (payload.get("pots") or []) if not p.get("deleted")]

    def transactions(
        self,
        account_id: str,
        since: str | None = None,
        before: str | None = None,
        max_pages: int = 200,
    ) -> list[dict]:
        """
        All transactions for an account, following Monzo's cursor pagination.

        `since` takes either an RFC 3339 timestamp or a transaction id; passing
        the last id seen is how a page is continued. Omitting it entirely asks
        for the full history, which only succeeds inside the five-minute window
        after authentication.

        max_pages defaults high because the first sync is the one chance at that
        full history -- a cap that truncates it costs data that cannot be
        re-fetched later.
        """
        items: list[dict] = []
        cursor = since

        for page in range(max_pages):
            params: dict[str, Any] = {
                "account_id": account_id,
                "limit": _PAGE_LIMIT,
                "expand[]": "merchant",
            }
            if cursor:
                params["since"] = cursor
            if before:
                params["before"] = before

            batch = self._get("/transactions", params=params).get("transactions") or []
            items.extend(batch)

            if len(batch) < _PAGE_LIMIT:
                return items

            last_id = batch[-1].get("id")
            if not last_id or last_id == cursor:
                logger.warning(
                    "Monzo pagination stalled on %s at page %d; stopping to avoid "
                    "a loop.", account_id, page + 1,
                )
                return items
            cursor = last_id

        logger.warning(
            "Stopped paginating Monzo transactions for %s at the %d-page cap; "
            "older records were not fetched.", account_id, max_pages,
        )
        return items
