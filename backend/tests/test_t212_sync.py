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

    # external_id is keyed on the Trading 212 account id, not the env var slug.
    deposit = rows["t212:12345678:TXN-1"]
    assert deposit.amount == 1000.0          # income, positive
    assert deposit.category == "Transfers"
    assert deposit.account_type == "investment"
    assert deposit.account_name == "Trading 212 Invest"
    assert deposit.date == "2026-01-10"
    assert deposit.confidence == 1.0         # deterministic, not an LLM guess

    assert rows["t212:12345678:TXN-2"].amount == -250.0   # withdrawal, negative
    assert rows["t212:12345678:TXN-3"].amount == -1.50
    assert rows["t212:12345678:TXN-3"].category == "Investment Fees"


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


# -- ticker normalisation and instrument identity ---------------------------

@pytest.mark.parametrize("raw,expected", [
    ("AAPL_US_EQ", "AAPL"),      # SYMBOL_EXCHANGE_TYPE
    ("BTIPl_EQ", "BTIP"),        # London listing: trailing lowercase marker
    ("VUSAl_EQ", "VUSA"),
    ("SGLNl_EQ", "SGLN"),
    ("XLKQl_EQ", "XLKQ"),
    ("GOOGL_US_EQ", "GOOGL"),    # trailing L is uppercase - must be kept
    ("PLTR_US_EQ", "PLTR"),
    ("TSLA_US_EQ", "TSLA"),
])
def test_normalise_ticker_strips_only_lowercase_exchange_markers(raw, expected):
    assert t212_sync.normalise_ticker(raw) == expected


def test_existing_name_for_an_isin_wins_so_series_stay_continuous(db):
    """Trading 212 returns legacy 'FB' for Meta; the statement parser said 'META'."""
    sf = SourceFile(filename="s.pdf", file_type="pdf", status="done")
    db.add(sf)
    db.flush()
    db.add(InvestmentHolding(
        report_date="2026-03-11", account_name="Trading 212 Stocks ISA",
        instrument="META", isin="US30303M1027", value_gbp=100.0, source_file_id=sf.id,
    ))
    db.commit()

    assert t212_sync._instrument_name(db, "US30303M1027", "FB_US_EQ") == "META"
    # An unknown ISIN just gets the normalised symbol.
    assert t212_sync._instrument_name(db, "US0378331005", "AAPL_US_EQ") == "AAPL"


# -- account detection ------------------------------------------------------

def _seed_two_accounts(db):
    sf = SourceFile(filename="s.pdf", file_type="pdf", status="done")
    db.add(sf)
    db.flush()
    # ISA holds the VUSA/SGLN pair; Invest holds AVGO/MSTR. TSLA is in both.
    for name, isins in {
        "Trading 212 Stocks ISA": ["IE00B3XXRP09", "IE00B4ND3602", "US88160R1014"],
        "Trading 212 Invest": ["US11135F1012", "US5949724083", "US88160R1014"],
    }.items():
        for isin in isins:
            db.add(InvestmentHolding(
                report_date="2026-03-11", account_name=name, instrument=isin[:4],
                isin=isin, value_gbp=10.0, source_file_id=sf.id,
            ))
    db.commit()


def _positions_with(isins):
    return [{"instrument": {"ticker": "X_US_EQ", "isin": i}} for i in isins]


def test_isin_overlap_identifies_the_right_account(db):
    _seed_two_accounts(db)
    detected = t212_sync._detect_account_by_isin(
        db, _positions_with(["IE00B3XXRP09", "IE00B4ND3602", "US88160R1014"])
    )
    assert detected is not None
    assert detected[0] == "Trading 212 Stocks ISA"


def test_detection_declines_when_the_match_is_ambiguous(db):
    _seed_two_accounts(db)
    # Only the shared holding: no basis to choose between the two accounts.
    assert t212_sync._detect_account_by_isin(db, _positions_with(["US88160R1014"])) is None


def test_detection_overrides_a_wrong_env_var_hint(db):
    """
    Trading 212 does not say which account a key belongs to, so the env var name
    is a guess. Evidence must win over it.
    """
    _seed_two_accounts(db)
    mislabelled = T212Account(slug="invest", api_key_id="k", api_secret="s")
    label = t212_sync._account_label(
        db, mislabelled, {"id": 42},
        _positions_with(["IE00B3XXRP09", "IE00B4ND3602", "US88160R1014"]),
    )
    assert label == "Trading 212 Stocks ISA"


def test_hint_is_used_when_there_is_nothing_to_detect_against(db):
    account = T212Account(slug="invest", api_key_id="k", api_secret="s")
    assert t212_sync._account_label(db, account, {"id": 42}, []) == "Trading 212 Invest"


def test_falls_back_to_account_id_with_no_hint_and_no_evidence(db):
    account = T212Account(slug="legacy", api_key_id="k", api_secret="s")
    assert t212_sync._account_label(db, account, {"id": 42}, []) == "Trading 212 42"


# -- transaction sign and category mapping ----------------------------------

@pytest.mark.parametrize("tx_type,raw,expected", [
    # Trading 212 sends every amount positive; direction comes from the type.
    ("DEPOSIT", 15000.0, 15000.0),
    ("INTEREST_ON_FREE_CASH", 1.45, 1.45),     # income, not an outgoing
    ("INTEREST", 2.0, 2.0),
    ("DIVIDEND", 3.0, 3.0),
    ("WITHDRAW", 250.0, -250.0),
    ("FEE", 1.50, -1.50),
    ("TAX", 4.0, -4.0),
    # Ambiguous direction: keep whatever the API said rather than guessing.
    ("TRANSFER", 5000.0, 5000.0),
    ("TRANSFER", -5000.0, -5000.0),
    ("SOMETHING_NEW", -7.0, -7.0),
])
def test_amount_direction_comes_from_type_not_a_blanket_rule(tx_type, raw, expected):
    assert t212_sync._signed_amount(tx_type, raw) == expected


def test_interest_is_income_and_categorised_as_interest(db, monkeypatch):
    """346 of this user's 350 records are daily interest; a sign error here
    would invert the entire investment cash flow."""
    items = [{"type": "INTEREST_ON_FREE_CASH", "amount": 1.45, "currency": "GBP",
              "dateTime": "2026-08-24T01:05:38.845Z", "reference": "ref-1"}]

    def handler(request):
        path = request.url.path
        if path.endswith("/account/summary"):
            return httpx.Response(200, json=SUMMARY)
        if path.endswith("/positions"):
            return httpx.Response(200, json=[])
        if "history/transactions" in path:
            return httpx.Response(200, json={"items": items, "nextPagePath": None})
        return httpx.Response(200, json={"items": [], "nextPagePath": None})

    patch_client(monkeypatch, handler)
    t212_sync.sync_account(db, ACCOUNT, include_history=True)

    tx = db.query(Transaction).one()
    assert tx.amount == 1.45           # positive: it is income
    assert tx.category == "Interest"
    assert tx.description == "Trading 212 Interest On Free Cash"


def test_cash_absorbs_price_drift_so_the_total_reconciles_exactly(db, monkeypatch):
    """
    account/summary and positions are separate calls, so in a moving market
    their figures come from different instants. Cash is the residual against the
    positions actually stored, which keeps the portfolio total exact.
    """
    summary = {
        "id": 1, "currency": "GBP", "totalValue": 1000.0,
        "cash": {"availableToTrade": 100.0, "reservedForOrders": 0, "inPies": 0},
        # Deliberately inconsistent with the positions below, as happens live.
        "investments": {"currentValue": 900.0},
    }
    positions = [{
        "instrument": {"ticker": "X_US_EQ", "isin": "US1", "currency": "GBP"},
        "quantity": 1, "currentPrice": 100.0, "averagePricePaid": 90.0,
        "walletImpact": {"totalCost": 90.0, "currentValue": 893.44,
                         "unrealizedProfitLoss": 3.44},
    }]

    def handler(request):
        p = request.url.path
        if p.endswith("/account/summary"):
            return httpx.Response(200, json=summary)
        if p.endswith("/positions"):
            return httpx.Response(200, json=positions)
        return httpx.Response(200, json={"items": [], "nextPagePath": None})

    patch_client(monkeypatch, handler)
    t212_sync.sync_account(db, ACCOUNT, include_history=False)

    total = sum(h.value_gbp for h in db.query(InvestmentHolding).all())
    assert total == pytest.approx(1000.0, abs=0.01)

    cash = db.query(InvestmentHolding).filter_by(instrument="CASH").one()
    assert cash.value_gbp == pytest.approx(1000.0 - 893.44, abs=0.01)


def test_renaming_the_env_var_does_not_reimport_the_history(db, stub_api, monkeypatch):
    """
    Row identity must come from Trading 212's account id, not the env var name.

    Keying on the slug meant that renaming T212_API_KEY_INVEST to
    ..._STOCKS_ISA changed every external_id and the SourceFile name, so an
    entire account's history re-imported as new and duplicated.
    """
    as_invest = T212Account(slug="invest", api_key_id="k", api_secret="s")
    as_isa = T212Account(slug="stocks_isa", api_key_id="k", api_secret="s")

    t212_sync.sync_account(db, as_invest, include_history=True)
    after_first = db.query(Transaction).count()
    assert after_first > 0

    # Same credentials, same Trading 212 account, different env var name.
    t212_sync.sync_account(db, as_isa, include_history=True)

    assert db.query(Transaction).count() == after_first
    assert db.query(SourceFile).filter(SourceFile.file_type == "api").count() == 1
    # Identity is the account id from account/summary, not the slug.
    assert db.query(SourceFile).filter_by(filename="trading212:12345678").count() == 1


def test_lending_interest_is_income(db):
    assert t212_sync._signed_amount("LENDING_INTEREST", 0.05) == 0.05
    assert t212_sync._TRANSACTION_CATEGORIES["LENDING_INTEREST"] == "Interest"
