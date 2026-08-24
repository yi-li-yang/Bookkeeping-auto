"""
Monzo -> database persistence.

Writes into the same `transactions` table as file ingestion and the Trading 212
sync, so every read endpoint works unchanged.

Idempotency is by `external_id` (`monzo:<transaction_id>`), matching the T212
path, so re-syncing overlapping windows never duplicates.
"""
import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from backend.db.models import SourceFile, Transaction, SyncRun
from backend.integrations import monzo_auth
from backend.integrations.monzo import (
    MonzoApprovalRequired,
    MonzoClient,
    MonzoError,
)

logger = logging.getLogger(__name__)

# Monzo only serves 90 days once the post-auth window closes. Ask for a little
# less than the limit so a clock skew cannot push the request outside it.
_INCREMENTAL_DAYS = 85

# Overlap each incremental sync with what we already have, so a transaction that
# settled late (its `created` moves) is still picked up. Duplicates are stopped
# by external_id, so the overlap is free.
_OVERLAP_DAYS = 3

# Monzo's own category strings are a fixed, coarse enum ("general", "expenses")
# that would collide with the LLM's naturally-named categories. Only the two
# that are structurally certain are set here; the rest go to the LLM.
_POT_SCHEME = "uk_retail_pot"


# One label per Monzo account type. A single "Monzo" label was wrong: a user
# can hold a current account, a Flex credit account, a rewards pot and a joint
# account at once, and merging them under one name makes them indistinguishable
# in every chart and breaks credit-card balance matching, which keys on
# account_name.
_ACCOUNT_LABELS = {
    "uk_retail": "Monzo",
    "uk_retail_joint": "Monzo Joint",
    "uk_monzo_flex": "Monzo Flex",
    "uk_rewards": "Monzo Rewards",
    "uk_business": "Monzo Business",
}

# Monzo Flex is instalment credit, not a current account. Typed as `bank` it
# would count as cash in net worth and never match its credit_cards row.
_CREDIT_ACCOUNT_TYPES = {"uk_monzo_flex"}


def account_label(account: dict) -> str:
    """
    A stable display name, one per account.

    Deliberately not the account's `description` -- Monzo sets that to the
    account holder's name (or a raw internal id), which would put the user's
    real name on every row and in every chart legend.
    """
    monzo_type = account.get("type") or ""
    known = _ACCOUNT_LABELS.get(monzo_type)
    if known:
        return known

    # An unrecognised type must still get a distinct name rather than being
    # merged into another account's history.
    suffix = monzo_type.removeprefix("uk_").replace("_", " ").title()
    return f"Monzo {suffix}".strip() if suffix else "Monzo"


def account_kind(account: dict) -> str:
    """`account_type` for the transactions table."""
    return "credit_card" if account.get("type") in _CREDIT_ACCOUNT_TYPES else "bank"


def _source_file(db: Session, account_id: str, label: str) -> SourceFile:
    """Get-or-create the synthetic SourceFile that owns this account's API rows."""
    filename = f"monzo:{account_id}"
    row = db.query(SourceFile).filter(SourceFile.filename == filename).one_or_none()
    if row is None:
        row = SourceFile(filename=filename, file_type="api", status="done", row_count=0)
        db.add(row)
        db.flush()
    row.processed_at = datetime.utcnow()
    row.status = "done"
    row.error_message = None
    return row


def transaction_description(item: dict) -> str:
    """
    The most human-readable name available.

    Monzo's raw `description` is the card-network string ("TFL.GOV.UK/CP
    LONDON"); the expanded merchant carries a clean one ("Transport for
    London"). Prefer the clean one, fall back through counterparty, and only
    then use the raw string.
    """
    merchant = item.get("merchant")
    if isinstance(merchant, dict) and merchant.get("name"):
        return merchant["name"]

    counterparty = item.get("counterparty") or {}
    if counterparty.get("name"):
        return counterparty["name"]

    if item.get("scheme") == _POT_SCHEME:
        return "Monzo Pot Transfer"

    return (item.get("description") or "Monzo transaction").strip()


def own_account_ids(db: Session) -> set[str]:
    """
    Every Monzo account id this database has synced.

    Derived from the synthetic SourceFile names rather than a live API call, so
    it works offline, in tests, and during re-categorisation.
    """
    rows = (
        db.query(SourceFile.filename)
        .filter(SourceFile.filename.like("monzo:%"))
        .all()
    )
    return {r[0].split(":", 1)[1] for r in rows if ":" in r[0]}


def deterministic_category(item: dict, own_ids: set[str] | None = None) -> str | None:
    """
    Categories we know for certain, without asking the LLM.

    All three are movements of the user's own money rather than spending, so
    getting them into `Transfers` matters: the analytics layer excludes that
    category from income and expenses.

    The third rule is the important one. Funding a joint account looks exactly
    like a payment to another person -- same `p2p_payment` scheme, and the
    counterparty name is a person's name. Left to the LLM it gets read as
    whatever the joint account then pays for: two transfers of 2000 and 800 into
    a joint account that paid 1950 of rent were all filed as Rent, reporting
    4750 of rent in a month where 1950 left the household. The counterparty's
    `account_id` settles it -- if it is an account we also sync, the money never
    left.
    """
    if item.get("scheme") == _POT_SCHEME:
        return "Transfers"
    if item.get("is_load"):
        return "Transfers"      # a top-up, i.e. money in from another account

    counterparty = item.get("counterparty") or {}
    if own_ids and counterparty.get("account_id") in own_ids:
        return "Transfers"

    return None


def is_skippable(item: dict) -> bool:
    """
    Declines and zero-amount rows never moved money.

    Monzo returns declined attempts with the amount they would have been. Left
    in, a declined 500 pound payment reads as 500 pounds of spending.
    """
    if item.get("decline_reason"):
        return True
    return not item.get("amount")


def _existing_external_ids(db: Session, source_file_id: int) -> set[str]:
    rows = (
        db.query(Transaction.external_id)
        .filter(Transaction.source_file_id == source_file_id)
        .filter(Transaction.external_id.isnot(None))
        .all()
    )
    return {r[0] for r in rows}


def _since_for(db: Session, source_file_id: int, full_history: bool) -> str | None:
    """
    Where to resume from.

    None asks for everything, which Monzo only allows in the five minutes after
    authentication -- so it is used for the first sync of an account and never
    again.
    """
    if full_history:
        return None

    latest = (
        db.query(Transaction.date)
        .filter(Transaction.source_file_id == source_file_id)
        .order_by(Transaction.date.desc())
        .first()
    )
    now = datetime.now(timezone.utc)
    floor = now - timedelta(days=_INCREMENTAL_DAYS)

    if latest and latest[0]:
        try:
            start = datetime.fromisoformat(latest[0]).replace(tzinfo=timezone.utc)
            start -= timedelta(days=_OVERLAP_DAYS)
            # Never ask for older than Monzo will serve; it errors rather than
            # clamping.
            start = max(start, floor)
        except ValueError:
            start = floor
    else:
        start = floor

    return start.strftime("%Y-%m-%dT%H:%M:%SZ")


def _categorise(rows: list[dict]) -> dict[int, dict]:
    """
    Run the shared LLM categoriser over rows that need one.

    Imported lazily so a sync with nothing to categorise -- or a run without an
    Anthropic key -- does not touch the LLM module at all.
    """
    if not rows:
        return {}

    from backend.ingestion import llm_processor

    try:
        results = llm_processor.categorise_transactions(rows)
    except Exception as exc:                      # noqa: BLE001
        # A categorisation failure must not lose the transactions themselves.
        logger.error(
            "Monzo categorisation failed (%s); storing %d transactions "
            "uncategorised. Re-run the categoriser later.", exc, len(rows),
        )
        return {}
    return {c["id"]: c for c in results}


def _write_transactions(
    db: Session, source_file: SourceFile, label: str, items: list[dict],
    kind: str = "bank", own_ids: set[str] | None = None,
) -> int:
    known = _existing_external_ids(db, source_file.id)

    pending: list[tuple[str, dict, str, float, str]] = []
    for item in items:
        tx_id = item.get("id")
        if not tx_id or is_skippable(item):
            continue

        external_id = f"monzo:{tx_id}"
        if external_id in known:
            continue
        known.add(external_id)

        created = item.get("created") or ""
        if not created:
            continue

        # Minor units, and already signed the way this schema wants: negative
        # for money out. No sign inference needed, unlike Trading 212.
        amount = float(item["amount"]) / 100.0
        pending.append(
            (external_id, item, created[:10], amount, transaction_description(item))
        )

    if not pending:
        return 0

    # Ask the LLM only about rows whose category is not already certain.
    for_llm = [
        {"id": i, "date": date, "amount": amount, "description": description}
        for i, (_, item, date, amount, description) in enumerate(pending)
        if deterministic_category(item, own_ids) is None
    ]
    categories = _categorise(for_llm)

    for i, (external_id, item, date, amount, description) in enumerate(pending):
        certain = deterministic_category(item, own_ids)
        if certain is not None:
            category, confidence = certain, 1.0
        else:
            guess = categories.get(i, {})
            category, confidence = guess.get("category"), guess.get("confidence")

        db.add(
            Transaction(
                date=date,
                description=description,
                amount=amount,
                category=category,
                account_name=label,
                account_type=kind,
                source_file_id=source_file.id,
                confidence=confidence,
                is_user_edited=False,
                raw_row=json.dumps(item),
                external_id=external_id,
            )
        )

    return len(pending)


def sync_account(
    db: Session, client: MonzoClient, account: dict, full_history: bool = False,
    max_pages: int = 200,
) -> dict:
    account_id = account["id"]
    label = account_label(account)
    source_file = _source_file(db, account_id, label)
    db.flush()

    since = _since_for(db, source_file.id, full_history)
    items = client.transactions(account_id, since=since, max_pages=max_pages)
    written = _write_transactions(
        db, source_file, label, items, account_kind(account), own_account_ids(db),
    )

    # Flush first: the count below must include the rows just added, and
    # adding `written` to a post-autoflush count would double them.
    db.flush()
    source_file.row_count = (
        db.query(Transaction)
        .filter(Transaction.source_file_id == source_file.id)
        .count()
    )
    db.commit()

    return {
        "connector": f"monzo:{account_id}",
        "account_name": label,
        "status": "ok",
        "fetched": len(items),
        "written": written,
        "full_history": full_history,
    }


def sync_all(db: Session, full_history: bool | None = None, max_pages: int = 200) -> dict:
    """
    Sync every Monzo account.

    `full_history` defaults to "yes if this account has never been synced".
    That first run is the only chance at the complete history -- Monzo closes
    the window five minutes after authentication and reopening it needs a fresh
    consent -- so it must not be skipped by accident.
    """
    from backend.config import settings

    results: list[dict] = []
    try:
        token = monzo_auth.access_token(settings)
    except MonzoError as exc:
        _record_run(db, "monzo", "auth_required", 0, str(exc))
        return {"status": "auth_required", "message": str(exc), "accounts": []}

    try:
        with MonzoClient(token) as client:
            accounts = client.accounts()
            if not accounts:
                _record_run(db, "monzo", "ok", 0, "No open Monzo accounts.")
                return {"status": "ok", "message": "No open accounts.", "accounts": []}

            for account in accounts:
                first_run = (
                    db.query(SourceFile)
                    .filter(SourceFile.filename == f"monzo:{account['id']}")
                    .one_or_none()
                ) is None
                wants_all = first_run if full_history is None else full_history

                try:
                    results.append(
                        sync_account(db, client, account, wants_all, max_pages)
                    )
                except MonzoError as exc:
                    db.rollback()
                    logger.error("Monzo sync failed for %s: %s", account["id"], exc)
                    results.append({
                        "connector": f"monzo:{account['id']}",
                        "account_name": account_label(account),
                        "status": "error",
                        "written": 0,
                        "message": str(exc),
                    })
    except MonzoApprovalRequired as exc:
        _record_run(db, "monzo", "auth_required", 0, str(exc))
        return {"status": "auth_required", "message": str(exc), "accounts": []}
    except MonzoError as exc:
        _record_run(db, "monzo", "error", 0, str(exc))
        return {"status": "error", "message": str(exc), "accounts": []}

    written = sum(r.get("written", 0) for r in results)
    failed = [r for r in results if r.get("status") != "ok"]
    status = "ok" if not failed else ("error" if len(failed) == len(results) else "partial")
    _record_run(db, "monzo", status, written, "; ".join(
        f"{r['account_name']}: {r.get('message', '')}" for r in failed
    ) or None)

    return {"status": status, "written": written, "accounts": results}


def _record_run(
    db: Session, connector: str, status: str, items_written: int, message: str | None,
) -> None:
    now = datetime.utcnow()
    db.add(SyncRun(
        connector=connector, started_at=now, finished_at=now,
        status=status, items_written=items_written, message=message,
    ))
    db.commit()


def recategorise_internal_transfers(db: Session) -> list[dict]:
    """
    Re-apply the deterministic rules to Monzo rows already stored.

    Needed because own-account detection arrived after the first sync, and a
    transfer misfiled as spending inflates whatever category it was guessed
    into. Only ever moves a row *to* Transfers, and never touches a row the
    user has edited by hand.
    """
    own = own_account_ids(db)
    if not own:
        return []

    rows = (
        db.query(Transaction)
        .join(SourceFile, Transaction.source_file_id == SourceFile.id)
        .filter(SourceFile.filename.like("monzo:%"))
        .filter(Transaction.raw_row.isnot(None))
        .all()
    )

    changed = []
    for row in rows:
        if row.is_user_edited:
            continue
        try:
            item = json.loads(row.raw_row)
        except (TypeError, ValueError):
            continue

        certain = deterministic_category(item, own)
        if certain is None or row.category == certain:
            continue

        changed.append({
            "date": row.date, "amount": row.amount,
            "description": row.description,
            "from": row.category, "to": certain,
        })
        row.category = certain
        row.confidence = 1.0

    if changed:
        db.commit()
    return changed
