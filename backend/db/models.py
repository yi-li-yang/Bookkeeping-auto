from datetime import datetime
from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship
from db.database import Base


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

    source_file = relationship("SourceFile", back_populates="transactions")


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
