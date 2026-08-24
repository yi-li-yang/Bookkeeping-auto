"""
Monzo token persistence and refresh.

Separate from monzo.py so that module stays free of config and filesystem, and
from monzo_sync.py because the OAuth callback route needs to store tokens
before any syncing happens.

The refresh token is the single point of failure for this integration: Monzo
invalidates the old one on every refresh, so losing the current value means a
manual re-consent. Writes are therefore atomic (temp file + replace) -- a crash
partway through must not leave a truncated file where a working token was.
"""
import json
import logging
import os
import tempfile
from pathlib import Path

from backend.integrations.monzo import (
    MonzoAuthError,
    MonzoTokens,
    refresh_tokens,
)

logger = logging.getLogger(__name__)


class MonzoNotConnected(MonzoAuthError):
    """No stored tokens. The account has never been linked, or was disconnected."""


def _token_path(settings) -> Path:
    if settings.monzo_token_path is None:
        raise MonzoAuthError("MONZO_TOKEN_PATH is not configured.")
    return settings.monzo_token_path


def load_tokens(settings) -> MonzoTokens | None:
    path = _token_path(settings)
    if not path.exists():
        return None
    try:
        return MonzoTokens.from_dict(json.loads(path.read_text()))
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        logger.error(
            "Monzo token file at %s is unreadable (%s). Reconnect the account.",
            path, exc,
        )
        return None


def save_tokens(settings, tokens: MonzoTokens) -> None:
    path = _token_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Write-then-replace: os.replace is atomic on both POSIX and Windows, so a
    # reader either sees the old file or the new one, never a half-written one.
    handle, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(handle, "w") as fh:
            json.dump(tokens.to_dict(), fh, indent=2)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def clear_tokens(settings) -> bool:
    path = _token_path(settings)
    if path.exists():
        path.unlink()
        return True
    return False


def is_connected(settings) -> bool:
    return load_tokens(settings) is not None


def access_token(settings, now: float | None = None) -> str:
    """
    A usable access token, refreshing and re-persisting it if needed.

    Every caller goes through here rather than reading the file directly, so
    there is exactly one place that can forget to save a rotated refresh token.
    """
    tokens = load_tokens(settings)
    if tokens is None:
        raise MonzoNotConnected(
            "Monzo is not connected. Visit /api/banks/monzo/connect to link the "
            "account."
        )

    if not tokens.is_expired(now=now):
        return tokens.access_token

    if not tokens.refresh_token:
        raise MonzoNotConnected(
            "The Monzo access token has expired and there is no refresh token "
            "to renew it -- the OAuth client was registered as "
            "non-confidential. Re-register it as confidential at "
            "https://developers.monzo.com/ and reconnect."
        )

    if not settings.monzo_configured:
        raise MonzoAuthError(
            "MONZO_CLIENT_ID / MONZO_CLIENT_SECRET are not set, so the expired "
            "token cannot be refreshed."
        )

    logger.info("Monzo access token expired; refreshing.")
    fresh = refresh_tokens(
        settings.monzo_client_id, settings.monzo_client_secret,
        tokens.refresh_token, now=now,
    )
    # Persist before returning: Monzo has already invalidated the old refresh
    # token by this point, so a failure to save here loses the connection.
    save_tokens(settings, fresh)
    return fresh.access_token
