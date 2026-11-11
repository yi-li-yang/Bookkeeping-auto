"""Made-up statement lines only. Never put real statement data in tests."""
from backend.agent_tools.statement_to_ledger import (
    detect_columns,
    parse_amount,
    parse_day,
    rows_from_csv,
    rows_from_text,
    to_ledger,
)


def test_parse_amount_handles_pounds_commas_and_brackets():
    assert parse_amount("£1,234.50") == 1234.5
    assert parse_amount("(12.00)") == -12.0
    assert parse_amount("12.00-") == -12.0
    assert parse_amount("") is None
    assert parse_amount("abc") is None


def test_parse_day_is_day_first_and_takes_a_year():
    assert parse_day("03/10/2026") == "2026-10-03"
    assert parse_day("03 Oct", 2025) == "2025-10-03"
    assert parse_day("nonsense") is None


def test_detects_common_column_names():
    cols = detect_columns(["Date", "Description", "Amount", "Extra"])
    assert cols["date"] == "Date" and cols["description"] == "Description" and cols["amount"] == "Amount"


def test_csv_with_one_amount_column_and_positive_charges():
    text = "Date,Description,Amount\n03/10/2026,EXAMPLE CAFE,4.50\n05/10/2026,PAYMENT RECEIVED,-100.00\n"
    rows, warnings = rows_from_csv(text, charges_positive=True)
    assert warnings == []
    assert [r["amount_gbp"] for r in rows] == [-4.5, 100.0]


def test_csv_with_paid_in_and_paid_out_columns():
    text = "Date,Details,Paid out,Paid in\n01/10/2026,EXAMPLE RENT,900.00,\n02/10/2026,EXAMPLE SALARY,,2000.00\n"
    rows, _ = rows_from_csv(text)
    assert [r["amount_gbp"] for r in rows] == [-900.0, 2000.0]


def test_csv_without_known_columns_warns():
    rows, warnings = rows_from_csv("A,B\n1,2\n")
    assert rows == [] and "could not find" in warnings[0]


def test_text_lines_with_credit_marker():
    text = "Statement for October\n03 Oct EXAMPLE SHOP LONDON 12.30\n09 Oct REFUND EXAMPLE SHOP 5.00 CR\nTotal 7.30\n"
    rows, warnings = rows_from_text(text, 2026, charges_positive=True)
    assert [(r["date"], r["amount_gbp"]) for r in rows] == [("2026-10-03", -12.3), ("2026-10-09", 5.0)]
    assert warnings == []


def test_to_ledger_marks_repeated_lines_and_totals():
    rows = [
        {"date": "2026-10-03", "amount_gbp": -3.2, "description": "Coffee", "line": 2},
        {"date": "2026-10-03", "amount_gbp": -3.2, "description": "Coffee", "line": 3},
        {"date": "2026-10-04", "amount_gbp": -10.0, "description": "Lunch", "line": 4},
    ]
    out = to_ledger(rows, "amex", "2026-10", [])
    assert out["total_gbp"] == -16.4 and out["count"] == 3
    assert [r.get("ref") for r in out["rows"]] == ["line 2", "line 3", None]
