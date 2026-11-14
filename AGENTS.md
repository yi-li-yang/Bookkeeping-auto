# Rules for agents working on bookkeeping-auto

This repository is the backend for Yili's finance agents in Claude Duet. Agents
run its code in their own cloud sessions; the data they handle never comes back
here.

## Never commit

- Statements, exports, bills, payslips or any file read from mail or Drive.
- Amounts, balances, holdings, card limits, account numbers, card numbers or sort codes, including in tests, fixtures, examples, commit messages and PR descriptions. Tests use made-up lines such as `EXAMPLE CAFE`.
- Keys, tokens, passwords or `.env` files. Secrets live in Cloudflare (the connector), GitHub secrets and Yili's local `.env`.

`.gitignore` blocks `*.pdf`, `*.csv`, `*.xlsx`, `*.ofx`, `*.qif` and `*.ledger.json`. Do not force-add any of them.

## How changes land

- Work on a branch and open a draft pull request. Yili merges. Never push to `main`.
- Run `python -m pytest backend/tests -q` before pushing. GitHub Actions runs the same on every pull request.
- Never rewrite history, change repository settings or visibility.

## Where things are

| Need | Where |
|---|---|
| Turn a statement file into rows | `python -m backend.agent_tools.statement_to_ledger FILE --source amex --statement 2026-10` (see the file's docstring) |
| Save or read statement and bill rows | `save_ledger_entries`, `get_ledger_entries`, `get_ledger_summary` on the yili-mcp-connector connector |
| Trading 212, Monzo, pensions | the yili-mcp-connector connector (read only) |
| The card list (limits, 0% end dates, fees) | the `cards` dataset of the Bookkeeping auto page in Yili's finance channel in Claude Duet (see "Updating the card list") |
| What each routine does | `agents/routines.md` |
| The local web app | `README.md` and `CLAUDE.md` |

## Updating the card list

The card list lives in the Bookkeeping auto page, so Yili sees and changes it in Claude Duet. Yili tells Claude the values in the chat; nothing is copied from emails.

1. Read the page's `datasets/cards` document. Its `source.url` names the current file (`/_blob/<id>`); read that file by its id.
2. Change only the values Yili gave. Keep the columns: `name, provider, type, credit_limit_gbp, purchase_0pct_end, balance_transfer_0pct_end, annual_fee_gbp, fee_renews_on, fx_fee_pct, role, notes`. Dates are `YYYY-MM-DD`; amounts are plain numbers.
3. Upload the new CSV to the page as an asset, then `update` `datasets/cards` with the new `source.url` and `updated: {at, by}`.
4. Never put card numbers, account numbers or security codes in the file.

## Working with statements

1. Download the attachment into the session's scratch space, never into the repository.
2. Run `statement_to_ledger`. Card statements usually print charges as positive numbers: pass `--charges-positive`. For a PDF, pass `--year`.
3. Check the rows against the statement's own total. If the parser missed lines, read the text with `--text` and fix the rows by hand.
4. Add a `category` to each row, then save with `save_ledger_entries`. Use `replace: true` when saving a corrected statement again.
5. Delete the downloaded file from the session when done.
