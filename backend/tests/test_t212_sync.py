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
    InvestmentTrade,
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



# Shaped from a real /equity/history/orders response. The nesting, the signed
# quantities, the absent `fill` on unexecuted orders and the wallet currency
# that is not always GBP are all as the API returns them.
ORDERS = [
    {   # plain buy: USD instrument, GBP wallet, conversion fee charged
        "order": {
            "id": 55707565256, "ticker": "INTC_US_EQ", "quantity": 26.0,
            "filledQuantity": 26.0, "status": "FILLED", "currency": "GBP",
            "side": "BUY", "createdAt": "2026-08-14T14:05:59.000Z",
            "instrument": {"ticker": "INTC_US_EQ", "name": "Intel",
                           "isin": "US4581401001", "currency": "USD"},
        },
        "fill": {
            "id": 55707565258, "quantity": 26.0, "price": 106.51, "type": "TRADE",
            "filledAt": "2026-08-14T14:05:59.000Z",
            "walletImpact": {
                "currency": "GBP", "netValue": 2046.36, "fxRate": 1.35528801,
                "taxes": [{"name": "CURRENCY_CONVERSION_FEE", "quantity": -3.06,
                           "currency": "GBP"}],
            },
        },
    },
    {   # sell: quantity arrives negative, and a realised P&L comes with it
        "order": {
            "id": 52752573653, "ticker": "ADSd_EQ", "quantity": -0.07797034,
            "filledQuantity": -0.07797034, "status": "FILLED", "currency": "GBP",
            "side": "SELL", "createdAt": "2026-06-16T09:39:13.000Z",
            "instrument": {"ticker": "ADSd_EQ", "name": "Adidas",
                           "isin": "DE000A1EWWW0", "currency": "EUR"},
        },
        "fill": {
            "id": 52752573662, "quantity": -0.07797034, "price": 174.40,
            "type": "TRADE", "filledAt": "2026-06-16T09:39:15.000Z",
            "walletImpact": {
                "currency": "GBP", "netValue": 11.74, "realisedProfitLoss": -0.07,
                "fxRate": 1.15629483,
                "taxes": [{"name": "CURRENCY_CONVERSION_FEE", "quantity": -0.02,
                           "currency": "GBP"}],
            },
        },
    },
    {   # cancelled: no fill key at all, but still reports the quantity it wanted
        "order": {
            "id": 53854719097, "ticker": "INTC_US_EQ", "quantity": 12.0,
            "filledQuantity": 0, "limitPrice": 100.0, "status": "CANCELLED",
            "currency": "GBP", "side": "BUY",
            "createdAt": "2026-07-08T12:13:55.000Z",
            "instrument": {"ticker": "INTC_US_EQ", "name": "Intel",
                           "isin": "US4581401001", "currency": "USD"},
        },
    },
    {   # rejected: likewise no fill
        "order": {
            "id": 53854719098, "ticker": "TSLA_US_EQ", "quantity": 5.0,
            "filledQuantity": 0, "status": "REJECTED", "currency": "GBP",
            "side": "BUY", "createdAt": "2026-07-09T12:13:55.000Z",
            "instrument": {"ticker": "TSLA_US_EQ", "name": "Tesla",
                           "isin": "US88160R1014", "currency": "USD"},
        },
    },
    {   # GBX instrument: price is in pence and fxRate 100 carries the /100
        "order": {
            "id": 51000000001, "ticker": "SGLNl_EQ", "quantity": -4.85942,
            "filledQuantity": -4.85942, "status": "FILLED", "currency": "GBP",
            "side": "SELL", "createdAt": "2026-04-02T10:00:00.000Z",
            "instrument": {"ticker": "SGLNl_EQ", "name": "iShares Physical Gold",
                           "isin": "IE00B4ND3602", "currency": "GBX"},
        },
        "fill": {
            "id": 51000000002, "quantity": -4.85942, "price": 6079.0,
            "type": "TRADE", "filledAt": "2026-04-02T10:00:01.000Z",
            "walletImpact": {"currency": "GBP", "netValue": 295.40,
                             "realisedProfitLoss": 40.10, "fxRate": 100,
                             "taxes": []},
        },
    },
    {   # settled in a USD wallet, as the Invest account did until 2026-05-04
        "order": {
            "id": 50563557150, "ticker": "TSLA_US_EQ", "quantity": -18.94998788,
            "filledQuantity": -18.94998788, "status": "FILLED", "currency": "USD",
            "side": "SELL", "createdAt": "2026-05-04T18:22:00.000Z",
            "instrument": {"ticker": "TSLA_US_EQ", "name": "Tesla",
                           "isin": "US88160R1014", "currency": "USD"},
        },
        "fill": {
            "id": 50563557151, "quantity": -18.94998788, "price": 391.29,
            "type": "TRADE", "filledAt": "2026-05-04T18:22:01.000Z",
            "walletImpact": {"currency": "USD", "netValue": 7414.94,
                             "realisedProfitLoss": 1244.28, "fxRate": 1,
                             "taxes": []},
        },
    },
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
        if "history/orders" in path:
            return httpx.Response(200, json={"items": ORDERS, "nextPagePath": None})
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


# -- trades ----------------------------------------------------------------

def test_only_executed_orders_become_trades(db, stub_api):
    """A cancelled or rejected order still reports a quantity; it is not a trade."""
    t212_sync.sync_account(db, ACCOUNT, include_history=True)

    trades = db.query(InvestmentTrade).all()
    assert len(trades) == 4                      # six orders, two never executed
    statuses = {t.status for t in trades}
    assert statuses == {"FILLED"}
    assert not db.query(InvestmentTrade).filter(
        InvestmentTrade.order_id.in_(["53854719097", "53854719098"])
    ).count()


def test_a_buy_maps_with_fees_folded_into_value(db, stub_api):
    t212_sync.sync_account(db, ACCOUNT, include_history=True)

    buy = db.query(InvestmentTrade).filter_by(order_id="55707565256").one()
    assert buy.side == "BUY"
    assert buy.quantity == 26.0
    assert buy.price == 106.51
    assert buy.isin == "US4581401001"
    assert buy.instrument_currency == "USD"      # instrument, not wallet
    assert buy.currency == "GBP"                 # wallet the trade settled in
    assert buy.value == 2046.36
    assert buy.value_gbp == 2046.36
    assert buy.fees == 3.06                      # stored positive, arrives negative
    assert buy.realised_pl is None               # buys realise nothing


def test_a_sell_stores_positive_quantity_with_direction_in_side(db, stub_api):
    """Quantity arrives negative on a sell; magnitude and direction are split."""
    t212_sync.sync_account(db, ACCOUNT, include_history=True)

    sell = db.query(InvestmentTrade).filter_by(order_id="52752573653").one()
    assert sell.side == "SELL"
    assert sell.quantity == pytest.approx(0.07797034)
    assert sell.quantity > 0
    assert sell.realised_pl == -0.07


def test_value_gbp_is_null_when_the_trade_did_not_settle_in_gbp(db, stub_api):
    """
    The Invest account settled in USD until 2026-05-04. Copying netValue into a
    GBP column would silently misstate those trades by the exchange rate.
    """
    t212_sync.sync_account(db, ACCOUNT, include_history=True)

    usd = db.query(InvestmentTrade).filter_by(order_id="50563557150").one()
    assert usd.currency == "USD"
    assert usd.value == 7414.94                  # faithful to what was reported
    assert usd.value_gbp is None                 # and no invented conversion
    assert usd.realised_pl == 1244.28


def test_gbx_price_is_stored_in_pence_with_the_rate_that_converts_it(db, stub_api):
    """
    London listings quote in pence. The API carries the /100 in fxRate, so the
    price stays as quoted rather than being silently rebased.
    """
    t212_sync.sync_account(db, ACCOUNT, include_history=True)

    gbx = db.query(InvestmentTrade).filter_by(order_id="51000000001").one()
    assert gbx.instrument_currency == "GBX"
    assert gbx.price == 6079.0
    assert gbx.fx_rate == 100
    assert gbx.value_gbp == 295.40
    assert gbx.quantity * gbx.price / gbx.fx_rate == pytest.approx(295.40, abs=0.05)


def test_trades_use_the_same_instrument_symbol_as_holdings(db, stub_api):
    """
    `instrument` is the normalised ticker everywhere in this schema, so trades
    join to holdings on it. The raw ticker and the full name are kept alongside
    rather than replacing it -- normalise_ticker is lossy (SGLNl_EQ -> SGLN).
    """
    t212_sync.sync_account(db, ACCOUNT, include_history=True)

    gbx = db.query(InvestmentTrade).filter_by(order_id="51000000001").one()
    assert gbx.instrument == "SGLN"
    assert gbx.ticker == "SGLNl_EQ"
    assert gbx.instrument_name == "iShares Physical Gold"


def test_resyncing_writes_no_duplicate_trades(db, stub_api):
    first = t212_sync.sync_account(db, ACCOUNT, include_history=True)
    assert first["trades"] == 4

    second = t212_sync.sync_account(db, ACCOUNT, include_history=True)
    assert second["trades"] == 0
    assert db.query(InvestmentTrade).count() == 4


def test_two_fills_of_one_order_are_two_trades(db, stub_api, monkeypatch):
    """
    Keying on the order id would collapse a partially-filled order into a single
    row and lose half the position.
    """
    split = [
        {"order": dict(ORDERS[0]["order"]),
         "fill": {**ORDERS[0]["fill"], "id": 900001, "quantity": 10.0}},
        {"order": dict(ORDERS[0]["order"]),
         "fill": {**ORDERS[0]["fill"], "id": 900002, "quantity": 16.0}},
    ]
    monkeypatch.setattr(t212_sync.Trading212Client, "history_orders",
                        lambda self, **kw: split)

    t212_sync.sync_account(db, ACCOUNT, include_history=True)

    rows = db.query(InvestmentTrade).all()
    assert len(rows) == 2
    assert sorted(r.quantity for r in rows) == [10.0, 16.0]
    assert len({r.external_id for r in rows}) == 2


def test_an_unexecuted_order_does_not_end_the_scan_early(db, stub_api, monkeypatch):
    """
    Re-syncs stop at the first already-stored record. A cancelled order is never
    stored, so if it were allowed to match, one sitting at the top of the
    history would truncate every future backfill.
    """
    t212_sync.sync_account(db, ACCOUNT, include_history=True)

    seen = {}

    def capture(self, max_pages=20, stop_when=None):
        seen["stop_when"] = stop_when
        return ORDERS

    monkeypatch.setattr(t212_sync.Trading212Client, "history_orders", capture)
    t212_sync.sync_account(db, ACCOUNT, include_history=True)

    stop_when = seen["stop_when"]
    assert stop_when(ORDERS[2]) is False          # cancelled -> keep scanning
    assert stop_when(ORDERS[0]) is True           # already stored -> stop


def test_pdf_and_api_trades_do_not_pollute_the_transactions_table(db, stub_api):
    """Trades are not cash flow; analytics must never see a buy as spending."""
    t212_sync.sync_account(db, ACCOUNT, include_history=True)

    descriptions = [t.description for t in db.query(Transaction).all()]
    assert all("Intel" not in d and "Tesla" not in d for d in descriptions)
    assert db.query(Transaction).count() == len(TRANSACTIONS)


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
