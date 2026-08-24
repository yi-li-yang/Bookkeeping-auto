"""
Check which of your banks Enable Banking can actually reach.

This is the gate on the rest of the bank integration: anything not covered
stays on the manual statement-drop path, so it is worth knowing before any
sync code is written.

Usage:
    .venv/Scripts/python.exe scripts/check_bank_coverage.py
    .venv/Scripts/python.exe scripts/check_bank_coverage.py --country GB --all
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import settings  # noqa: E402
from backend.integrations import enablebanking as eb  # noqa: E402

# The accounts this user actually holds. Matched case-insensitively as
# substrings, since Enable Banking's names carry legal-entity suffixes.
WANTED = {
    "Monzo": ["monzo"],
    "Bank of Scotland": ["bank of scotland"],
    "Chase UK": ["chase"],
    "American Express": ["american express", "amex"],
    "M&S Bank": ["m&s", "marks and spencer", "marks & spencer"],
    "Klarna": ["klarna"],
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--country", default="GB")
    parser.add_argument("--psu-type", default="personal")
    parser.add_argument("--all", action="store_true", help="list every bank found")
    args = parser.parse_args()

    if not eb.is_configured(settings):
        print("Enable Banking is not configured yet.\n")
        print("  1. Sign up (free, self-serve): https://enablebanking.com/sign-in/")
        print("  2. Control Panel -> API applications -> register an application")
        print("     Choose Restricted Production and set the redirect url to:")
        print(f"       {settings.eb_redirect_uri}")
        print("  3. Download the .pem private key and save it to:")
        print(f"       {settings.eb_private_key_path}")
        print("  4. Add the application id to .env:")
        print("       EB_APPLICATION_ID=<uuid from the control panel>")
        print("\nThen re-run this script.")
        return 2

    with eb.client_from_settings(settings) as client:
        banks = client.aspsps(country=args.country, psu_type=args.psu_type)

    print(f"Enable Banking reaches {len(banks)} {args.country} "
          f"{args.psu_type} institutions.\n")

    names_lower = [(b.name, b.name.lower()) for b in banks]
    covered = missing = 0

    print(f"  {'your account':22} {'status':10} matched as")
    print("  " + "-" * 66)
    for label, needles in WANTED.items():
        hits = [orig for orig, low in names_lower
                if any(n in low for n in needles)]
        if hits:
            covered += 1
            print(f"  {label:22} {'COVERED':10} {', '.join(hits[:3])}")
        else:
            missing += 1
            print(f"  {label:22} {'not found':10} -> stays on statement import")

    print(f"\n  {covered} covered, {missing} not found")

    if args.all:
        print(f"\nAll {args.country} institutions:")
        for b in sorted(banks, key=lambda x: x.name):
            print(f"  {b.name}")

    return 0 if missing == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
