from datetime import datetime
from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import relationship
from backend.db.database import Base


class SourceFile(Base):
    __tablename__ = "source_files"

    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String, unique=True, nullable=False)
    file_type = Column(String, nullable=False)  # csv / pdf
    processed_at = Column(DateTime, default=datetime.utcnow)
    status = Column(String, default="pending")  # pending / done / error
    row_count = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)

    transactions = relationship("Transaction", back_populates="source_file")
    holdings = relationship("InvestmentHolding", back_populates="source_file")
    dividends = relationship("InvestmentDividend", back_populates="source_file")
    trades = relationship("InvestmentTrade", back_populates="source_file")


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    date = Column(String, nullable=False)        # stored as ISO date string YYYY-MM-DD
    description = Column(Text, nullable=False)
    amount = Column(Float, nullable=False)        # negative = expense, positive = income
    category = Column(String, nullable=True)
    account_name = Column(String, nullable=True)
    account_type = Column(String, nullable=True)  # credit_card / bank / investment
    source_file_id = Column(Integer, ForeignKey("source_files.id"), nullable=False)
    confidence = Column(Float, nullable=True)
    is_user_edited = Column(Boolean, default=False)
    raw_row = Column(Text, nullable=True)         # JSON of original parsed row
    external_id = Column(String, nullable=True, index=True)  # e.g. "t212:invest:<ref>"

    source_file = relationship("SourceFile", back_populates="transactions")


class CreditCard(Base):
    """
    Stores manually-entered metadata for each credit card (e.g. limit, promo date, FX fee).
    account_name should match the account_name stored on Transaction rows for this card.
    """
    __tablename__ = "credit_cards"

    id = Column(Integer, primary_key=True, index=True)
    account_name = Column(String, unique=True, nullable=False)   # must match Transaction.account_name
    card_name = Column(String, nullable=True)                    # display name e.g. "Chase Sapphire Preferred"
    credit_limit = Column(Float, nullable=True)
    promotion_end_date = Column(String, nullable=True)           # YYYY-MM-DD
    fx_fee_pct = Column(Float, nullable=True)                    # e.g. 3.0 = 3%, 0.0 = no fee
    annual_fee = Column(Float, nullable=True)
    notes = Column(Text, nullable=True)


class InvestmentHolding(Base):
    """
    A snapshot of a single portfolio position from an investment statement PDF.
    Each row is one instrument at one point in time (report_date).
    Multiple snapshots over time enable history tracking.
    """
    __tablename__ = "investment_holdings"

    id = Column(Integer, primary_key=True, index=True)
    report_date = Column(String, nullable=False)       # YYYY-MM-DD — date of the statement
    account_name = Column(String, nullable=False)      # e.g. "Invest", "Stocks ISA"
    instrument = Column(String, nullable=False)        # ticker symbol e.g. "AAPL"
    isin = Column(String, nullable=True)
    currency = Column(String, nullable=True)           # original currency e.g. "USD"
    quantity = Column(Float, nullable=True)
    avg_price = Column(Float, nullable=True)           # avg cost per share (original currency)
    current_price = Column(Float, nullable=True)       # current price (original currency)
    fx_rate = Column(Float, nullable=True)             # FX rate to GBP at report date
    cost_gbp = Column(Float, nullable=True)            # cost basis in GBP
    value_gbp = Column(Float, nullable=False)          # current market value in GBP
    return_gbp = Column(Float, nullable=True)          # unrealised return in GBP
    source_file_id = Column(Integer, ForeignKey("source_files.id"), nullable=False)

    source_file = relationship("SourceFile", back_populates="holdings")


class InvestmentDividend(Base):
    """A single dividend or interest payment, pulled from a broker API."""
    __tablename__ = "investment_dividends"
    __table_args__ = (
        UniqueConstraint("account_name", "reference", name="uq_dividend_account_ref"),
    )

    id = Column(Integer, primary_key=True, index=True)
    account_name = Column(String, nullable=False)
    instrument = Column(String, nullable=True)
    isin = Column(String, nullable=True)
    ticker = Column(String, nullable=True)
    dividend_type = Column(String, nullable=True)      # ORDINARY / INTEREST / ...
    paid_on = Column(String, nullable=False)           # YYYY-MM-DD
    amount = Column(Float, nullable=False)             # in account currency
    currency = Column(String, nullable=True)
    quantity = Column(Float, nullable=True)
    gross_per_share = Column(Float, nullable=True)
    reference = Column(String, nullable=True)
    source_file_id = Column(Integer, ForeignKey("source_files.id"), nullable=False)

    source_file = relationship("SourceFile", back_populates="dividends")


class InvestmentTrade(Base):
    """
    One executed fill from a broker's order history.

    Deliberately not stored in `transactions`: a GBP5,000 buy is not spending,
    and every analytics endpoint reads that table.

    Two things the Trading 212 payload forces on this schema:

    * A single order can produce several fills, so the fill id -- not the order
      id -- is what makes a row unique.
    * `walletImpact.netValue` is denominated in the wallet the trade settled in,
      which is not always GBP. The Invest account settled in USD until
      2026-05-04. `value` therefore holds the figure as reported alongside its
      `currency`, and `value_gbp` is filled in only when that currency really is
      GBP. Converting the rest would mean inventing a historical FX rate.
    """
    __tablename__ = "investment_trades"

    id = Column(Integer, primary_key=True, index=True)
    account_name = Column(String, nullable=False, index=True)
    instrument = Column(String, nullable=False)        # ticker symbol, as investment_holdings
    instrument_name = Column(String, nullable=True)    # full name, e.g. "Intel"
    ticker = Column(String, nullable=True)             # raw, e.g. BTIPl_EQ
    isin = Column(String, nullable=True, index=True)
    side = Column(String, nullable=False)              # BUY / SELL
    quantity = Column(Float, nullable=False)           # absolute; direction is `side`
    price = Column(Float, nullable=True)               # in instrument currency (GBX = pence)
    instrument_currency = Column(String, nullable=True)
    fx_rate = Column(Float, nullable=True)             # instrument currency -> wallet currency
    value = Column(Float, nullable=False)              # net cash impact, fees included
    currency = Column(String, nullable=True)           # wallet currency of `value`
    value_gbp = Column(Float, nullable=True)           # only when currency == GBP
    fees = Column(Float, nullable=True)                # positive; charged in `currency`
    realised_pl = Column(Float, nullable=True)         # sells only, in `currency`
    executed_at = Column(String, nullable=False)       # full ISO datetime, not truncated
    status = Column(String, nullable=True)             # always FILLED today
    order_id = Column(String, nullable=True)
    external_id = Column(String, nullable=True, index=True)
    source_file_id = Column(Integer, ForeignKey("source_files.id"), nullable=False)
    raw_row = Column(Text, nullable=True)

    source_file = relationship("SourceFile", back_populates="trades")


class SyncRun(Base):
    """
    One connector sync attempt. Backs the Connections panel and lets an expired
    bank consent surface as `auth_required` instead of failing silently.
    """
    __tablename__ = "sync_runs"

    id = Column(Integer, primary_key=True, index=True)
    connector = Column(String, nullable=False, index=True)  # trading212:invest, bank:<uid>
    started_at = Column(DateTime, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)
    status = Column(String, nullable=False)                 # ok / error / auth_required
    items_written = Column(Integer, default=0)
    message = Column(Text, nullable=True)
