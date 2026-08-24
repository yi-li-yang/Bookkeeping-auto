"""
Self-transfers must not read as income or spending.

Moving money from a current account into a brokerage is not earnings, and the
matching debit is not an expense. Before this filter existed, syncing two
Trading 212 accounts added ~96k of phantom income and wrecked every savings-rate
figure on the dashboard.

Balance-tracking views are the deliberate exception: a transfer really does move
the balance, so net worth must keep counting it.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.api import analytics
from backend.db.database import Base
from backend.db.models import SourceFile, Transaction


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'a.db'}")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()

    sf = SourceFile(filename="seed", file_type="api", status="done")
    session.add(sf)
    session.flush()

    rows = [
        # Real income and spending on the bank account.
        ("2026-01-05", "Salary", 3000.0, "Salary", "bank"),
        ("2026-01-06", "Tesco", -80.0, "Groceries", "bank"),
        # Money moved into the brokerage: not income, not an expense.
        ("2026-01-07", "Trading 212 Deposit", 15000.0, "Transfers", "investment"),
        ("2026-01-07", "Transfer to Trading 212", -15000.0, "Transfers", "bank"),
        # Genuinely investment income and cost.
        ("2026-01-08", "Trading 212 Interest On Free Cash", 1.45, "Interest", "investment"),
        ("2026-01-09", "Trading 212 Fee", -2.50, "Investment Fees", "investment"),
    ]
    for date, desc, amount, category, atype in rows:
        session.add(Transaction(
            date=date, description=desc, amount=amount, category=category,
            account_name="acct", account_type=atype, source_file_id=sf.id,
        ))
    session.commit()
    yield session
    session.close()


def test_transfers_are_excluded_from_income_and_expenses(db):
    row = analytics.income_vs_expenses(start=None, end=None, db=db)[0]

    # 3000 salary + 1.45 interest -- the 15000 transfer must not appear.
    assert row["income"] == pytest.approx(3001.45)
    # 80 groceries + 2.50 fee -- the -15000 transfer must not appear.
    assert row["expenses"] == pytest.approx(82.50)   # returned as a magnitude


def test_investment_income_and_fees_are_still_counted(db):
    """
    Filtering by account_type would have been the wrong fix: it would also drop
    interest and fees, which are real.
    """
    row = analytics.income_vs_expenses(start=None, end=None, db=db)[0]
    assert row["income"] > 3000.0    # interest included
    assert row["expenses"] > 80.0    # fee included


def test_transfers_do_not_appear_as_a_spending_category(db):
    rows = analytics.spending_by_category(start=None, end=None, db=db)
    assert "Transfers" not in {r["category"] for r in rows}
    assert "Groceries" in {r["category"] for r in rows}


def test_annual_summary_excludes_transfers(db):
    summary = analytics.annual_summary(year=2026, db=db)
    assert summary["total_income"] == pytest.approx(3001.45)
    assert summary["total_expenses"] == pytest.approx(82.50, abs=0.01)


def test_net_worth_still_counts_transfers(db):
    """
    The deliberate exception: net worth tracks balances, so a transfer between
    accounts must move both sides. Excluding them here would make the investment
    line ignore every deposit.
    """
    rows = analytics.net_worth(start=None, end=None, db=db)
    month = rows[0]

    # 15000 in, 1.45 interest, 2.50 fee.
    assert month["investment"] == pytest.approx(14998.95)
    # 3000 salary - 80 groceries - 15000 transferred out.
    assert month["bank"] == pytest.approx(-12080.0)


def test_anomalies_ignore_transfers(db):
    """A 15k transfer would otherwise dominate every anomaly calculation."""
    found = analytics.anomalies(start=None, end=None, db=db)
    assert all(a["category"] != "Transfers" for a in found)


def test_uncategorised_rows_are_not_dropped(db):
    """The filter must not silently discard rows with a NULL category."""
    sf = db.query(SourceFile).first()
    db.add(Transaction(
        date="2026-01-10", description="Mystery", amount=-5.0, category=None,
        account_name="acct", account_type="bank", source_file_id=sf.id,
    ))
    db.commit()

    row = analytics.income_vs_expenses(start=None, end=None, db=db)[0]
    assert row["expenses"] == pytest.approx(87.50)
