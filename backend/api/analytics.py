import statistics
from collections import defaultdict
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, case
from sqlalchemy.orm import Session

from db.database import get_db
from db.models import Transaction

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


def _month_filter(q, start: Optional[str], end: Optional[str]):
    if start:
        q = q.filter(Transaction.date >= start)
    if end:
        q = q.filter(Transaction.date <= end)
    return q


# ---------------------------------------------------------------------------
# Tier 1 — Foundational Views
# ---------------------------------------------------------------------------

@router.get("/spending-by-category")
def spending_by_category(
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """Returns total spending per category per month (expenses only, amount < 0)."""
    month_expr = func.substr(Transaction.date, 1, 7)
    q = db.query(
        month_expr.label("month"),
        Transaction.category,
        func.sum(Transaction.amount).label("total"),
        func.count(Transaction.id).label("count"),
    ).filter(Transaction.amount < 0)
    q = _month_filter(q, start, end)
    rows = q.group_by(month_expr, Transaction.category).order_by(month_expr).all()
    return [
        {"month": r.month, "category": r.category or "Uncategorised", "total": abs(r.total), "count": r.count}
        for r in rows
    ]


@router.get("/income-vs-expenses")
def income_vs_expenses(
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """Returns monthly income, expenses, and savings rate."""
    month_expr = func.substr(Transaction.date, 1, 7)
    income_expr = func.sum(case((Transaction.amount > 0, Transaction.amount), else_=0))
    expense_expr = func.sum(case((Transaction.amount < 0, Transaction.amount), else_=0))
    q = db.query(
        month_expr.label("month"),
        income_expr.label("income"),
        expense_expr.label("expenses"),
    )
    q = _month_filter(q, start, end)
    rows = q.group_by(month_expr).order_by(month_expr).all()
    result = []
    for r in rows:
        income = r.income or 0.0
        expenses = abs(r.expenses or 0.0)
        savings = income - expenses
        savings_rate = round((savings / income * 100), 1) if income > 0 else 0.0
        result.append({
            "month": r.month,
            "income": round(income, 2),
            "expenses": round(expenses, 2),
            "savings": round(savings, 2),
            "savings_rate": savings_rate,
        })
    return result


@router.get("/cashflow-waterfall")
def cashflow_waterfall(
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """Monthly waterfall: income sources and expense categories per month."""
    month_expr = func.substr(Transaction.date, 1, 7)
    q = db.query(
        month_expr.label("month"),
        Transaction.category,
        func.sum(Transaction.amount).label("total"),
    )
    q = _month_filter(q, start, end)
    rows = q.group_by(month_expr, Transaction.category).order_by(month_expr).all()
    months: dict = {}
    for r in rows:
        m = r.month
        if m not in months:
            months[m] = {"month": m, "income": 0.0, "expenses": [], "net": 0.0}
        total = r.total or 0.0
        if total >= 0:
            months[m]["income"] += total
        else:
            months[m]["expenses"].append({"category": r.category or "Uncategorised", "amount": abs(total)})
        months[m]["net"] += total
    result = []
    running_balance = 0.0
    for m in sorted(months.keys()):
        entry = months[m]
        start_bal = running_balance
        running_balance += entry["net"]
        result.append({
            "month": m,
            "start_balance": round(start_bal, 2),
            "income": round(entry["income"], 2),
            "expenses": [{"category": e["category"], "amount": round(e["amount"], 2)} for e in entry["expenses"]],
            "end_balance": round(running_balance, 2),
            "net": round(entry["net"], 2),
        })
    return result


# ---------------------------------------------------------------------------
# Tier 2 — Trend & Pattern Analysis
# ---------------------------------------------------------------------------

@router.get("/category-trends")
def category_trends(
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """Monthly spending per category with 3-month rolling average and anomaly detection."""
    month_expr = func.substr(Transaction.date, 1, 7)
    q = db.query(
        month_expr.label("month"),
        Transaction.category,
        func.sum(Transaction.amount).label("total"),
    ).filter(Transaction.amount < 0)
    q = _month_filter(q, start, end)
    rows = q.group_by(month_expr, Transaction.category).order_by(month_expr).all()

    by_cat: dict = defaultdict(dict)
    for r in rows:
        by_cat[r.category or "Uncategorised"][r.month] = abs(r.total or 0)

    result = []
    for cat, month_data in by_cat.items():
        months_list = []
        sorted_months = sorted(month_data.keys())
        for i, m in enumerate(sorted_months):
            total = month_data[m]
            window = [month_data[sorted_months[j]] for j in range(max(0, i - 2), i + 1)]
            rolling_avg = round(sum(window) / len(window), 2)
            all_prior = [month_data[sorted_months[j]] for j in range(i)]
            if len(all_prior) >= 2:
                avg = sum(all_prior) / len(all_prior)
                std = statistics.stdev(all_prior)
                is_anomaly = bool(std > 0 and total > avg + 1.5 * std)
            else:
                is_anomaly = False
            months_list.append({
                "month": m,
                "total": round(total, 2),
                "rolling_avg_3m": rolling_avg,
                "is_anomaly": is_anomaly,
            })
        result.append({
            "category": cat,
            "months": months_list,
            "total_spend": round(sum(month_data.values()), 2),
        })
    result.sort(key=lambda x: x["total_spend"], reverse=True)
    return result


@router.get("/recurring-expenses")
def recurring_expenses(db: Session = Depends(get_db)):
    """Detect charges that recur across multiple months."""
    rows = db.query(
        Transaction.description,
        Transaction.category,
        Transaction.amount,
        func.substr(Transaction.date, 1, 7).label("month"),
    ).filter(Transaction.amount < 0).all()

    groups: dict = defaultdict(list)
    for r in rows:
        key = (r.description.strip()[:60].lower(), r.category or "Uncategorised")
        groups[key].append({"month": r.month, "amount": abs(r.amount)})

    result = []
    for (desc, cat), occurrences in groups.items():
        unique_months = list(set(o["month"] for o in occurrences))
        if len(unique_months) < 2:
            continue
        amounts = [o["amount"] for o in occurrences]
        avg_monthly = sum(amounts) / len(unique_months)
        if len(amounts) > 1:
            std = statistics.stdev(amounts)
            if avg_monthly > 0 and std / avg_monthly > 0.5:
                continue  # too variable to be a true recurring charge
        sorted_occ = sorted(occurrences, key=lambda x: x["month"])
        is_increasing = len(sorted_occ) > 1 and sorted_occ[-1]["amount"] > sorted_occ[0]["amount"]
        result.append({
            "description": desc,
            "category": cat,
            "monthly_cost": round(avg_monthly, 2),
            "annual_cost": round(avg_monthly * 12, 2),
            "occurrences": len(unique_months),
            "last_seen": max(unique_months),
            "is_increasing": is_increasing,
        })
    result.sort(key=lambda x: x["monthly_cost"], reverse=True)
    return result


@router.get("/spending-velocity")
def spending_velocity(
    year: int = Query(...),
    month: int = Query(...),
    db: Session = Depends(get_db),
):
    """Daily cumulative spending for a month vs the prior month."""
    import calendar

    def get_daily(y: int, m: int) -> dict:
        month_str = f"{y:04d}-{m:02d}"
        day_expr = func.substr(Transaction.date, 9, 2)
        rows = db.query(
            day_expr.label("day"),
            func.sum(Transaction.amount).label("total"),
        ).filter(
            Transaction.amount < 0,
            Transaction.date.like(f"{month_str}%"),
        ).group_by(day_expr).order_by(day_expr).all()
        return {int(r.day): abs(r.total or 0) for r in rows}

    days_in_month = calendar.monthrange(year, month)[1]
    current = get_daily(year, month)
    prior_year, prior_month = (year - 1, 12) if month == 1 else (year, month - 1)
    prior = get_daily(prior_year, prior_month)

    result = []
    cum, cum_prior = 0.0, 0.0
    for day in range(1, days_in_month + 1):
        cum += current.get(day, 0)
        cum_prior += prior.get(day, 0)
        result.append({
            "day": day,
            "daily": round(current.get(day, 0), 2),
            "cumulative": round(cum, 2),
            "prior_cumulative": round(cum_prior, 2),
        })
    return result


# ---------------------------------------------------------------------------
# Tier 3 — Investment & Net Worth
# ---------------------------------------------------------------------------

@router.get("/net-worth")
def net_worth(
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """Monthly cumulative net worth broken down by account type."""
    month_expr = func.substr(Transaction.date, 1, 7)
    q = db.query(
        month_expr.label("month"),
        Transaction.account_type,
        func.sum(Transaction.amount).label("net"),
    )
    q = _month_filter(q, start, end)
    rows = q.group_by(month_expr, Transaction.account_type).order_by(month_expr).all()

    months_net: dict = defaultdict(lambda: defaultdict(float))
    all_months = sorted(set(r.month for r in rows))
    for r in rows:
        months_net[r.month][r.account_type or "bank"] += r.net or 0

    running: dict = defaultdict(float)
    result = []
    for m in all_months:
        for atype, val in months_net[m].items():
            running[atype] += val
        result.append({
            "month": m,
            "bank": round(running.get("bank", 0), 2),
            "investment": round(running.get("investment", 0), 2),
            "credit_card": round(running.get("credit_card", 0), 2),
            "total": round(sum(running.values()), 2),
        })
    return result


# ---------------------------------------------------------------------------
# Tier 4 — Predictive & Actionable Insights
# ---------------------------------------------------------------------------

@router.get("/anomalies")
def anomalies(
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """Flag statistically unusual expense transactions (z-score > 2)."""
    all_expenses = db.query(Transaction).filter(Transaction.amount < 0).all()
    cat_amounts: dict = defaultdict(list)
    for t in all_expenses:
        cat_amounts[t.category or "Uncategorised"].append(abs(t.amount))

    cat_stats = {}
    for cat, amounts in cat_amounts.items():
        if len(amounts) >= 3:
            cat_stats[cat] = {
                "mean": statistics.mean(amounts),
                "std": statistics.stdev(amounts),
            }

    q = db.query(Transaction)
    q = _month_filter(q, start, end)
    txns = q.all()

    result = []
    for t in txns:
        if t.amount >= 0:
            continue
        cat = t.category or "Uncategorised"
        amount = abs(t.amount)
        if cat in cat_stats and cat_stats[cat]["std"] > 0:
            z = (amount - cat_stats[cat]["mean"]) / cat_stats[cat]["std"]
            if z > 2.0:
                result.append({
                    "id": t.id,
                    "date": t.date,
                    "description": t.description,
                    "amount": t.amount,
                    "category": cat,
                    "reason": f"£{amount:.0f} vs typical £{cat_stats[cat]['mean']:.0f} ({z:.1f}σ above average)",
                    "severity": "high" if z > 3 else "medium",
                })
    result.sort(key=lambda x: x["date"], reverse=True)
    return result


@router.get("/projection")
def projection(
    months: int = Query(6),
    db: Session = Depends(get_db),
):
    """Project future cash flow from the last 3 months average."""
    from dateutil.relativedelta import relativedelta
    from datetime import date

    month_expr = func.substr(Transaction.date, 1, 7)
    income_expr = func.sum(case((Transaction.amount > 0, Transaction.amount), else_=0))
    expense_expr = func.sum(case((Transaction.amount < 0, Transaction.amount), else_=0))
    rows = db.query(
        month_expr.label("month"),
        income_expr.label("income"),
        expense_expr.label("expenses"),
    ).group_by(month_expr).order_by(month_expr.desc()).limit(3).all()

    if not rows:
        return []

    avg_income = sum(r.income or 0 for r in rows) / len(rows)
    avg_expenses = sum(abs(r.expenses or 0) for r in rows) / len(rows)
    avg_net = avg_income - avg_expenses
    current_balance = float(db.query(func.sum(Transaction.amount)).scalar() or 0)

    today = date.today()
    result = []
    running = current_balance
    for i in range(1, months + 1):
        future = (today + relativedelta(months=i)).strftime("%Y-%m")
        running += avg_net
        result.append({
            "month": future,
            "projected_income": round(avg_income, 2),
            "projected_expenses": round(avg_expenses, 2),
            "projected_balance": round(running, 2),
            "projected_savings": round(avg_net, 2),
        })
    return result


@router.get("/monthly-summary")
def monthly_summary(
    month: str = Query(...),
    db: Session = Depends(get_db),
):
    """Generate an LLM-written natural language summary for a given month."""
    from ingestion.llm_processor import _call

    month_start = f"{month}-01"
    month_end = f"{month}-31"

    income_expr = func.sum(case((Transaction.amount > 0, Transaction.amount), else_=0))
    expense_expr = func.sum(case((Transaction.amount < 0, Transaction.amount), else_=0))
    ie = db.query(income_expr.label("income"), expense_expr.label("expenses")).filter(
        Transaction.date >= month_start, Transaction.date <= month_end,
    ).first()

    if not ie or not (ie.income or ie.expenses):
        return {"month": month, "summary": "No transaction data found for this month."}

    income = ie.income or 0
    expenses = abs(ie.expenses or 0)
    savings = income - expenses
    savings_rate = round(savings / income * 100, 1) if income > 0 else 0

    top_cats = db.query(
        Transaction.category,
        func.sum(Transaction.amount).label("total"),
    ).filter(
        Transaction.amount < 0,
        Transaction.date >= month_start,
        Transaction.date <= month_end,
    ).group_by(Transaction.category).order_by(func.sum(Transaction.amount)).limit(5).all()

    anom_count = len(anomalies(start=month_start, end=month_end, db=db))
    top_text = ", ".join(f"{r.category or 'Uncategorised'}: £{abs(r.total or 0):.0f}" for r in top_cats)

    prompt = f"""You are a personal finance assistant for a UK user. Write a concise 2-3 sentence monthly summary for {month}.

Key figures:
- Income: £{income:.0f}
- Expenses: £{expenses:.0f}
- Net savings: £{savings:.0f} ({savings_rate}% savings rate)
- Top spending categories: {top_text}
- Unusual transactions flagged: {anom_count}

Be specific with numbers. Note if the savings rate is strong (>20%) or concerning (<5%). Highlight what drove spending. Keep it natural and direct."""

    summary_text = _call(prompt, max_tokens=250)
    return {"month": month, "summary": summary_text}


# ---------------------------------------------------------------------------
# Tier 5 — UX Details
# ---------------------------------------------------------------------------

@router.get("/annual-summary")
def annual_summary(
    year: int = Query(...),
    db: Session = Depends(get_db),
):
    """Full-year overview statistics."""
    month_expr = func.substr(Transaction.date, 1, 7)
    income_expr = func.sum(case((Transaction.amount > 0, Transaction.amount), else_=0))
    expense_expr = func.sum(case((Transaction.amount < 0, Transaction.amount), else_=0))
    rows = db.query(
        month_expr.label("month"),
        income_expr.label("income"),
        expense_expr.label("expenses"),
    ).filter(Transaction.date.like(f"{year}%")).group_by(month_expr).all()

    if not rows:
        return {
            "year": year, "total_income": 0, "total_expenses": 0, "savings": 0,
            "savings_rate": 0, "months_of_data": 0, "top_categories": [],
            "highest_spending_month": None, "best_savings_month": None,
        }

    total_income = sum(r.income or 0 for r in rows)
    total_expenses = sum(abs(r.expenses or 0) for r in rows)
    savings = total_income - total_expenses
    savings_rate = round(savings / total_income * 100, 1) if total_income > 0 else 0

    top_cats = db.query(
        Transaction.category,
        func.sum(Transaction.amount).label("total"),
        func.count(Transaction.id).label("count"),
    ).filter(
        Transaction.amount < 0,
        Transaction.date.like(f"{year}%"),
    ).group_by(Transaction.category).order_by(func.sum(Transaction.amount)).limit(8).all()

    biggest_spend = max(rows, key=lambda r: abs(r.expenses or 0), default=None)
    best_month = max(rows, key=lambda r: (r.income or 0) - abs(r.expenses or 0), default=None)

    return {
        "year": year,
        "total_income": round(total_income, 2),
        "total_expenses": round(total_expenses, 2),
        "savings": round(savings, 2),
        "savings_rate": savings_rate,
        "months_of_data": len(rows),
        "top_categories": [
            {"category": r.category or "Uncategorised", "total": round(abs(r.total or 0), 2), "count": r.count}
            for r in top_cats
        ],
        "highest_spending_month": biggest_spend.month if biggest_spend else None,
        "best_savings_month": best_month.month if best_month else None,
    }


@router.get("/data-quality")
def data_quality(db: Session = Depends(get_db)):
    """Categorisation confidence and coverage metrics."""
    total = db.query(func.count(Transaction.id)).scalar() or 0
    uncategorised = db.query(func.count(Transaction.id)).filter(Transaction.category.is_(None)).scalar() or 0
    user_edited = db.query(func.count(Transaction.id)).filter(Transaction.is_user_edited == True).scalar() or 0
    low_confidence = db.query(func.count(Transaction.id)).filter(
        Transaction.confidence.isnot(None),
        Transaction.confidence < 0.7,
    ).scalar() or 0
    avg_conf = db.query(func.avg(Transaction.confidence)).filter(Transaction.confidence.isnot(None)).scalar()
    return {
        "total": total,
        "uncategorised": uncategorised,
        "low_confidence": low_confidence,
        "user_edited": user_edited,
        "avg_confidence": round(float(avg_conf), 3) if avg_conf else None,
        "categorised_pct": round((total - uncategorised) / total * 100, 1) if total else 0,
    }


@router.get("/month-comparison")
def month_comparison(
    month1: str = Query(...),
    month2: str = Query(...),
    db: Session = Depends(get_db),
):
    """Side-by-side category spending comparison for two months."""
    def get_cat_totals(m: str) -> dict:
        rows = db.query(
            Transaction.category,
            func.sum(Transaction.amount).label("total"),
        ).filter(
            Transaction.amount < 0,
            Transaction.date.like(f"{m}%"),
        ).group_by(Transaction.category).all()
        return {r.category or "Uncategorised": abs(r.total or 0) for r in rows}

    m1 = get_cat_totals(month1)
    m2 = get_cat_totals(month2)
    all_cats = sorted(set(list(m1.keys()) + list(m2.keys())))
    result = []
    for cat in all_cats:
        v1 = m1.get(cat, 0)
        v2 = m2.get(cat, 0)
        change_pct = round((v2 - v1) / v1 * 100, 1) if v1 > 0 else None
        result.append({
            "category": cat,
            "month1_total": round(v1, 2),
            "month2_total": round(v2, 2),
            "change_pct": change_pct,
            "change_abs": round(v2 - v1, 2),
        })
    result.sort(key=lambda x: x["month2_total"], reverse=True)
    return result
