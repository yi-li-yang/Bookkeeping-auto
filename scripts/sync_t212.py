"""
Refresh Trading 212 data from the API, without needing the server running.

Called by the /portfolio command when a refresh is asked for or prices are
stale, not by hand. Writes; the read-only counterpart is scripts/portfolio.py.

    python scripts/sync_t212.py [--no-history] [--max-pages N]
"""
import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.db.database import SessionLocal, init_db          # noqa: E402
from backend.integrations import t212_sync                     # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync Trading 212 into the local database.")
    parser.add_argument("--no-history", action="store_true",
                        help="holdings only; skip transactions, dividends and trades")
    parser.add_argument("--max-pages", type=int, default=20,
                        help="page cap per history endpoint (default: 20)")
    parser.add_argument("--full", action="store_true",
                        help="re-scan all history instead of stopping at the newest "
                             "known record; needed to deepen a backfill that a "
                             "previous --max-pages cut short")
    parser.add_argument("--verbose", action="store_true", help="show sync log output")
    args = parser.parse_args()

    # Log to stderr so stdout stays parseable as JSON.
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING, stream=sys.stderr
    )

    init_db()
    db = SessionLocal()
    try:
        result = t212_sync.sync_all(
            db, include_history=not args.no_history, max_pages=args.max_pages,
            full_history=args.full,
        )
    finally:
        db.close()

    json.dump(result, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")
    # A partial sync is a failure worth surfacing: it skews the "latest snapshot
    # date", which is a global max across accounts.
    return 1 if result.get("failed") else 0


if __name__ == "__main__":
    raise SystemExit(main())
