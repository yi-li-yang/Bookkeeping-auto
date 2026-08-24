"""
Bank connection endpoints.

Monzo only. The other five accounts this user holds have no API an individual
can reach -- see backend/integrations/enablebanking.py for why the open banking
aggregator route does not work for UK accounts -- so they stay on statement
import.

The OAuth round trip is served by the app itself rather than a standalone local
callback server, since the app is already a web server on a known port.
"""
import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from backend.config import settings
from backend.db.database import get_db
from backend.db.models import SyncRun
from backend.integrations import monzo, monzo_auth, monzo_sync

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/banks", tags=["banks"])

# The OAuth state parameter, held in memory. Single-user, single-process, and a
# consent round trip lasts a minute or two -- persisting it would outlive its
# usefulness. A restart mid-flow just means starting the link again.
_pending_states: set[str] = set()


def _page(title: str, body: str, ok: bool = True) -> HTMLResponse:
    """
    The callback lands in a browser tab, not in the SPA, so it needs to render
    something on its own.
    """
    colour = "#15803d" if ok else "#b91c1c"
    return HTMLResponse(
        f"""<!doctype html><meta charset="utf-8">
<title>{title}</title>
<body style="font-family:system-ui,sans-serif;max-width:34rem;margin:4rem auto;
             padding:0 1rem;line-height:1.6">
  <h1 style="color:{colour};font-size:1.35rem">{title}</h1>
  <p>{body}</p>
  <p><a href="/">Back to the dashboard</a></p>
</body>""",
        status_code=200 if ok else 400,
    )


@router.get("/status")
def bank_status(db: Session = Depends(get_db)):
    """What is connected, and what the last sync did."""
    last = (
        db.query(SyncRun)
        .filter(SyncRun.connector.like("monzo%"))
        .order_by(SyncRun.started_at.desc())
        .first()
    )
    return {
        "monzo": {
            "configured": settings.monzo_configured,
            "connected": monzo_auth.is_connected(settings),
            "last_sync": {
                "status": last.status,
                "at": last.started_at.isoformat() if last.started_at else None,
                "items_written": last.items_written,
                "message": last.message,
            } if last else None,
        },
        # Recorded so the UI can state plainly that these are manual, rather
        # than leaving them looking like connectors that failed.
        "manual": [
            "Bank of Scotland", "Chase", "American Express", "M&S Bank", "Klarna",
        ],
    }


@router.get("/monzo/connect")
def monzo_connect():
    """Start the Monzo consent flow."""
    if not settings.monzo_configured:
        raise HTTPException(
            status_code=400,
            detail=(
                "MONZO_CLIENT_ID / MONZO_CLIENT_SECRET are not set. Register a "
                "confidential OAuth client at https://developers.monzo.com/ "
                f"with redirect URL {settings.monzo_redirect_uri}, then add both "
                "to .env."
            ),
        )

    state = secrets.token_urlsafe(24)
    _pending_states.add(state)
    url = monzo.authorize_url(
        settings.monzo_client_id, settings.monzo_redirect_uri, state
    )
    return RedirectResponse(url, status_code=307)


@router.get("/monzo/callback")
def monzo_callback(
    code: str | None = Query(None),
    state: str | None = Query(None),
    error: str | None = Query(None),
):
    if error:
        return _page("Monzo refused the connection", f"Monzo returned: {error}", ok=False)
    if not code:
        return _page("No authorisation code", "Monzo did not return a code.", ok=False)

    # Reject a state we did not issue: without this the endpoint would accept a
    # code injected by any page that can make the browser navigate here.
    if not state or state not in _pending_states:
        return _page(
            "Unrecognised request",
            "The state parameter did not match a pending connection. Start again "
            "from the dashboard.",
            ok=False,
        )
    _pending_states.discard(state)

    try:
        tokens = monzo.exchange_code(
            settings.monzo_client_id, settings.monzo_client_secret,
            settings.monzo_redirect_uri, code,
        )
        monzo_auth.save_tokens(settings, tokens)
    except monzo.MonzoError as exc:
        logger.error("Monzo token exchange failed: %s", exc)
        return _page("Could not complete the connection", str(exc), ok=False)

    return _page(
        "Monzo connected",
        "Open the Monzo app and approve the data access request, then run a "
        "sync from the dashboard. <strong>Do this promptly</strong> -- Monzo "
        "serves the full transaction history only in the first few minutes "
        "after linking; after that only the last 90 days are reachable.",
    )


@router.post("/monzo/sync")
def monzo_sync_endpoint(
    full_history: bool | None = Query(
        None,
        description=(
            "Force a full-history pull. Defaults to automatic: full on an "
            "account's first sync, incremental afterwards."
        ),
    ),
    db: Session = Depends(get_db),
):
    """
    Sync Monzo.

    A sync `def`, so FastAPI runs it in a threadpool -- the client's pacing is
    blocking and would otherwise stall the event loop.
    """
    result = monzo_sync.sync_all(db, full_history=full_history)
    if result["status"] == "auth_required":
        raise HTTPException(status_code=401, detail=result["message"])
    return result


@router.post("/monzo/disconnect")
def monzo_disconnect():
    """
    Forget the stored tokens.

    Leaves already-synced transactions in place; this severs the connection, it
    does not delete history.
    """
    removed = monzo_auth.clear_tokens(settings)
    return {"disconnected": removed}
