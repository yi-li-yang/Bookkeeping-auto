"""
Enable Banking API client.

Enable Banking is a licensed AISP, so it can reach UK and EU banks that do not
expose an API to individuals directly (Chase UK, for instance, serves only
FCA-registered TPPs). Its Restricted Production mode is free and scoped to
accounts you whitelist yourself.

Pure HTTP, no SQLAlchemy, so it is testable against a mock transport. Auth is a
short-lived RS256 JWT signed with the application's private key rather than a
static secret, so there is no bearer token to store.
"""
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import httpx
import jwt

logger = logging.getLogger(__name__)

BASE_URL = "https://api.enablebanking.com"

_JWT_ISSUER = "enablebanking.com"
_JWT_AUDIENCE = "api.enablebanking.com"
# Enable Banking caps token lifetime at one hour. Mint per client rather than
# persisting, since a token is cheap and a stored one only goes stale.
_JWT_TTL_SECONDS = 3600

_MIN_INTERVAL = 0.35
_MAX_RETRIES = 3
_MAX_BACKOFF_SECONDS = 60.0


class EBError(RuntimeError):
    """Any Enable Banking API failure."""


class EBAuthError(EBError):
    """401/403 - bad application credentials, or an expired bank consent."""


class EBConsentExpired(EBAuthError):
    """The bank consent has lapsed and the user must reauthorise."""


class EBApplicationInactive(EBAuthError):
    """
    The application is registered but not yet activated.

    A freshly registered restricted-production application stays inactive until
    at least one account is linked, and every endpoint returns 403 until then.
    Credentials are fine in this state, so it must not be reported as an auth
    failure -- the fix is to link an account, not to re-check the key.
    """


class EBRateLimitError(EBError):
    """429 that survived the retry budget."""


@dataclass(frozen=True)
class ASPSP:
    """A bank, as Enable Banking describes it."""
    name: str
    country: str
    logo: str | None = None
    psu_types: tuple[str, ...] = ()
    auth_methods: tuple[str, ...] = ()

    @classmethod
    def from_api(cls, data: dict) -> "ASPSP":
        return cls(
            name=data.get("name") or "",
            country=data.get("country") or "",
            logo=data.get("logo"),
            psu_types=tuple(data.get("psu_types") or ()),
            auth_methods=tuple(
                m.get("name", "") if isinstance(m, dict) else str(m)
                for m in (data.get("auth_methods") or ())
            ),
        )


def build_jwt(application_id: str, private_key: str, now: int | None = None) -> str:
    """
    Sign the RS256 assertion Enable Banking expects.

    The application id goes in the `kid` header, not the payload -- that is how
    the key is looked up on their side.
    """
    issued = int(now if now is not None else time.time())
    return jwt.encode(
        {
            "iss": _JWT_ISSUER,
            "aud": _JWT_AUDIENCE,
            "iat": issued,
            "exp": issued + _JWT_TTL_SECONDS,
        },
        private_key,
        algorithm="RS256",
        headers={"kid": application_id},
    )


def load_private_key(path: Path) -> str:
    if not path.exists():
        raise EBAuthError(
            f"Enable Banking private key not found at {path}. Download the .pem "
            "for your application from the Enable Banking Control Panel and save "
            "it there, or set EB_PRIVATE_KEY_PATH."
        )
    return path.read_text()


class EnableBankingClient:
    def __init__(
        self,
        application_id: str,
        private_key: str,
        base_url: str = BASE_URL,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if not application_id:
            raise EBAuthError(
                "EB_APPLICATION_ID is not set. Register an application at "
                "https://enablebanking.com/sign-in/ and add its id to .env."
            )
        self.application_id = application_id
        self._sleep = sleep
        self._last_call: float | None = None
        self._client = httpx.Client(
            base_url=base_url,
            timeout=timeout,
            transport=transport,
            headers={
                "Authorization": f"Bearer {build_jwt(application_id, private_key)}"
            },
        )

    def __enter__(self) -> "EnableBankingClient":
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

    def _request(self, method: str, path: str, **kwargs) -> Any:
        last_error: Exception | None = None

        for attempt in range(_MAX_RETRIES):
            self._pace()
            response = self._client.request(method, path, **kwargs)

            if response.status_code in (401, 403):
                body = response.text[:300]
                # An inactive application 403s on every endpoint, including
                # /aspsps, even though the JWT verified. Check this before the
                # path-based split below, which would otherwise call it a
                # credentials problem and send you re-checking a working key.
                if "not active" in body.lower():
                    raise EBApplicationInactive(
                        "Enable Banking application "
                        f"{self.application_id} is registered but not active. "
                        "Link at least one account in the Control Panel "
                        "(Country -> ASPSP -> Usage type -> Link); activation "
                        "follows automatically. Credentials are fine -- the "
                        "JWT was accepted."
                    )
                # A lapsed bank consent and bad app credentials both surface as
                # 401/403, but they need different fixes, so separate them.
                if "session" in path.lower() or "account" in path.lower():
                    raise EBConsentExpired(
                        f"Enable Banking refused {path} (HTTP "
                        f"{response.status_code}). The bank consent has most "
                        f"likely expired and needs reauthorising. {body}"
                    )
                raise EBAuthError(
                    f"Enable Banking rejected the application credentials on "
                    f"{path} (HTTP {response.status_code}). Check "
                    f"EB_APPLICATION_ID and that the private key matches it. {body}"
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
                    "Enable Banking rate limited %s; waiting %.1fs (%d/%d)",
                    path, wait, attempt + 1, _MAX_RETRIES,
                )
                last_error = EBRateLimitError(f"429 on {path}")
                self._sleep(wait)
                continue

            if response.status_code >= 500:
                wait = min(2.0 ** attempt, _MAX_BACKOFF_SECONDS)
                logger.warning(
                    "Enable Banking %d on %s; retrying in %.1fs (%d/%d)",
                    response.status_code, path, wait, attempt + 1, _MAX_RETRIES,
                )
                last_error = EBError(f"HTTP {response.status_code} on {path}")
                self._sleep(wait)
                continue

            if response.status_code >= 400:
                raise EBError(
                    f"Enable Banking returned HTTP {response.status_code} on "
                    f"{path}: {response.text[:300]}"
                )

            return response.json() if response.content else {}

        raise last_error or EBError(f"Gave up on {path} after {_MAX_RETRIES} attempts")

    def _paginate(self, path: str, params: dict | None = None,
                  max_pages: int = 50) -> list[dict]:
        """Follow continuation_key pagination, which every listing endpoint uses."""
        items: list[dict] = []
        params = dict(params or {})

        for _ in range(max_pages):
            payload = self._request("GET", path, params=params)
            items.extend(payload.get("transactions") or payload.get("items") or [])
            key = payload.get("continuation_key")
            if not key:
                return items
            params["continuation_key"] = key

        logger.warning(
            "Stopped paginating %s at the %d-page cap; older records were not "
            "fetched.", path, max_pages,
        )
        return items

    # -- public ------------------------------------------------------------

    def aspsps(self, country: str = "GB", psu_type: str = "personal") -> list[ASPSP]:
        """Banks Enable Banking can reach. This is the coverage check."""
        payload = self._request(
            "GET", "/aspsps", params={"country": country, "psu_type": psu_type}
        )
        return [ASPSP.from_api(a) for a in payload.get("aspsps", [])]

    def start_authorisation(
        self, aspsp_name: str, country: str, redirect_url: str,
        state: str, valid_until: str, psu_type: str = "personal",
    ) -> dict:
        """Begin consent. Returns a url to send the user to."""
        return self._request("POST", "/auth", json={
            "access": {"valid_until": valid_until},
            "aspsp": {"name": aspsp_name, "country": country},
            "state": state,
            "redirect_url": redirect_url,
            "psu_type": psu_type,
        })

    def create_session(self, code: str) -> dict:
        """Exchange the callback code for a session listing the linked accounts."""
        return self._request("POST", "/sessions", json={"code": code})

    def get_session(self, session_id: str) -> dict:
        return self._request("GET", f"/sessions/{session_id}")

    def account_balances(self, account_uid: str) -> dict:
        return self._request("GET", f"/accounts/{account_uid}/balances")

    def account_transactions(
        self, account_uid: str, date_from: str | None = None, max_pages: int = 50,
    ) -> list[dict]:
        params = {"date_from": date_from} if date_from else {}
        return self._paginate(
            f"/accounts/{account_uid}/transactions", params=params, max_pages=max_pages
        )


def client_from_settings(settings=None) -> EnableBankingClient:
    """Build a client from config, raising an actionable error if unconfigured."""
    if settings is None:
        from backend.config import settings as _settings

        settings = _settings
    return EnableBankingClient(
        application_id=settings.eb_application_id or "",
        private_key=load_private_key(settings.eb_private_key_path),
    )


def is_configured(settings=None) -> bool:
    if settings is None:
        from backend.config import settings as _settings

        settings = _settings
    return bool(
        settings.eb_application_id
        and settings.eb_private_key_path
        and settings.eb_private_key_path.exists()
    )
