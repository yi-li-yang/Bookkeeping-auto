"""
Load the credit-card roster from data/cards.json into the database.

The roster holds real credit limits, so it lives in a gitignored JSON file
rather than in this script. This script is committed; the data is not.

Idempotent: matches on account_name and updates in place, so re-running after
editing cards.json applies the edits instead of duplicating rows.

Usage:
    .venv/Scripts/python.exe scripts/seed_cards.py
    .venv/Scripts/python.exe scripts/seed_cards.py --dry-run
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import settings          # noqa: E402
from backend.db.database import SessionLocal, init_db  # noqa: E402
from backend.db.models import CreditCard     # noqa: E402

# Columns CreditCard actually has. `role` and `unverified` are carried in
# cards.json ahead of the schema gaining them, and folded into notes until it
# does -- better than dropping them silently on load.
_COLUMNS = (
    "account_name", "card_name", "credit_limit",
    "promotion_end_date", "fx_fee_pct", "annual_fee", "notes",
)


def _roster_path() -> Path:
    return settings.repo_root / "data" / "cards.json"


def _to_row(card: dict) -> dict:
    row = {k: card.get(k) for k in _COLUMNS}

    extras = []
    if card.get("role"):
        extras.append(f"Role: {card['role']}.")
    if card.get("unverified"):
        extras.append(f"UNVERIFIED fields: {', '.join(card['unverified'])}.")
    if extras:
        row["notes"] = " ".join(extras + ([row["notes"]] if row["notes"] else []))
    return row


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    path = _roster_path()
    if not path.exists():
        print(f"No roster at {path}.")
        print("Create it with a {\"cards\": [...]} structure. It is gitignored "
              "because it holds real credit limits.")
        return 2

    cards = json.loads(path.read_text(encoding="utf-8")).get("cards") or []
    if not cards:
        print(f"{path} contains no cards.")
        return 2

    init_db()
    db = SessionLocal()
    created = updated = 0
    try:
        for card in cards:
            name = card.get("account_name")
            if not name:
                print(f"  SKIP: a card has no account_name: {card.get('card_name')!r}")
                continue

            row = _to_row(card)
            existing = db.query(CreditCard).filter_by(account_name=name).one_or_none()

            if existing is None:
                print(f"  + {name:32} limit {row['credit_limit']}")
                created += 1
                if not args.dry_run:
                    db.add(CreditCard(**row))
            else:
                changed = [
                    k for k in _COLUMNS if getattr(existing, k) != row[k]
                ]
                if changed:
                    print(f"  ~ {name:32} updating {', '.join(changed)}")
                    updated += 1
                    if not args.dry_run:
                        for k in _COLUMNS:
                            setattr(existing, k, row[k])
                else:
                    print(f"  = {name:32} unchanged")

        if args.dry_run:
            print(f"\nDry run: would create {created}, update {updated}.")
            db.rollback()
        else:
            db.commit()
            print(f"\n{created} created, {updated} updated, "
                  f"{db.query(CreditCard).count()} cards total.")
    finally:
        db.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
