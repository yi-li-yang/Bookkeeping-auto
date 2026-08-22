"""
Trading 212 sync tests: API payloads -> database rows.

Uses a real (temporary) SQLite database so the actual SQLAlchemy models,
constraints and the delete-then-insert idempotency path are exercised, with the
HTTP layer stubbed out.
"""
import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.db.database import Base
from backend.db.models import (
    InvestmentDividend,
    InvestmentHolding,
    SourceFile,
    SyncRun,
    Transaction,
)
from backend.integrations import t212_sync
from backend.integrations.trading212 import T212Account, Trading212Client

_PRISTINE_INIT = Trading212Client.__init__

ACCOUNT = T212Account(slug="invest", api_key_id="k", api_secret="s")


def patch_client(monkeypatch, handler):
    """Route Trading212Client through `handler` and disable sleeping."""
    def patched(self, account, env="live", timeout=30.0, transport=None, sleep=None):
        _PRISTINE_INIT(
            self, account, env=env, timeout=timeout,
            transport=httpx.MockTransport(handler), sleep=lambda s: None,
        )

    monkeypatch.setattr(Trading212Client, "__init__", patched)

SUMMARY = {
    "id": 12345678,
    "currency": "GBP",
    "totalValue": 15250.75,
    "cash": {"availableToTrade": 2500.50, "reservedForOrders": 150.0, "inPies": 500.0},
    "investments": {
        "currentValue": 12100.25,
        "totalCost": 10500.0,
        "realizedProfitLoss": 850.50,
        "unrealizedProfitLoss": 1600.25,
    },
}

POSITIONS = [
    {
        "instrument": {
            "ticker": "AAPL_US_EQ",
            "name": "Apple Inc",
            "isin": "US0378331005",
            "currency": "USD",
        },
        "quantity": 15.5,
        "currentPrice": 185.50,
        "averagePricePaid": 170.25,
        "walletImpact": {
            "currency": "GBP",
            "totalCost": 2089.45,
            "currentValue": 2275.10,
            "unrealizedProfitLoss": 185.65,
            "fxImpact": 12.30,
        },
    },
    {
        "instrument": {
            "ticker": "VUSA_LSE_EQ",
            "name": "Vanguard S&P 500",
            "isin": "IE00B3XXRP09",
            "currency": "GBP",
        },
        "quantity": 100.0,
        "currentPrice": 98.25,
        "averagePricePaid": 90.0,
        "walletImpact": {
            "currency": "GBP",
            "totalCost": 9000.0,
            "currentValue": 9825.15,
            "unrealizedProfitLoss": 825.15,
        },
    },
]

TRANSACTIONS = [
    {"type": "DEPOSIT", "amount": 1000.0, "currency": "GBP",
     "dateTime": "2026-01-10T14:30:00Z", "reference": "TXN-1"},
    {"type": "WITHDRAW", "amount": 250.0, "currency": "GBP",
     "dateTime": "2026-02-11T09:00:00Z", "reference": "TXN-2"},
    {"type": "FEE", "amount": 1.50, "currency": "GBP",
     "dateTime": "2026-02-12T09:00:00Z", "reference": "TXN-3"},
]

DIVIDENDS = [
    {"ticker": "AAPL_US_EQ",
     "instrument": {"name": "Apple Inc", "isin": "US0378331005"},
     "type": "ORDINARY", "amount": 12.50, "currency": "GBP",
     "grossAmountPerShare": 0.24, "quantity": 15.5,
     "paidOn": "2026-02-15T00:00:00Z", "reference": "DIV-1"},
]


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture
def stub_api(monkeypatch):
    """Point Trading212Client at a MockTransport and skip all sleeping."""
    def handler(request):
        path = request.url.path
        if path.endswith("/account/summary"):
            return httpx.Response(200, json=SUMMARY)
        if path.endswith("/positions"):
            return httpx.Response(200, json=POSITIONS)
        if "history/transactions" in path:
            return httpx.Response(200, json={"items": TRANSACTIONS, "nextPagePath": None})
        if "history/dividends" in path:
            return httpx.Response(200, json={"items": DIVIDENDS, "nextPagePath": None})
        return httpx.Response(404)

    patch_client(monkeypatch, handler)
    return handler


# -- holdings mapping ------------------------------------------------------

def test_positions_map_onto_investment_holdings(db, stub_api):
    result = t212_sync.sync_account(db, ACCOUNT, include_history=False)

    aapl = db.query(InvestmentHolding).filter_by(instrument="AAPL").one()
    assert aapl.isin == "US0378331005"
    assert aapl.currency == "USD"
    assert aapl.quantity == 15.5
    assert aapl.avg_price == 170.25
    assert aapl.current_price == 185.50
    assert aapl.cost_gbp == 2089.45
    assert aapl.value_gbp == 2275.10
    assert aapl.return_gbp == 185.65
    assert aapl.account_name == "Trading 212 Invest"
    assert result["report_date"] == aapl.report_date


def test_ticker_is_shortened_to_match_the_pdf_convention(db, stub_api):
    t212_sync.sync_account(db, ACCOUNT, include_history=False)
    names = {h.instrument for h in db.query(InvestmentHolding).all()}
    # "AAPL_US_EQ" -> "AAPL", not the raw Trading 212 ticker.
    assert "AAPL" in names and "AAPL_US_EQ" not in names
    assert "VUSA" in names


def test_fx_rate_is_a_derived_multiplier_not_the_fximpact_amount(db, stub_api):
    t212_sync.sync_account(db, ACCOUNT, include_history=False)
    aapl = db.query(InvestmentHolding).filter_by(instrument="AAPL").one()

    # value_gbp / (quantity * currentPrice) = 2275.10 / (15.5 * 185.50)
    assert aapl.fx_rate == pytest.approx(2275.10 / (15.5 * 185.50), rel=1e-6)
    # Must not be walletImpact.fxImpact, which is a GBP P&L amount.
    assert aapl.fx_rate != 12.30
    assert 0 < aapl.fx_rate < 2


# -- cash reconciliation ---------------------------------------------------

def test_cash_is_stored_as_a_synthetic_holding(db, stub_api):
    t212_sync.sync_account(db, ACCOUNT, include_history=False)
    cash = db.query(InvestmentHolding).filter_by(instrument="CASH").one()

    # Residual, so the portfolio total matches Trading 212's own totalValue.
    assert cash.value_gbp == pytest.approx(15250.75 - 12100.25)
    assert cash.currency == "GBP"


def test_cash_row_has_null_cost_and_return_so_existing_endpoints_stay_correct(db, stub_api):
    """
    /performance filters on return_gbp is not None and SUM skips NULLs, so cash
    must not carry a cost basis or a return, or it would distort return figures.
    """
    t212_sync.sync_account(db, ACCOUNT, include_history=False)
    cash = db.query(InvestmentHolding).filter_by(instrument="CASH").one()
    assert cash.cost_gbp is None
    assert cash.return_gbp is None
    assert cash.quantity is None


def test_holdings_total_reconciles_with_reported_total_value(db, stub_api):
    t212_sync.sync_account(db, ACCOUNT, include_history=False)
    total = sum(h.value_gbp for h in db.query(InvestmentHolding).all())
    assert total == pytest.approx(SUMMARY["totalValue"], abs=0.01)


# -- transactions ----------------------------------------------------------

def test_transactions_map_with_correct_signs_and_categories(db, stub_api):
    t212_sync.sync_account(db, ACCOUNT, include_history=True)
    rows = {t.external_id: t for t in db.query(Transaction).all()}

    deposit = rows["t212:invest:TXN-1"]
    assert deposit.amount == 1000.0          # income, positive
    assert deposit.category == "Transfers"
    assert deposit.account_type == "investment"
    assert deposit.account_name == "Trading 212 Invest"
    assert deposit.date == "2026-01-10"
    assert deposit.confidence == 1.0         # deterministic, not an LLM guess

    assert rows["t212:invest:TXN-2"].amount == -250.0   # withdrawal, negative
    assert rows["t212:invest:TXN-3"].amount == -1.50
    assert rows["t212:invest:TXN-3"].category == "Investment Fees"


def test_dividends_are_recorded(db, stub_api):
    t212_sync.sync_account(db, ACCOUNT, include_history=True)
    div = db.query(InvestmentDividend).one()
    assert div.ticker == "AAPL_US_EQ"
    assert div.amount == 12.50
    assert div.paid_on == "2026-02-15"
    assert div.dividend_type == "ORDINARY"
    assert div.reference == "DIV-1"


# -- idempotency -----------------------------------------------------------

def test_resync_does_not_duplicate_anything(db, stub_api):
    first = t212_sync.sync_account(db, ACCOUNT, include_history=True)
    counts_after_first = (
        db.query(InvestmentHolding).count(),
        db.query(Transaction).count(),
        db.query(InvestmentDividend).count(),
    )

    t212_sync.sync_account(db, ACCOUNT, include_history=True)
    counts_after_second = (
        db.query(InvestmentHolding).count(),
        db.query(Transaction).count(),
        db.query(InvestmentDividend).count(),
    )

    assert counts_after_first == counts_after_second
    assert first["holdings"] == 3  # 2 positions + cash


def test_resync_replaces_holdings_so_a_sold_position_disappears(db, stub_api, monkeypatch):
    t212_sync.sync_account(db, ACCOUNT, include_history=False)
    assert db.query(InvestmentHolding).filter_by(instrument="AAPL").count() == 1

    def handler(request):
        path = request.url.path
        if path.endswith("/account/summary"):
            return httpx.Response(200, json=SUMMARY)
        if path.endswith("/positions"):
            return httpx.Response(200, json=POSITIONS[1:])  # AAPL sold
        return httpx.Response(200, json={"items": [], "nextPagePath": None})

    patch_client(monkeypatch, handler)
    t212_sync.sync_account(db, ACCOUNT, include_history=False)

    assert db.query(InvestmentHolding).filter_by(instrument="AAPL").count() == 0
    assert db.query(InvestmentHolding).filter_by(instrument="VUSA").count() == 1


def test_pdf_sourced_rows_are_never_touched_by_a_sync(db, stub_api):
    """API rows belong to a different source_file, so PDF history must survive."""
    pdf = SourceFile(filename="statement.pdf", file_type="pdf", status="done")
    db.add(pdf)
    db.flush()
    db.add(InvestmentHolding(
        report_date="2026-03-11", account_name="Trading 212 Invest",
        instrument="LEGACY", value_gbp=999.0, source_file_id=pdf.id,
    ))
    db.commit()

    t212_sync.sync_account(db, ACCOUNT, include_history=False)

    survivor = db.query(InvestmentHolding).filter_by(instrument="LEGACY").one()
    assert survivor.value_gbp == 999.0


# -- orchestration ---------------------------------------------------------

def test_sync_all_records_a_sync_run(db, stub_api, monkeypatch):
    monkeypatch.setattr(t212_sync, "candidate_accounts", lambda: [ACCOUNT])
    result = t212_sync.sync_all(db, include_history=False)

    assert result["synced"] == 1 and result["failed"] == 0
    run = db.query(SyncRun).order_by(SyncRun.id.desc()).first()
    assert run.connector == "trading212:invest"
    assert run.status == "ok"
    assert run.finished_at is not None


def test_one_bad_account_does_not_stop_the_other(db, monkeypatch):
    good = T212Account(slug="invest", api_key_id="k", api_secret="s")
    bad = T212Account(slug="stocks_isa", api_key_id="bad", api_secret="bad")

    def handler(request):
        if request.headers.get("authorization", "").endswith("YmFkOmJhZA=="):
            return httpx.Response(401)
        path = request.url.path
        if path.endswith("/account/summary"):
            return httpx.Response(200, json=SUMMARY)
        if path.endswith("/positions"):
            return httpx.Response(200, json=POSITIONS)
        return httpx.Response(200, json={"items": [], "nextPagePath": None})

    patch_client(monkeypatch, handler)
    monkeypatch.setattr(t212_sync, "candidate_accounts", lambda: [bad, good])

    result = t212_sync.sync_all(db, include_history=False)

    assert result["failed"] == 1 and result["synced"] == 1
    statuses = {r.connector: r.status for r in db.query(SyncRun).all()}
    assert statuses["trading212:stocks_isa"] == "auth_required"
    assert statuses["trading212:invest"] == "ok"
    # The good account still wrote its holdings.
    assert db.query(InvestmentHolding).count() == 3
