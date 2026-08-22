"""
Lightweight additive migrations for SQLite.

Deliberately not Alembic. Everything here is additive and idempotent, so it is
safe to run on every startup: new columns are guarded by PRAGMA table_info,
indexes use IF NOT EXISTS, and the one data migration checks before writing.

Making an existing column nullable is NOT supported and should not be added --
SQLite needs a full table rebuild for that, which is a real risk against a
database holding the only copy of the user's data. Model around it instead.
"""
import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)

# table -> {column: SQL type}. Nullable, additive columns only.
_ADDITIVE_COLUMNS: dict[str, dict[str, str]] = {
    "transactions": {"external_id": "VARCHAR"},
}

_INDEXES: list[str] = [
    "CREATE INDEX IF NOT EXISTS ix_transactions_external_id "
    "ON transactions(external_id)",
    "CREATE INDEX IF NOT EXISTS ix_holdings_date_account "
    "ON investment_holdings(report_date, account_name)",
]

# Existing PDF-sourced rows used short names; API rows use the qualified form.
# Without this the dashboard would show four accounts instead of two and the
# per-account history would break into disconnected series.
_ACCOUNT_RENAMES: dict[str, str] = {
    "Invest": "Trading 212 Invest",
    "Stocks ISA": "Trading 212 Stocks ISA",
}


def _existing_tables(conn) -> set[str]:
    rows = conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='table'")
    ).fetchall()
    return {r[0] for r in rows}


def _existing_columns(conn, table: str) -> set[str]:
    rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return {r[1] for r in rows}


def run_migrations(engine) -> list[str]:
    """Apply pending additive migrations. Returns a list of what changed."""
    applied: list[str] = []

    with engine.begin() as conn:
        tables = _existing_tables(conn)

        for table, columns in _ADDITIVE_COLUMNS.items():
            if table not in tables:
                continue  # create_all will have made it with the column already
            present = _existing_columns(conn, table)
            for column, sql_type in columns.items():
                if column in present:
                    continue
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}"))
                applied.append(f"add column {table}.{column}")

        for statement in _INDEXES:
            table = statement.split(" ON ")[1].split("(")[0].strip()
            if table not in tables:
                continue
            conn.execute(text(statement))

        if "investment_holdings" in tables:
            for old, new in _ACCOUNT_RENAMES.items():
                count = conn.execute(
                    text(
                        "SELECT COUNT(*) FROM investment_holdings "
                        "WHERE account_name = :old"
                    ),
                    {"old": old},
                ).scalar()
                if not count:
                    continue
                conn.execute(
                    text(
                        "UPDATE investment_holdings SET account_name = :new "
                        "WHERE account_name = :old"
                    ),
                    {"old": old, "new": new},
                )
                applied.append(f"rename account {old!r} -> {new!r} ({count} rows)")

    if applied:
        logger.info("Migrations applied: %s", "; ".join(applied))
    return applied
