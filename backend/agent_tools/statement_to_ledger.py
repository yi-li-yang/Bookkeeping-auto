"""Turn one statement file into rows for the connector's save_ledger_entries tool.

Agents in Claude Duet take a statement from mail or Drive, run this on it in
their own session, check the rows (and the statement total), add categories,
and save them with save_ledger_entries on yili-mcp-connector.

    python -m backend.agent_tools.statement_to_ledger FILE --source amex --statement 2026-10
    python -m backend.agent_tools.statement_to_ledger FILE --source amex --statement 2026-10 --charges-positive
    python -m backend.agent_tools.statement_to_ledger FILE.pdf --source chase --statement 2026-10 --year 2026
    python -m backend.agent_tools.statement_to_ledger FILE.pdf --text   # dump the PDF text and tables only

It needs no database, no .env and no API key. It prints JSON to stdout and
writes nothing to disk. The output holds real amounts, so it must never be
committed or saved into this repository.

Sign convention for the output: amount_gbp is negative for money out.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
from datetime import date
from pathlib import Path

import dateutil.parser

DATE_COLUMNS = ("date", "transaction date", "posted date", "posting date", "trans date", "value date", "completed date")
DESCRIPTION_COLUMNS = ("description", "merchant", "name", "details", "payee", "narrative", "transaction description", "reference")
AMOUNT_COLUMNS = ("amount", "amount (gbp)", "amount gbp", "value", "transaction amount", "money")
DEBIT_COLUMNS = ("debit", "paid out", "money out", "withdrawals", "out", "debit amount")
CREDIT_COLUMNS = ("credit", "paid in", "money in", "deposits", "in", "credit amount")

_SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")


def parse_amount(value) -> float | None:
    """'£1,234.50' -> 1234.5 and '(12.00)' -> -12.0. A trailing CR is handled by the caller."""
    if value is None:
        return None
    s = str(value).strip()
    if s in ("", "-", "N/A", "n/a"):
        return None
    negative = s.startswith("(") and s.endswith(")")
    s = s.strip("()").replace(",", "").replace("£", "").replace("GBP", "").strip()
    if s.endswith("-"):
        negative, s = True, s[:-1]
    try:
        n = float(s)
    except ValueError:
        return None
    return -n if negative else n


def parse_day(value, year: int | None = None) -> str | None:
    """UK day-first dates to YYYY-MM-DD. A date without a year takes `year`."""
    if value is None or not str(value).strip():
        return None
    text = str(value).strip()
    default = date(year or date.today().year, 1, 1)
    try:
        return dateutil.parser.parse(text, dayfirst=True, default=_as_datetime(default)).strftime("%Y-%m-%d")
    except (ValueError, OverflowError):
        return None


def _as_datetime(d: date):
    from datetime import datetime

    return datetime(d.year, d.month, d.day)


def _find(columns: list[str], names: tuple[str, ...]) -> str | None:
    lowered = {c.strip().lower(): c for c in columns}
    for name in names:
        if name in lowered:
            return lowered[name]
    return None


def detect_columns(columns: list[str]) -> dict:
    return {
        "date": _find(columns, DATE_COLUMNS),
        "description": _find(columns, DESCRIPTION_COLUMNS),
        "amount": _find(columns, AMOUNT_COLUMNS),
        "debit": _find(columns, DEBIT_COLUMNS),
        "credit": _find(columns, CREDIT_COLUMNS),
    }


def rows_from_csv(text: str, charges_positive: bool = False) -> tuple[list[dict], list[str]]:
    """Rows from a CSV export. Returns (rows, warnings)."""
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    columns = [c for c in (reader.fieldnames or []) if c]
    cols = detect_columns(columns)
    warnings: list[str] = []
    if not cols["date"] or not cols["description"] or not (cols["amount"] or cols["debit"] or cols["credit"]):
        return [], [f"could not find date, description and amount columns among: {', '.join(columns)}"]

    rows = []
    for line_no, raw in enumerate(reader, start=2):
        day = parse_day(raw.get(cols["date"]))
        if cols["amount"]:
            amount = parse_amount(raw.get(cols["amount"]))
            if amount is not None and charges_positive:
                amount = -amount
        else:
            debit = parse_amount(raw.get(cols["debit"])) if cols["debit"] else None
            credit = parse_amount(raw.get(cols["credit"])) if cols["credit"] else None
            amount = None if debit is None and credit is None else (credit or 0.0) - abs(debit or 0.0)
        description = " ".join(str(raw.get(cols["description"]) or "").split())
        if day is None or amount is None or not description:
            warnings.append(f"skipped line {line_no}: missing date, amount or description")
            continue
        rows.append({"date": day, "amount_gbp": round(amount, 2), "description": description[:200], "line": line_no})
    return rows, warnings


# "03 Oct", "03 Oct 2026", "03/10", "03/10/26", "03.10.2026" at the start of a line,
# then the description, then an amount at the end, optionally marked CR (credit).
_LINE = re.compile(
    r"^(?P<date>\d{1,2}[ /.-](?:[A-Za-z]{3,9}|\d{1,2})(?:[ /.-]\d{2,4})?)\s+"
    r"(?P<desc>.+?)\s+"
    r"(?P<amount>-?£?\(?[\d,]+\.\d{2}\)?-?)\s*(?P<cr>CR)?$"
)


def rows_from_text(text: str, year: int | None, charges_positive: bool) -> tuple[list[dict], list[str]]:
    """Best-effort rows from statement text, one transaction per line.

    Card statements usually print charges as positive numbers and credits
    with CR, so pass charges_positive=True for them. Agents must check the
    result against the statement's own totals before saving.
    """
    rows, warnings = [], []
    for line_no, line in enumerate(text.splitlines(), start=1):
        m = _LINE.match(line.strip())
        if not m:
            continue
        day = parse_day(m["date"], year)
        amount = parse_amount(m["amount"])
        if day is None or amount is None:
            continue
        if m["cr"]:
            amount = abs(amount)  # CR marks money in
        elif charges_positive:
            amount = -amount
        rows.append({"date": day, "amount_gbp": round(amount, 2), "description": " ".join(m["desc"].split())[:200], "line": line_no})
    if not rows:
        warnings.append("no transaction lines recognised; read the text with --text and extract the rows by hand")
    if year is None:
        warnings.append("no --year given: dates printed without a year were given this year")
    return rows, warnings


def to_ledger(rows: list[dict], source: str, statement: str, warnings: list[str]) -> dict:
    """The arguments for save_ledger_entries, plus count, total and warnings."""
    seen: dict[tuple, int] = {}
    keys = [(r["date"], r["amount_gbp"], r["description"]) for r in rows]
    for k in keys:
        seen[k] = seen.get(k, 0) + 1
    out = []
    for r, k in zip(rows, keys):
        row = {"date": r["date"], "amount_gbp": r["amount_gbp"], "description": r["description"]}
        if seen[k] > 1:
            row["ref"] = f"line {r['line']}"
        out.append(row)
    return {
        "source": source,
        "statement": statement,
        "rows": out,
        "count": len(out),
        "total_gbp": round(sum(r["amount_gbp"] for r in out), 2),
        "warnings": warnings,
    }


def pdf_contents(path: Path) -> dict:
    import pdfplumber

    tables, parts = [], []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            parts.append(page.extract_text() or "")
            for table in page.extract_tables():
                cleaned = [row for row in table if any(cell for cell in row if cell)]
                if cleaned:
                    tables.append(cleaned)
    return {"text": "\n".join(parts), "tables": tables}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("file", type=Path)
    p.add_argument("--source", help='short lowercase name, e.g. "amex"')
    p.add_argument("--statement", help='short lowercase name of the statement, e.g. "2026-10"')
    p.add_argument("--year", type=int, help="year for dates printed without one (PDF statements)")
    p.add_argument("--charges-positive", action="store_true", help="the file prints money out as positive numbers (most card statements)")
    p.add_argument("--text", action="store_true", help="print the PDF's text and tables and stop")
    args = p.parse_args(argv)

    suffix = args.file.suffix.lower()
    if args.text:
        if suffix != ".pdf":
            p.error("--text is for PDF files")
        json.dump(pdf_contents(args.file), sys.stdout, ensure_ascii=False, indent=1)
        print()
        return 0

    for name in ("source", "statement"):
        value = getattr(args, name)
        if not value or not _SLUG.match(value):
            p.error(f"--{name} must be a short lowercase name such as amex or 2026-10")

    if suffix == ".pdf":
        rows, warnings = rows_from_text(pdf_contents(args.file)["text"], args.year, args.charges_positive)
    elif suffix in (".csv", ".txt"):
        rows, warnings = rows_from_csv(args.file.read_text(encoding="utf-8-sig", errors="replace"), args.charges_positive)
    else:
        p.error("only .csv and .pdf files are supported")

    json.dump(to_ledger(rows, args.source, args.statement, warnings), sys.stdout, ensure_ascii=False, indent=1)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
