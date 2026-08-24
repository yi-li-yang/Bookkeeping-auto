"""
Read-only view of the portfolio, for the /portfolio commands to call.

Not a tool to run by hand. It exists so the schema's sharp edges are handled in
one place instead of being rediscovered in hand-written SQL every session:

* `report_date` is a per-account snapshot date. Taking a global max across
  accounts -- which /api/investments/summary does -- silently drops an account
  whose last sync failed, so every account is resolved to its own latest date.
* `investment_holdings` carries a synthetic `CASH` row with NULL cost and
  return. It belongs in the total but not in any return calculation.
* Dates are ISO strings, not DATE columns.
* A trade's `value` is denominated in the wallet it settled in. `value_gbp` is
  NULL where that was not GBP (the Invest account settled in USD until
  2026-05-04), so summing `value` across currencies is wrong.

The database is opened read-only at the driver level, so an analysis session
cannot alter the books even if it tries.
"""
import argparse
import json
import re
import sqlite3
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import settings  # noqa: E402


def connect() -> sqlite3.Connection:
    if not settings.db_path.exists():
        raise SystemExit(f"No database at {settings.db_path}. Run a sync first.")
    conn = sqlite3.connect(f"file:{settings.db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def rows(conn, sql, params=()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _has_trades(conn) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='investment_trades'"
    ).fetchone())


def freshness(conn) -> dict:
    """Snapshot age, per account and overall. Always reported, never inferred."""
    per = rows(conn, """
        SELECT account_name, MAX(report_date) AS report_date
          FROM investment_holdings GROUP BY account_name ORDER BY account_name
    """)
    today = date.today()
    for r in per:
        try:
            r["age_days"] = (today - date.fromisoformat(r["report_date"])).days
        except (TypeError, ValueError):
            r["age_days"] = None
    ages = [r["age_days"] for r in per if r["age_days"] is not None]
    return {
        "as_of": today.isoformat(),
        "accounts": per,
        "age_days": max(ages) if ages else None,
        "stale": bool(ages and max(ages) > 1),
        "accounts_disagree_on_snapshot_date": len({r["report_date"] for r in per}) > 1,
    }


# -- subcommands -----------------------------------------------------------

def cmd_snapshot(conn, args) -> dict:
    """Latest holdings per account, each at its own most recent snapshot date."""
    where, params = "", []
    if args.account:
        where = "AND h.account_name LIKE ?"
        params = [f"%{args.account}%"]

    holdings = rows(conn, f"""
        SELECT h.account_name, h.report_date, h.instrument, h.isin, h.currency,
               h.quantity, h.avg_price, h.current_price, h.fx_rate,
               h.cost_gbp, h.value_gbp, h.return_gbp
          FROM investment_holdings h
          JOIN (SELECT account_name, MAX(report_date) AS d
                  FROM investment_holdings GROUP BY account_name) latest
            ON latest.account_name = h.account_name AND latest.d = h.report_date
         WHERE 1=1 {where}
         ORDER BY h.account_name, h.value_gbp DESC
    """, params)

    total = sum(h["value_gbp"] or 0 for h in holdings)
    for h in holdings:
        h["weight_pct"] = round((h["value_gbp"] or 0) / total * 100, 2) if total else None
        cost = h["cost_gbp"]
        if cost is None and h["value_gbp"] is not None and h["return_gbp"] is not None:
            cost = h["value_gbp"] - h["return_gbp"]
        h["return_pct"] = (
            round(h["return_gbp"] / cost * 100, 2)
            if h["return_gbp"] is not None and cost else None
        )

    by_account: dict[str, dict] = {}
    for h in holdings:
        a = by_account.setdefault(h["account_name"], {
            "value_gbp": 0.0, "cost_gbp": 0.0, "return_gbp": 0.0,
            "cash_gbp": 0.0, "positions": 0,
        })
        a["value_gbp"] += h["value_gbp"] or 0
        a["cost_gbp"] += h["cost_gbp"] or 0
        a["return_gbp"] += h["return_gbp"] or 0
        if h["instrument"] == "CASH":
            a["cash_gbp"] += h["value_gbp"] or 0
        else:
            a["positions"] += 1
    for a in by_account.values():
        for k in ("value_gbp", "cost_gbp", "return_gbp", "cash_gbp"):
            a[k] = round(a[k], 2)
        a["return_pct"] = (
            round(a["return_gbp"] / a["cost_gbp"] * 100, 2) if a["cost_gbp"] else None
        )

    invested = sum(h["value_gbp"] or 0 for h in holdings if h["instrument"] != "CASH")
    cash = sum(h["value_gbp"] or 0 for h in holdings if h["instrument"] == "CASH")
    return {
        "freshness": freshness(conn),
        "totals": {
            "value_gbp": round(total, 2),
            "invested_gbp": round(invested, 2),
            "cash_gbp": round(cash, 2),
            "cost_gbp": round(sum(h["cost_gbp"] or 0 for h in holdings), 2),
            "return_gbp": round(sum(h["return_gbp"] or 0 for h in holdings), 2),
        },
        "by_account": by_account,
        "holdings": holdings,
        "note": "CASH is a synthetic row with NULL cost/return; exclude it from return maths.",
    }


def _trade_filters(args) -> tuple[str, list]:
    where, params = [], []
    if getattr(args, "isin", None):
        where.append("(isin = ? OR instrument = ? OR ticker LIKE ?)")
        params += [args.isin, args.isin.upper(), f"{args.isin}%"]
    if getattr(args, "since", None):
        where.append("executed_at >= ?")
        params.append(args.since)
    if getattr(args, "account", None):
        where.append("account_name LIKE ?")
        params.append(f"%{args.account}%")
    return (("WHERE " + " AND ".join(where)) if where else ""), params


def cmd_trades(conn, args) -> dict:
    if not _has_trades(conn):
        return {"freshness": freshness(conn), "trades": [],
                "note": "No investment_trades table yet - run a sync."}
    clause, params = _trade_filters(args)

    trades = rows(conn, f"""
        SELECT account_name, executed_at, instrument, instrument_name, ticker, isin,
               side, quantity, price, instrument_currency, fx_rate,
               value, currency, value_gbp, fees, realised_pl, order_id
          FROM investment_trades {clause}
         ORDER BY executed_at DESC
         LIMIT ?
    """, params + [args.limit])

    rp_clause = f"{clause} AND realised_pl IS NOT NULL" if clause \
        else "WHERE realised_pl IS NOT NULL"
    realised = rows(conn, f"""
        SELECT currency, ROUND(SUM(realised_pl), 2) AS realised_pl, COUNT(*) AS sells
          FROM investment_trades {rp_clause}
         GROUP BY currency
    """, params)

    return {
        "freshness": freshness(conn),
        "count": len(trades),
        "realised_pl_by_currency": realised,
        "trades": trades,
        "note": ("value is in `currency` (the wallet the trade settled in); value_gbp "
                 "is NULL where that was not GBP. Corporate actions such as stock "
                 "splits create shares with no trade, so quantities need not "
                 "reconcile against holdings across a split."),
    }


def cmd_income(conn, args) -> dict:
    since = args.since or "0000-00-00"
    dividends = rows(conn, """
        SELECT account_name, instrument, isin, ticker, dividend_type, paid_on,
               amount, currency, quantity, gross_per_share
          FROM investment_dividends WHERE paid_on >= ?
         ORDER BY paid_on DESC LIMIT ?
    """, (since, args.limit))
    by_instrument = rows(conn, """
        SELECT instrument, isin, ROUND(SUM(amount), 2) AS amount, COUNT(*) AS payments
          FROM investment_dividends WHERE paid_on >= ?
         GROUP BY instrument, isin ORDER BY amount DESC
    """, (since,))
    by_month = rows(conn, """
        SELECT substr(paid_on, 1, 7) AS month, ROUND(SUM(amount), 2) AS amount
          FROM investment_dividends WHERE paid_on >= ?
         GROUP BY month ORDER BY month
    """, (since,))
    interest = rows(conn, """
        SELECT account_name, substr(date, 1, 7) AS month,
               ROUND(SUM(amount), 2) AS amount, COUNT(*) AS payments
          FROM transactions
         WHERE account_type = 'investment' AND category = 'Interest' AND date >= ?
         GROUP BY account_name, month ORDER BY month
    """, (since,))
    return {
        "freshness": freshness(conn),
        "dividends": {
            "total": round(sum(d["amount"] or 0 for d in dividends), 2),
            "by_instrument": by_instrument, "by_month": by_month, "items": dividends,
        },
        "interest": {
            "total": round(sum(i["amount"] or 0 for i in interest), 2),
            "by_month": interest,
        },
    }


def cmd_flows(conn, args) -> dict:
    """Cash into and out of the brokerage - deposits and withdrawals."""
    items = rows(conn, """
        SELECT account_name, date, description, amount, category
          FROM transactions
         WHERE account_type = 'investment' AND category = 'Transfers'
         ORDER BY date DESC LIMIT ?
    """, (args.limit,))
    by_month = rows(conn, """
        SELECT substr(date, 1, 7) AS month, ROUND(SUM(amount), 2) AS net
          FROM transactions
         WHERE account_type = 'investment' AND category = 'Transfers'
         GROUP BY month ORDER BY month
    """)
    fees = rows(conn, """
        SELECT account_name, ROUND(SUM(amount), 2) AS amount, COUNT(*) AS n
          FROM transactions
         WHERE account_type = 'investment' AND category = 'Investment Fees'
         GROUP BY account_name
    """)
    return {
        "freshness": freshness(conn),
        "net_contributed_gbp": round(sum(i["amount"] or 0 for i in items), 2),
        "by_month": by_month, "fees": fees, "items": items,
    }


_READ_ONLY = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)
_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|attach|detach|pragma|vacuum)\b",
    re.IGNORECASE,
)


def cmd_query(conn, args) -> dict:
    """Arbitrary SELECT, for questions the fixed subcommands do not cover."""
    sql = args.sql.strip().rstrip(";")
    if not _READ_ONLY.match(sql) or _FORBIDDEN.search(sql) or ";" in sql:
        raise SystemExit(
            "Refused: only a single SELECT/WITH statement is allowed. The database "
            "is also opened read-only, so writes cannot succeed regardless."
        )
    return {"freshness": freshness(conn), "sql": sql, "rows": rows(conn, sql)}


def cmd_schema(conn, args) -> dict:
    out = {}
    for (name,) in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        " ORDER BY name"
    ):
        out[name] = {
            "columns": [
                {"name": c["name"], "type": c["type"], "notnull": bool(c["notnull"])}
                for c in conn.execute(f"PRAGMA table_info({name})")
            ],
            "rows": conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0],
        }
    return {"freshness": freshness(conn), "tables": out}


def _print_table(result, indent=0) -> None:
    pad = "  " * indent
    for key, value in result.items():
        if isinstance(value, list) and value and isinstance(value[0], dict):
            print(f"{pad}{key}:")
            cols = list(value[0].keys())
            widths = [max(len(c), *(len(str(r.get(c, ""))) for r in value)) for c in cols]
            print(pad + "  " + "  ".join(c.ljust(w) for c, w in zip(cols, widths)))
            for r in value:
                print(pad + "  " + "  ".join(
                    str(r.get(c, "")).ljust(w) for c, w in zip(cols, widths)))
        elif isinstance(value, dict):
            print(f"{pad}{key}:")
            _print_table(value, indent + 1)
        else:
            print(f"{pad}{key}: {value}")


def main() -> int:
    p = argparse.ArgumentParser(description="Read-only portfolio queries.")
    p.add_argument("--table", action="store_true",
                   help="human-readable output (default is JSON, for Claude)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("snapshot", help="latest holdings, weights and totals")
    s.add_argument("--account")
    s.set_defaults(fn=cmd_snapshot)

    s = sub.add_parser("trades", help="executed buys and sells")
    s.add_argument("--isin", help="ISIN, instrument symbol or ticker prefix")
    s.add_argument("--since", help="YYYY-MM-DD")
    s.add_argument("--account")
    s.add_argument("--limit", type=int, default=500)
    s.set_defaults(fn=cmd_trades)

    s = sub.add_parser("income", help="dividends and interest")
    s.add_argument("--since", help="YYYY-MM-DD")
    s.add_argument("--limit", type=int, default=500)
    s.set_defaults(fn=cmd_income)

    s = sub.add_parser("flows", help="deposits and withdrawals")
    s.add_argument("--limit", type=int, default=500)
    s.set_defaults(fn=cmd_flows)

    s = sub.add_parser("query", help="a single read-only SELECT")
    s.add_argument("sql")
    s.set_defaults(fn=cmd_query)

    s = sub.add_parser("schema", help="tables, columns and row counts")
    s.set_defaults(fn=cmd_schema)

    args = p.parse_args()
    conn = connect()
    try:
        result = args.fn(conn, args)
    finally:
        conn.close()

    if args.table:
        _print_table(result)
    else:
        json.dump(result, sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
