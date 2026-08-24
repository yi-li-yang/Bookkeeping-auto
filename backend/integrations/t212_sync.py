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
    InvestmentTrade,
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

# Trading 212 returns transaction amounts unsigned -- a deposit, an interest
# payment and a withdrawal all arrive positive -- so direction comes from the
# type. Only types whose direction is unambiguous are forced; anything else
# keeps the sign the API gave, since guessing would corrupt the cash flow.
_INCOME_TYPES = {
    "DEPOSIT", "INTEREST_ON_FREE_CASH", "LENDING_INTEREST", "INTEREST", "DIVIDEND",
}
_EXPENSE_TYPES = {"WITHDRAW", "WITHDRAWAL", "FEE", "TAX", "CHARGE", "COMMISSION"}

_TRANSACTION_CATEGORIES = {
    "DEPOSIT": "Transfers",
    "WITHDRAW": "Transfers",
    "WITHDRAWAL": "Transfers",
    "TRANSFER": "Transfers",
    "FEE": "Investment Fees",
    "COMMISSION": "Investment Fees",
    "CHARGE": "Investment Fees",
    "TAX": "Investment Fees",
    "INTEREST_ON_FREE_CASH": "Interest",
    "LENDING_INTEREST": "Interest",
    "INTEREST": "Interest",
    "DIVIDEND": "Dividends",
}


def _signed_amount(tx_type: str, amount: float) -> float:
    """Apply direction from the transaction type, trusting the API where unsure."""
    if tx_type in _INCOME_TYPES:
        return abs(amount)
    if tx_type in _EXPENSE_TYPES:
        return -abs(amount)
    return amount  # e.g. TRANSFER, which can go either way


def _detect_account_by_isin(db: Session, positions: list[dict]) -> tuple[str, float, float] | None:
    """
    Identify an account by overlapping live ISINs against stored snapshots.

    Returns (account_name, score, runner_up_score), or None when there is not
    enough evidence to be confident.
    """
    live_isins = {
        (p.get("instrument") or {}).get("isin")
        for p in positions
        if (p.get("instrument") or {}).get("isin")
    }
    if not live_isins:
        return None

    scores: list[tuple[float, str]] = []
    for (name,) in db.query(InvestmentHolding.account_name).distinct().all():
        known = {
            r[0]
            for r in db.query(InvestmentHolding.isin)
            .filter(InvestmentHolding.account_name == name)
            .filter(InvestmentHolding.isin.isnot(None))
            .distinct()
            .all()
        }
        if known:
            scores.append((len(live_isins & known) / len(live_isins), name))

    if not scores:
        return None

    scores.sort(reverse=True)
    best_score, best_name = scores[0]
    runner_up = scores[1][0] if len(scores) > 1 else 0.0

    # Invest and Stocks ISA legitimately share popular tickers, so require both
    # a strong absolute match and a clear margin over the alternative.
    if best_score >= 0.5 and best_score >= runner_up * 1.3:
        return best_name, best_score, runner_up
    return None


def _account_label(
    db: Session, account: T212Account, summary: dict, positions: list[dict]
) -> str:
    """
    Work out which Trading 212 account a key pair belongs to.

    Trading 212 does not tell you which account a key is for when you create it,
    and /equity/account/summary does not report Invest vs Stocks ISA either. So
    the env var name is only the user's guess and cannot be trusted on its own.

    Evidence wins: when live positions match a stored snapshot convincingly we
    use that, and say so loudly if it contradicts the env var name. The hint is
    the fallback, not the authority.
    """
    detected = _detect_account_by_isin(db, positions)
    hinted = _ACCOUNT_LABELS.get(account.slug)

    if detected:
        name, score, runner_up = detected
        if hinted and hinted != name:
            logger.warning(
                "Trading 212 key is stored under %r (implying %r), but its live "
                "positions match %r far better (%.0f%% ISIN overlap vs %.0f%%). "
                "Using %r. Rename the env var to match, or the two accounts will "
                "be mislabelled.",
                account.slug, hinted, name, score * 100, runner_up * 100, name,
            )
        else:
            logger.info(
                "Matched Trading 212 key %r to %r (%.0f%% ISIN overlap, "
                "runner-up %.0f%%)",
                account.slug, name, score * 100, runner_up * 100,
            )
        return name

    if hinted:
        return hinted

    account_id = summary.get("id")
    return f"Trading 212 {account_id}" if account_id else "Trading 212"


def normalise_ticker(ticker: str) -> str:
    """
    Reduce a Trading 212 ticker to the bare symbol.

    Two formats appear in practice:
        AAPL_US_EQ  -> AAPL     (SYMBOL_EXCHANGE_TYPE)
        BTIPl_EQ    -> BTIP     (SYMBOL+exchange-letter_TYPE, London listings)

    The trailing lowercase letter on London lines is an exchange marker, not
    part of the symbol, and the PDF statements omit it. Only lowercase suffixes
    are stripped, so GOOGL and PLTR keep their uppercase trailing letters.
    """
    symbol = (ticker or "").split("_")[0]
    while len(symbol) > 1 and symbol[-1].islower():
        symbol = symbol[:-1]
    return symbol or ticker


def _instrument_name(db: Session, isin: str | None, ticker: str) -> str:
    """
    Prefer a name already used for this ISIN so a holding stays one series.

    Trading 212 still returns the legacy `FB` ticker for Meta (US30303M1027),
    while the statement parser recorded `META`. Same holding, and without this
    it would split into two lines on the history chart.
    """
    symbol = normalise_ticker(ticker)
    if isin:
        existing = (
            db.query(InvestmentHolding.instrument)
            .filter(InvestmentHolding.isin == isin)
            .filter(InvestmentHolding.instrument.isnot(None))
            .first()
        )
        if existing and existing[0] and existing[0] != symbol:
            logger.info(
                "Instrument %s (%s) already stored as %s; keeping the stored name "
                "so the series stays continuous.",
                symbol, isin, existing[0],
            )
            return existing[0]
    return symbol


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


def _account_key(summary: dict, account: T212Account) -> str:
    """
    Stable identifier for an account's API-sourced rows.

    Uses Trading 212's own numeric account id rather than the env var name. The
    env var is only the user's guess at which account a key belongs to, and
    renaming it must not change row identity -- doing so re-imports the entire
    history as "new" and duplicates it.
    """
    account_id = summary.get("id")
    return str(account_id) if account_id else account.slug


def _source_file(db: Session, account_key: str, label: str) -> SourceFile:
    """Get-or-create the synthetic SourceFile that owns this account's API rows."""
    filename = f"trading212:{account_key}"
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
    positions_total = 0.0
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
                instrument=_instrument_name(db, instrument.get("isin"), ticker),
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
        positions_total += value_gbp

    cash = _cash_value(summary, positions_total=positions_total)
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


def _cash_value(summary: dict, positions_total: float | None = None) -> float | None:
    """
    Cash as a residual: totalValue minus the position values actually stored.

    Deriving it against the stored positions -- rather than against
    investments.currentValue, or by summing availableToTrade + reservedForOrders
    + inPies -- is what makes sum(value_gbp) equal Trading 212's own totalValue
    exactly. account/summary and positions are two separate calls a second or so
    apart, so in a moving market their figures come from slightly different
    instants; absorbing that drift into cash keeps the portfolio total right,
    which is what net worth depends on.
    """
    total = summary.get("totalValue")
    if total is None:
        return None

    if positions_total is None:
        invested = (summary.get("investments") or {}).get("currentValue")
        if invested is None:
            return None
        positions_total = invested

    cash = round(total - positions_total, 2)

    # Sanity-check against what Trading 212 reports as cash. A large gap means
    # something other than price drift is going on and is worth surfacing.
    reported = summary.get("cash") or {}
    if reported:
        reported_total = sum(
            v for v in (
                reported.get("availableToTrade"),
                reported.get("reservedForOrders"),
                reported.get("inPies"),
            ) if isinstance(v, (int, float))
        )
        if reported_total and abs(cash - reported_total) > max(1.0, reported_total * 0.01):
            logger.warning(
                "Derived cash %.2f differs from reported cash %.2f by more than 1%%; "
                "storing the derived figure so the portfolio total reconciles.",
                cash, reported_total,
            )

    return cash if abs(cash) >= 0.01 else None


def _existing_external_ids(db: Session, source_file_id: int) -> set[str]:
    rows = (
        db.query(Transaction.external_id)
        .filter(Transaction.source_file_id == source_file_id)
        .filter(Transaction.external_id.isnot(None))
        .all()
    )
    return {r[0] for r in rows}


def _transaction_external_id(account_key: str, item: dict) -> str:
    """Stable per-record key. account_key is the Trading 212 account id."""
    reference = item.get("reference")
    if reference:
        return f"t212:{account_key}:{reference}"
    return (
        f"t212:{account_key}:{item.get('dateTime')}:{item.get('type')}"
        f":{item.get('amount')}"
    )


def _write_transactions(
    db: Session, source_file: SourceFile, account_name: str, account_key: str,
    items: list[dict],
) -> int:
    known = _existing_external_ids(db, source_file.id)
    written = 0

    for item in items:
        external_id = _transaction_external_id(account_key, item)
        if external_id in known:
            continue
        known.add(external_id)

        tx_type = (item.get("type") or "").upper()
        amount = item.get("amount")
        date_time = item.get("dateTime") or ""
        if amount is None or not date_time:
            continue

        if tx_type not in _TRANSACTION_CATEGORIES:
            logger.warning(
                "Unmapped Trading 212 transaction type %r (amount %s); keeping the "
                "API's sign and filing it as Investment Other.",
                tx_type, amount,
            )
        signed = _signed_amount(tx_type, float(amount))

        # References are opaque UUIDs, so they add noise rather than meaning.
        description = f"Trading 212 {tx_type.replace('_', ' ').title()}"

        db.add(
            Transaction(
                date=date_time[:10],
                description=description,
                amount=signed,
                category=_TRANSACTION_CATEGORIES.get(tx_type, "Investment Other"),
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


def _trade_external_id(account_key: str, fill: dict) -> str:
    """
    Keyed on the fill, not the order: one order can execute in several fills,
    and keying on the order id would collapse them into a single row.
    """
    return f"t212:{account_key}:fill:{fill.get('id')}"


def _existing_trade_ids(db: Session, source_file_id: int) -> set[str]:
    rows = (
        db.query(InvestmentTrade.external_id)
        .filter(InvestmentTrade.source_file_id == source_file_id)
        .filter(InvestmentTrade.external_id.isnot(None))
        .all()
    )
    return {r[0] for r in rows}


def _write_trades(
    db: Session, source_file: SourceFile, account_name: str, account_key: str,
    items: list[dict],
) -> int:
    """
    Persist executed fills from the order history.

    Only items carrying a `fill` are stored. A cancelled or rejected order still
    reports the quantity it would have traded, so writing those would invent
    purchases that never happened -- the same failure mode as counting a
    declined card payment as spending.
    """
    known = _existing_trade_ids(db, source_file.id)
    written = 0

    for item in items:
        order = item.get("order") or {}
        fill = item.get("fill")
        if not fill:
            continue  # never executed

        external_id = _trade_external_id(account_key, fill)
        if external_id in known:
            continue
        known.add(external_id)

        executed_at = fill.get("filledAt") or order.get("createdAt") or ""
        quantity = fill.get("quantity")
        wallet = fill.get("walletImpact") or {}
        value = wallet.get("netValue")
        if not executed_at or quantity is None or value is None:
            continue

        instrument = order.get("instrument") or {}
        isin = instrument.get("isin")
        raw_ticker = order.get("ticker") or instrument.get("ticker")

        # Quantity arrives signed (negative on a sell); direction is kept in
        # `side` so the magnitude reads the same for both.
        side = (order.get("side") or ("SELL" if quantity < 0 else "BUY")).upper()

        # Taxes arrive negative and in the wallet currency; store the magnitude.
        taxes = wallet.get("taxes") or []
        fees = sum(abs(t.get("quantity") or 0.0) for t in taxes) or None

        # netValue follows the wallet the trade settled in, which has not always
        # been GBP -- so only claim value_gbp when it genuinely is.
        currency = wallet.get("currency")
        value_gbp = float(value) if currency == "GBP" else None
        if currency and currency != "GBP":
            logger.debug(
                "Trade %s settled in %s; value_gbp left NULL rather than "
                "converting at an invented rate.", external_id, currency,
            )

        db.add(
            InvestmentTrade(
                account_name=account_name,
                instrument=_instrument_name(db, isin, raw_ticker or ""),
                instrument_name=instrument.get("name"),
                ticker=raw_ticker,
                isin=isin,
                side=side,
                quantity=abs(float(quantity)),
                price=fill.get("price"),
                instrument_currency=instrument.get("currency"),
                fx_rate=wallet.get("fxRate"),
                value=float(value),
                currency=currency,
                value_gbp=value_gbp,
                fees=fees,
                realised_pl=wallet.get("realisedProfitLoss"),
                executed_at=executed_at,
                status=order.get("status"),
                order_id=str(order.get("id")) if order.get("id") is not None else None,
                external_id=external_id,
                source_file_id=source_file.id,
                raw_row=json.dumps(item),
            )
        )
        written += 1

    return written


def sync_account(
    db: Session,
    account: T212Account,
    include_history: bool = True,
    max_pages: int = 20,
    full_history: bool = False,
) -> dict:
    """
    Sync one Trading 212 account. Raises on failure; sync_all captures those.

    `full_history` turns off the stop-at-first-known short-circuit. Routine
    syncs want that short-circuit -- it keeps a re-sync to a single page -- but
    it also means a backfill truncated by `max_pages` can never be deepened,
    because the second run stops at the newest record the first one stored.
    Writes stay deduplicated by external id, so a full pass costs API calls and
    nothing else.
    """
    report_date = date.today().isoformat()

    with Trading212Client(account, env=settings.t212_env) as client:
        summary = client.account_summary()
        positions = client.positions()
        label = _account_label(db, account, summary, positions)
        account_key = _account_key(summary, account)

        transactions: list[dict] = []
        dividends: list[dict] = []
        orders: list[dict] = []
        if include_history:
            source_file_peek = (
                db.query(SourceFile)
                .filter(SourceFile.filename == f"trading212:{account_key}")
                .one_or_none()
            )
            known = (
                _existing_external_ids(db, source_file_peek.id)
                if source_file_peek
                else set()
            )
            known_fills = (
                _existing_trade_ids(db, source_file_peek.id)
                if source_file_peek
                else set()
            )
            # Newest-first, so stopping at the first already-stored record turns
            # a re-sync into a single page.
            transactions = client.history_transactions(
                max_pages=max_pages,
                stop_when=None if full_history
                else (lambda i: _transaction_external_id(account_key, i) in known),
            )
            dividends = client.history_dividends(max_pages=max_pages)
            # Unexecuted orders carry no fill and are never stored, so they must
            # not stop the scan either -- otherwise a cancelled order sitting at
            # the top of the history would end every future re-sync at page one.
            orders = client.history_orders(
                max_pages=max_pages,
                stop_when=None if full_history else (
                    lambda i: bool(i.get("fill"))
                    and _trade_external_id(account_key, i["fill"]) in known_fills
                ),
            )

    source_file = _source_file(db, account_key, label)
    holdings_written = _write_holdings(
        db, source_file, label, report_date, positions, summary
    )
    transactions_written = _write_transactions(
        db, source_file, label, account_key, transactions
    )
    dividends_written = _write_dividends(db, source_file, label, dividends)
    trades_written = _write_trades(db, source_file, label, account_key, orders)

    source_file.row_count = holdings_written
    db.commit()

    return {
        "slug": account.slug,
        "account_name": label,
        "report_date": report_date,
        "holdings": holdings_written,
        "transactions": transactions_written,
        "dividends": dividends_written,
        "trades": trades_written,
        "total_value_gbp": summary.get("totalValue"),
        "cash_gbp": _cash_value(summary),
        "error": None,
    }


def sync_all(
    db: Session,
    include_history: bool = True,
    max_pages: int = 20,
    full_history: bool = False,
) -> dict:
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
                db, account, include_history=include_history, max_pages=max_pages,
                full_history=full_history,
            )
            run.status = "ok"
            run.items_written = (
                result["holdings"] + result["transactions"] + result["dividends"]
                + result["trades"]
            )
            run.message = (
                f"{result['holdings']} holdings, {result['transactions']} transactions, "
                f"{result['dividends']} dividends, {result['trades']} trades"
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
        "trades": 0,
        "total_value_gbp": None,
        "cash_gbp": None,
        "error": message,
    }
