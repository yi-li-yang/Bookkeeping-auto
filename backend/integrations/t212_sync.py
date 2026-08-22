"""
Trading 212 sync: fetch from the API and persist into the existing tables.

Design notes worth keeping:

* Holdings are written as a dated snapshot into `investment_holdings`, the same
  table the PDF Activity Statement path writes to, so the five existing read
  endpoints pick API data up with no changes.
* Each account gets a synthetic `SourceFile` row rather than making
  `source_file_id` nullable, which in SQLite would need a full table rebuild.
  It also keeps `POST /api/ingest?force=true` from touching API data, because
  that only clears files found under INPUT_DIR.
* Cash is stored as a synthetic CASH holding so the portfolio total reconciles
  with what Trading 212 itself reports.
"""
import json
import logging
from datetime import date, datetime

from sqlalchemy.orm import Session

from backend.config import settings
from backend.db.models import (
    InvestmentDividend,
    InvestmentHolding,
    SourceFile,
    SyncRun,
    Transaction,
)
from backend.integrations.trading212 import (
    T212Account,
    T212AuthError,
    T212Error,
    Trading212Client,
    candidate_accounts,
)

logger = logging.getLogger(__name__)

# Trading 212 issues one key pair per account, and the env var name is only a
# hint, so the account is identified from what the API actually returns.
_ACCOUNT_LABELS = {
    "invest": "Trading 212 Invest",
    "stocks_isa": "Trading 212 Stocks ISA",
}

_TRANSACTION_CATEGORIES = {
    "DEPOSIT": "Transfers",
    "WITHDRAW": "Transfers",
    "TRANSFER": "Transfers",
    "FEE": "Investment Fees",
}


def _account_label(
    db: Session, account: T212Account, summary: dict, positions: list[dict]
) -> str:
    """
    Work out which Trading 212 account a key pair belongs to.

    The env var name is only a hint, and /equity/account/summary does not say
    whether an account is Invest or Stocks ISA. So when the hint is unusable we
    identify the account by overlapping the live positions' ISINs against the
    historical snapshots already in the database, which do carry the account
    name. Falling back to the numeric account id keeps two unlabelled keys
    distinguishable rather than silently merging them.
    """
    if account.slug in _ACCOUNT_LABELS:
        return _ACCOUNT_LABELS[account.slug]

    live_isins = {
        (p.get("instrument") or {}).get("isin")
        for p in positions
        if (p.get("instrument") or {}).get("isin")
    }
    if live_isins:
        scores: list[tuple[float, str]] = []
        known_accounts = [
            r[0]
            for r in db.query(InvestmentHolding.account_name).distinct().all()
        ]
        for name in known_accounts:
            known_isins = {
                r[0]
                for r in db.query(InvestmentHolding.isin)
                .filter(InvestmentHolding.account_name == name)
                .filter(InvestmentHolding.isin.isnot(None))
                .distinct()
                .all()
            }
            if known_isins:
                scores.append((len(live_isins & known_isins) / len(live_isins), name))

        scores.sort(reverse=True)
        if scores:
            best_score, best_name = scores[0]
            runner_up = scores[1][0] if len(scores) > 1 else 0.0
            # Invest and ISA legitimately share popular tickers, so require both
            # a strong absolute match and a clear margin over the alternative.
            if best_score >= 0.5 and best_score >= runner_up * 1.3:
                logger.info(
                    "Matched Trading 212 key %r to %r (%.0f%% ISIN overlap, "
                    "runner-up %.0f%%)",
                    account.slug, best_name, best_score * 100, runner_up * 100,
                )
                return best_name
            logger.warning(
                "Could not confidently match Trading 212 key %r to a known account "
                "(best %r at %.0f%%, runner-up %.0f%%). Labelling by account id; "
                "set T212_API_KEY_INVEST / T212_API_KEY_STOCKS_ISA to name it "
                "explicitly.",
                account.slug, best_name, best_score * 100, runner_up * 100,
            )

    account_id = summary.get("id")
    return f"Trading 212 {account_id}" if account_id else "Trading 212"


def _derive_fx_rate(position: dict, value_gbp: float | None) -> float | None:
    """
    FX rate as a multiplier, derived rather than taken from walletImpact.fxImpact.

    fxImpact is a GBP profit-and-loss amount attributable to currency movement,
    not a rate. Writing it into fx_rate would make the column mean two different
    things depending on whether the row came from a PDF or the API.
    """
    quantity = position.get("quantity")
    price = position.get("currentPrice")
    if value_gbp and quantity and price:
        denominator = quantity * price
        if denominator:
            return round(value_gbp / denominator, 6)
    currency = (position.get("instrument") or {}).get("currency")
    return 1.0 if currency == "GBP" else None


def _source_file(db: Session, account: T212Account, label: str) -> SourceFile:
    """Get-or-create the synthetic SourceFile that owns this account's API rows."""
    filename = f"trading212:{account.slug}"
    row = db.query(SourceFile).filter(SourceFile.filename == filename).one_or_none()
    if row is None:
        row = SourceFile(filename=filename, file_type="api", status="done", row_count=0)
        db.add(row)
        db.flush()
    row.processed_at = datetime.utcnow()
    row.status = "done"
    row.error_message = None
    return row


def _write_holdings(
    db: Session,
    source_file: SourceFile,
    account_name: str,
    report_date: str,
    positions: list[dict],
    summary: dict,
) -> int:
    """
    Replace this account's snapshot for `report_date`.

    Delete-then-insert keeps a same-day re-sync idempotent and makes a position
    sold today correctly disappear, while leaving PDF-sourced rows alone since
    those belong to a different source_file_id.
    """
    db.query(InvestmentHolding).filter(
        InvestmentHolding.source_file_id == source_file.id,
        InvestmentHolding.report_date == report_date,
    ).delete(synchronize_session=False)

    written = 0
    for position in positions:
        instrument = position.get("instrument") or {}
        ticker = instrument.get("ticker") or ""
        wallet = position.get("walletImpact") or {}
        value_gbp = wallet.get("currentValue")
        if value_gbp is None:
            continue

        db.add(
            InvestmentHolding(
                report_date=report_date,
                account_name=account_name,
                # "AAPL_US_EQ" -> "AAPL", matching the PDF path's convention.
                instrument=ticker.split("_")[0] or ticker,
                isin=instrument.get("isin"),
                currency=instrument.get("currency"),
                quantity=position.get("quantity"),
                avg_price=position.get("averagePricePaid"),
                current_price=position.get("currentPrice"),
                fx_rate=_derive_fx_rate(position, value_gbp),
                cost_gbp=wallet.get("totalCost"),
                value_gbp=value_gbp,
                return_gbp=wallet.get("unrealizedProfitLoss"),
                source_file_id=source_file.id,
            )
        )
        written += 1

    cash = _cash_value(summary)
    if cash is not None:
        # cost_gbp / return_gbp stay NULL on purpose: _return_pct already handles
        # None, /performance filters rows without a return, and SQL SUM skips
        # NULLs -- so cash lands in value and allocation without distorting
        # return figures, and no existing endpoint needs changing.
        db.add(
            InvestmentHolding(
                report_date=report_date,
                account_name=account_name,
                instrument="CASH",
                currency=summary.get("currency") or "GBP",
                value_gbp=cash,
                source_file_id=source_file.id,
            )
        )
        written += 1

    return written


def _cash_value(summary: dict) -> float | None:
    """
    Cash as a residual: totalValue - investments.currentValue.

    Derived this way rather than summing availableToTrade + reservedForOrders +
    inPies so that sum(value_gbp) equals Trading 212's own totalValue exactly.
    """
    total = summary.get("totalValue")
    invested = (summary.get("investments") or {}).get("currentValue")
    if total is None or invested is None:
        return None
    cash = round(total - invested, 2)
    return cash if abs(cash) >= 0.01 else None


def _existing_external_ids(db: Session, source_file_id: int) -> set[str]:
    rows = (
        db.query(Transaction.external_id)
        .filter(Transaction.source_file_id == source_file_id)
        .filter(Transaction.external_id.isnot(None))
        .all()
    )
    return {r[0] for r in rows}


def _transaction_external_id(slug: str, item: dict) -> str:
    reference = item.get("reference")
    if reference:
        return f"t212:{slug}:{reference}"
    return (
        f"t212:{slug}:{item.get('dateTime')}:{item.get('type')}:{item.get('amount')}"
    )


def _write_transactions(
    db: Session, source_file: SourceFile, account_name: str, slug: str, items: list[dict]
) -> int:
    known = _existing_external_ids(db, source_file.id)
    written = 0

    for item in items:
        external_id = _transaction_external_id(slug, item)
        if external_id in known:
            continue
        known.add(external_id)

        tx_type = (item.get("type") or "").upper()
        amount = item.get("amount")
        date_time = item.get("dateTime") or ""
        if amount is None or not date_time:
            continue

        # Defensive: normalise sign from the type rather than trusting it.
        magnitude = abs(float(amount))
        signed = magnitude if tx_type == "DEPOSIT" else -magnitude

        description = f"Trading 212 {tx_type.title()}"
        if item.get("reference"):
            description = f"{description} - {item['reference']}"

        db.add(
            Transaction(
                date=date_time[:10],
                description=description,
                amount=signed,
                category=_TRANSACTION_CATEGORIES.get(tx_type, "Transfers"),
                account_name=account_name,
                account_type="investment",
                source_file_id=source_file.id,
                confidence=1.0,          # deterministic, not an LLM guess
                is_user_edited=False,
                raw_row=json.dumps(item),
                external_id=external_id,
            )
        )
        written += 1

    return written


def _write_dividends(
    db: Session, source_file: SourceFile, account_name: str, items: list[dict]
) -> int:
    existing = {
        r[0]
        for r in db.query(InvestmentDividend.reference)
        .filter(InvestmentDividend.account_name == account_name)
        .all()
    }
    written = 0

    for item in items:
        reference = item.get("reference")
        if reference and reference in existing:
            continue
        if reference:
            existing.add(reference)

        paid_on = item.get("paidOn") or ""
        amount = item.get("amount")
        if amount is None or not paid_on:
            continue

        instrument = item.get("instrument") or {}
        db.add(
            InvestmentDividend(
                account_name=account_name,
                instrument=instrument.get("name") or item.get("ticker"),
                isin=instrument.get("isin"),
                ticker=item.get("ticker"),
                dividend_type=item.get("type"),
                paid_on=paid_on[:10],
                amount=float(amount),
                currency=item.get("currency"),
                quantity=item.get("quantity"),
                gross_per_share=item.get("grossAmountPerShare"),
                reference=reference,
                source_file_id=source_file.id,
            )
        )
        written += 1

    return written


def sync_account(
    db: Session,
    account: T212Account,
    include_history: bool = True,
    max_pages: int = 20,
) -> dict:
    """Sync one Trading 212 account. Raises on failure; sync_all captures those."""
    report_date = date.today().isoformat()

    with Trading212Client(account, env=settings.t212_env) as client:
        summary = client.account_summary()
        positions = client.positions()
        label = _account_label(db, account, summary, positions)

        transactions: list[dict] = []
        dividends: list[dict] = []
        if include_history:
            source_file_peek = (
                db.query(SourceFile)
                .filter(SourceFile.filename == f"trading212:{account.slug}")
                .one_or_none()
            )
            known = (
                _existing_external_ids(db, source_file_peek.id)
                if source_file_peek
                else set()
            )
            # Newest-first, so stopping at the first already-stored record turns
            # a re-sync into a single page.
            transactions = client.history_transactions(
                max_pages=max_pages,
                stop_when=lambda i: _transaction_external_id(account.slug, i) in known,
            )
            dividends = client.history_dividends(max_pages=max_pages)

    source_file = _source_file(db, account, label)
    holdings_written = _write_holdings(
        db, source_file, label, report_date, positions, summary
    )
    transactions_written = _write_transactions(
        db, source_file, label, account.slug, transactions
    )
    dividends_written = _write_dividends(db, source_file, label, dividends)

    source_file.row_count = holdings_written
    db.commit()

    return {
        "slug": account.slug,
        "account_name": label,
        "report_date": report_date,
        "holdings": holdings_written,
        "transactions": transactions_written,
        "dividends": dividends_written,
        "total_value_gbp": summary.get("totalValue"),
        "cash_gbp": _cash_value(summary),
        "error": None,
    }


def sync_all(db: Session, include_history: bool = True, max_pages: int = 20) -> dict:
    """
    Sync every configured account, sequentially.

    Per-account failures are captured rather than raised so one bad credential
    cannot stop the other account from syncing.
    """
    accounts = candidate_accounts()
    results: list[dict] = []

    for account in accounts:
        run = SyncRun(connector=f"trading212:{account.slug}", status="running")
        db.add(run)
        db.commit()

        try:
            result = sync_account(
                db, account, include_history=include_history, max_pages=max_pages
            )
            run.status = "ok"
            run.items_written = (
                result["holdings"] + result["transactions"] + result["dividends"]
            )
            run.message = (
                f"{result['holdings']} holdings, {result['transactions']} transactions, "
                f"{result['dividends']} dividends"
            )
        except T212AuthError as exc:
            db.rollback()
            logger.warning("Trading 212 auth failed for %s: %s", account.slug, exc)
            result = _error_result(account, str(exc))
            run.status = "auth_required"
            run.message = str(exc)
        except (T212Error, Exception) as exc:  # noqa: BLE001 - recorded, not swallowed
            db.rollback()
            logger.exception("Trading 212 sync failed for %s", account.slug)
            result = _error_result(account, str(exc))
            run.status = "error"
            run.message = str(exc)

        run.finished_at = datetime.utcnow()
        db.add(run)
        db.commit()
        results.append(result)

    return {
        "accounts": results,
        "synced": sum(1 for r in results if not r["error"]),
        "failed": sum(1 for r in results if r["error"]),
    }


def _error_result(account: T212Account, message: str) -> dict:
    return {
        "slug": account.slug,
        "account_name": _ACCOUNT_LABELS.get(account.slug, "Trading 212"),
        "report_date": None,
        "holdings": 0,
        "transactions": 0,
        "dividends": 0,
        "total_value_gbp": None,
        "cash_gbp": None,
        "error": message,
    }
