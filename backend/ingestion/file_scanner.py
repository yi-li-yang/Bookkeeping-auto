"""
Scans the input folder for new CSV/PDF files and orchestrates ingestion.
"""
import json
import logging
import os
from datetime import datetime

from sqlalchemy.orm import Session

from db.models import SourceFile, Transaction, InvestmentHolding
from ingestion import csv_parser, pdf_parser, llm_processor

logger = logging.getLogger(__name__)

INPUT_DIR = os.environ.get("INPUT_DIR", "/app/data/input")
SUPPORTED_EXTENSIONS = {".csv", ".pdf"}


def scan_and_ingest(db: Session) -> dict:
    """
    Walk INPUT_DIR, find files not yet in the DB, process them.
    Returns summary: {processed, skipped, errors, files}
    """
    processed = 0
    skipped = 0
    errors = 0
    result_files = []

    for root, _, filenames in os.walk(INPUT_DIR):
        for fname in sorted(filenames):
            ext = os.path.splitext(fname)[1].lower()
            if ext not in SUPPORTED_EXTENSIONS:
                continue

            full_path = os.path.join(root, fname)
            rel_name = os.path.relpath(full_path, INPUT_DIR)

            # Check if already processed successfully
            existing = db.query(SourceFile).filter_by(filename=rel_name).first()
            if existing and existing.status == "done":
                skipped += 1
                continue

            # Create or reuse the SourceFile record
            if not existing:
                sf = SourceFile(filename=rel_name, file_type=ext.lstrip("."))
                db.add(sf)
                db.commit()
                db.refresh(sf)
            else:
                sf = existing
                sf.status = "pending"
                sf.error_message = None
                db.commit()

            try:
                count = _process_file(full_path, sf, db)
                sf.status = "done"
                sf.row_count = count
                sf.processed_at = datetime.utcnow()
                db.commit()
                processed += 1
                logger.info(f"Processed {rel_name}: {count} transactions")
            except Exception as exc:
                sf.status = "error"
                sf.error_message = str(exc)
                db.commit()
                errors += 1
                logger.error(f"Error processing {rel_name}: {exc}")

            db.refresh(sf)
            result_files.append(sf)

    return {
        "processed": processed,
        "skipped": skipped,
        "errors": errors,
        "files": result_files,
    }


def _process_file(file_path: str, sf: SourceFile, db: Session) -> int:
    ext = os.path.splitext(file_path)[1].lower()

    if ext == ".csv":
        return _ingest_csv(file_path, sf, db)
    elif ext == ".pdf":
        return _ingest_pdf(file_path, sf, db)
    else:
        raise ValueError(f"Unsupported file type: {ext}")


def _ingest_csv(file_path: str, sf: SourceFile, db: Session) -> int:
    rows = csv_parser.parse_csv(file_path)
    if not rows:
        return 0

    col_map = llm_processor.detect_csv_columns(rows)
    date_col = col_map.get("date")
    desc_col = col_map.get("description")
    amount_col = col_map.get("amount")
    debit_col = col_map.get("amount_debit")
    credit_col = col_map.get("amount_credit")

    raw_transactions = []
    for row in rows:
        date_val = _get_col(row, date_col)
        desc_val = _get_col(row, desc_col) or ""

        if amount_col:
            amount_val = _parse_amount(_get_col(row, amount_col))
        elif debit_col and credit_col:
            debit = _parse_amount(_get_col(row, debit_col)) or 0.0
            credit = _parse_amount(_get_col(row, credit_col)) or 0.0
            amount_val = credit - debit  # net: positive = money in
        else:
            amount_val = None

        if date_val is None or amount_val is None:
            continue

        raw_transactions.append({
            "date": _normalise_date(date_val),
            "amount": amount_val,
            "description": desc_val,
            "raw_row": json.dumps(row),
        })

    return _save_transactions(raw_transactions, sf, db)


def _is_investment_statement(pdf_data: dict) -> bool:
    """Return True if the PDF looks like a broker portfolio/holdings statement."""
    text_lower = pdf_data.get("text", "").lower()
    indicators = [
        ("trading 212" in text_lower and "activity statement" in text_lower),
        ("trading 212" in text_lower and "portfolio" in text_lower),
        ("open positions" in text_lower and ("invest" in text_lower or "isa" in text_lower)),
    ]
    return any(indicators)


def _ingest_pdf(file_path: str, sf: SourceFile, db: Session) -> int:
    pdf_data = pdf_parser.parse_pdf(file_path)

    if _is_investment_statement(pdf_data):
        return _ingest_investment_pdf(pdf_data, sf, db)

    parsed = llm_processor.parse_pdf_transactions(pdf_data, os.path.basename(file_path))

    raw_transactions = []
    for item in parsed:
        date_val = item.get("date") or ""
        amount_val = item.get("amount")
        desc_val = item.get("description") or ""

        if not date_val or amount_val is None:
            continue

        raw_transactions.append({
            "date": date_val,
            "amount": float(amount_val),
            "description": desc_val,
            "raw_row": json.dumps(item),
        })

    return _save_transactions(raw_transactions, sf, db)


def _ingest_investment_pdf(pdf_data: dict, sf: SourceFile, db: Session) -> int:
    """Extract portfolio holdings from a broker statement and store as InvestmentHolding rows."""
    holdings = llm_processor.extract_investment_holdings(pdf_data["text"], sf.filename)

    count = 0
    for h in holdings:
        value_gbp = h.get("value_gbp")
        if value_gbp is None:
            continue

        holding = InvestmentHolding(
            report_date=h.get("report_date") or "",
            account_name=h.get("account_name") or "Unknown",
            instrument=h.get("instrument") or "Unknown",
            isin=h.get("isin"),
            currency=h.get("currency"),
            quantity=h.get("quantity"),
            avg_price=h.get("avg_price"),
            current_price=h.get("current_price"),
            fx_rate=h.get("fx_rate"),
            cost_gbp=h.get("cost_gbp"),
            value_gbp=float(value_gbp),
            return_gbp=h.get("return_gbp"),
            source_file_id=sf.id,
        )
        db.add(holding)
        count += 1

    db.commit()
    return count


def _save_transactions(raw_transactions: list[dict], sf: SourceFile, db: Session) -> int:
    if not raw_transactions:
        return 0

    # Ask Claude to categorise all at once
    for_llm = [
        {"id": i, "date": t["date"], "amount": t["amount"], "description": t["description"]}
        for i, t in enumerate(raw_transactions)
    ]
    categories = llm_processor.categorise_transactions(for_llm)
    cat_map = {c["id"]: c for c in categories}

    count = 0
    for i, t in enumerate(raw_transactions):
        cat_info = cat_map.get(i, {})
        txn = Transaction(
            date=t["date"],
            description=t["description"],
            amount=t["amount"],
            category=cat_info.get("category"),
            confidence=cat_info.get("confidence"),
            account_name=_infer_account_name(sf.filename),
            account_type=_infer_account_type(sf.filename),
            source_file_id=sf.id,
            raw_row=t.get("raw_row"),
        )
        db.add(txn)
        count += 1

    db.commit()
    return count


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_col(row: dict, col: str | None):
    if col is None:
        return None
    # Case-insensitive lookup
    for k, v in row.items():
        if k.strip().lower() == col.strip().lower():
            return v
    return None


def _parse_amount(val) -> float | None:
    if val is None:
        return None
    s = str(val).replace(",", "").replace("£", "").replace("$", "").replace("€", "").strip()
    if s in ("", "-", "N/A", "n/a"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _normalise_date(val: str) -> str:
    """Best-effort date normalisation; returns the original string if parsing fails."""
    import dateutil.parser
    try:
        return dateutil.parser.parse(str(val), dayfirst=True).strftime("%Y-%m-%d")
    except Exception:
        return str(val)


def _infer_account_name(filename: str) -> str:
    return os.path.splitext(os.path.basename(filename))[0]


def _infer_account_type(filename: str) -> str:
    lower = filename.lower()
    if any(k in lower for k in ("credit", "card", "cc", "chase", "amex", "citi", "discover", "capitalone", "capital_one", "barclays", "natwest", "visa", "mastercard")):
        return "credit_card"
    if any(k in lower for k in ("invest", "stock", "share", "brokerage", "isa")):
        return "investment"
    return "bank"
