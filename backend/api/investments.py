"""
Investment portfolio analytics endpoints.

Reads from the investment_holdings table populated by the investment statement parser.
"""
from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from db.database import get_db
from db.models import InvestmentHolding

router = APIRouter(prefix="/api/investments", tags=["investments"])


def _return_pct(return_gbp, cost_gbp, value_gbp) -> float | None:
    """
    Calculate return % from cost basis.
    If cost_gbp is missing/zero, derive cost from value - return (since value = cost + return).
    """
    if return_gbp is None:
        return None
    effective_cost = cost_gbp if cost_gbp else (value_gbp - return_gbp if value_gbp else None)
    if not effective_cost:
        return None
    return round(return_gbp / effective_cost * 100, 2)


def _latest_report_date(db: Session) -> str | None:
    """Return the most recent report_date in the holdings table."""
    row = db.query(func.max(InvestmentHolding.report_date)).scalar()
    return row


# ---------------------------------------------------------------------------
# Summary — headline KPIs
# ---------------------------------------------------------------------------

@router.get("/summary")
def investment_summary(db: Session = Depends(get_db)):
    """
    Total portfolio value, total cost basis, unrealised return and return %.
    Also broken down by account.
    """
    latest_date = _latest_report_date(db)
    if not latest_date:
        return {
            "report_date": None,
            "total_value_gbp": 0,
            "total_cost_gbp": 0,
            "total_return_gbp": 0,
            "return_pct": 0,
            "accounts": [],
        }

    holdings = (
        db.query(InvestmentHolding)
        .filter(InvestmentHolding.report_date == latest_date)
        .all()
    )

    total_value = sum(h.value_gbp for h in holdings)
    total_return = sum(h.return_gbp for h in holdings if h.return_gbp is not None)
    total_cost = sum(h.cost_gbp for h in holdings if h.cost_gbp is not None)
    # Fall back: derive cost from value - return when cost_gbp not stored
    if not total_cost and total_value and total_return:
        total_cost = total_value - total_return
    return_pct = round((total_return / total_cost * 100), 2) if total_cost else 0

    # Per-account breakdown
    accounts: dict[str, dict] = {}
    for h in holdings:
        acc = h.account_name
        if acc not in accounts:
            accounts[acc] = {"account_name": acc, "value_gbp": 0, "cost_gbp": 0, "return_gbp": 0}
        accounts[acc]["value_gbp"] += h.value_gbp
        if h.cost_gbp is not None:
            accounts[acc]["cost_gbp"] += h.cost_gbp
        if h.return_gbp is not None:
            accounts[acc]["return_gbp"] += h.return_gbp

    for acc in accounts.values():
        cost = acc["cost_gbp"] or (acc["value_gbp"] - acc["return_gbp"])
        ret = acc["return_gbp"]
        acc["return_pct"] = round(ret / cost * 100, 2) if cost else 0
        acc["value_gbp"] = round(acc["value_gbp"], 2)
        acc["cost_gbp"] = round(acc["cost_gbp"], 2)
        acc["return_gbp"] = round(acc["return_gbp"], 2)

    return {
        "report_date": latest_date,
        "total_value_gbp": round(total_value, 2),
        "total_cost_gbp": round(total_cost, 2),
        "total_return_gbp": round(total_return, 2),
        "return_pct": return_pct,
        "accounts": list(accounts.values()),
    }


# ---------------------------------------------------------------------------
# Holdings — full position list from the latest snapshot
# ---------------------------------------------------------------------------

@router.get("/holdings")
def investment_holdings(db: Session = Depends(get_db)):
    """All positions from the latest report date, sorted by value descending."""
    latest_date = _latest_report_date(db)
    if not latest_date:
        return []

    holdings = (
        db.query(InvestmentHolding)
        .filter(InvestmentHolding.report_date == latest_date)
        .order_by(InvestmentHolding.value_gbp.desc())
        .all()
    )

    return [
        {
            "id": h.id,
            "account_name": h.account_name,
            "instrument": h.instrument,
            "isin": h.isin,
            "currency": h.currency,
            "quantity": h.quantity,
            "avg_price": h.avg_price,
            "current_price": h.current_price,
            "fx_rate": h.fx_rate,
            "cost_gbp": h.cost_gbp,
            "value_gbp": h.value_gbp,
            "return_gbp": h.return_gbp,
            "return_pct": _return_pct(h.return_gbp, h.cost_gbp, h.value_gbp),
        }
        for h in holdings
    ]


# ---------------------------------------------------------------------------
# Performance — return by instrument (bar chart data)
# ---------------------------------------------------------------------------

@router.get("/performance")
def investment_performance(db: Session = Depends(get_db)):
    """
    Unrealised return per instrument from the latest snapshot,
    sorted by absolute return descending.
    """
    latest_date = _latest_report_date(db)
    if not latest_date:
        return []

    holdings = (
        db.query(InvestmentHolding)
        .filter(InvestmentHolding.report_date == latest_date)
        .all()
    )

    rows = [
        {
            "instrument": h.instrument,
            "account_name": h.account_name,
            "return_gbp": round(h.return_gbp, 2) if h.return_gbp is not None else 0,
            "return_pct": _return_pct(h.return_gbp, h.cost_gbp, h.value_gbp) or 0,
            "value_gbp": round(h.value_gbp, 2),
        }
        for h in holdings
        if h.return_gbp is not None
    ]

    rows.sort(key=lambda r: abs(r["return_gbp"]), reverse=True)
    return rows


# ---------------------------------------------------------------------------
# Allocation — portfolio weight per position (treemap / pie data)
# ---------------------------------------------------------------------------

@router.get("/allocation")
def investment_allocation(db: Session = Depends(get_db)):
    """
    Each position as a % of total portfolio value — for pie/treemap charts.
    Returns per-instrument AND per-account breakdowns.
    """
    latest_date = _latest_report_date(db)
    if not latest_date:
        return {"by_instrument": [], "by_account": []}

    holdings = (
        db.query(InvestmentHolding)
        .filter(InvestmentHolding.report_date == latest_date)
        .all()
    )

    total = sum(h.value_gbp for h in holdings) or 1

    by_instrument = sorted(
        [
            {
                "instrument": h.instrument,
                "account_name": h.account_name,
                "value_gbp": round(h.value_gbp, 2),
                "weight_pct": round(h.value_gbp / total * 100, 2),
            }
            for h in holdings
        ],
        key=lambda r: r["value_gbp"],
        reverse=True,
    )

    # Aggregate by account
    acc_totals: dict[str, float] = {}
    for h in holdings:
        acc_totals[h.account_name] = acc_totals.get(h.account_name, 0) + h.value_gbp

    by_account = sorted(
        [
            {
                "account_name": acc,
                "value_gbp": round(val, 2),
                "weight_pct": round(val / total * 100, 2),
            }
            for acc, val in acc_totals.items()
        ],
        key=lambda r: r["value_gbp"],
        reverse=True,
    )

    return {"by_instrument": by_instrument, "by_account": by_account}


# ---------------------------------------------------------------------------
# History — total portfolio value over time (as more statements are ingested)
# ---------------------------------------------------------------------------

@router.get("/history")
def investment_history(db: Session = Depends(get_db)):
    """
    Portfolio total value per snapshot date.
    As users ingest more statements over time, this builds into a history chart.
    """
    rows = (
        db.query(
            InvestmentHolding.report_date,
            func.sum(InvestmentHolding.value_gbp).label("total_value"),
            func.sum(InvestmentHolding.cost_gbp).label("total_cost"),
            func.sum(InvestmentHolding.return_gbp).label("total_return"),
        )
        .group_by(InvestmentHolding.report_date)
        .order_by(InvestmentHolding.report_date)
        .all()
    )

    return [
        {
            "date": r.report_date,
            "total_value": round(r.total_value or 0, 2),
            "total_cost": round(r.total_cost or 0, 2),
            "total_return": round(r.total_return or 0, 2),
        }
        for r in rows
    ]
