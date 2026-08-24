# Bookkeeping Auto — CLAUDE.md

## Project overview

Personal finance automation: pull holdings and transactions from broker APIs,
ingest bank/credit-card statements (CSV + PDF), use Claude to auto-categorise,
store in SQLite, visualise in a React dashboard.

Single-user, runs locally. No Docker.

## Tech stack

- **Backend**: Python 3.11+, FastAPI, SQLAlchemy, httpx, pdfplumber, pandas
- **Frontend**: React 18, TypeScript, Vite, Recharts, Axios
- **LLM**: Anthropic Claude (`claude-opus-4-6`) via the `anthropic` SDK
- **Database**: SQLite at `data/bookkeeping.db`
- **Run model**: one FastAPI process serves the API and the built SPA on `:8000`

## How to run

```bash
npm run setup     # pip install -r backend/requirements.txt + npm install in frontend/
npm run dev       # uvicorn --reload :8000 + vite :5173  -> use http://localhost:5173
npm run start     # build the SPA, then serve UI + API together on http://localhost:8000
npm test          # pytest backend/tests
```

`npm run dev` is for editing (hot reload on both sides, Vite proxies `/api` to
:8000). `npm run start` is for everyday use — one process, one URL.

Requires Python 3.11+ and Node 20+ on the host, and a `.venv/` at the repo root
(`python -m venv .venv`). `scripts/py.mjs` resolves the venv interpreter
cross-platform, so package.json scripts never hardcode a path.

## Key directories

- `backend/config.py` — **all** env vars and paths; nothing else reads `os.environ`
- `backend/integrations/` — broker/bank API clients and their sync services
- `backend/ingestion/` — file scanning, CSV/PDF parsing, LLM processing
- `backend/api/` — route handlers (thin; logic lives below them)
- `backend/db/` — SQLAlchemy models, session, and `migrations.py`
- `backend/tests/` — pytest, mocked HTTP via `httpx.MockTransport`
- `frontend/src/components/` — dashboard widgets
- `data/input/` — drop statement files here before running ingest

## Architecture notes that are easy to get wrong

**Imports are package-qualified.** `from backend.db...`, never `from db...`.
The app must import from the repo root; `uvicorn backend.main:app` depends on it.

**The SPA mount goes last.** `app.mount("/", StaticFiles(...))` in `main.py` is
registered after every router. Starlette matches in registration order, so
moving it earlier makes every `/api/*` route 404.

**Config is centralised.** Add new env vars to `backend/config.py`, not inline.
Paths are repo-relative; there is no `/app/...` anywhere.

**Migrations are additive only.** `backend/db/migrations.py` runs on every
startup: `PRAGMA table_info` guards new columns, indexes use `IF NOT EXISTS`.
Do not add anything needing a table rebuild (e.g. making a column nullable) —
SQLite requires copy/drop/rename for that, which risks the only copy of the data.

## Database

Five tables. `transactions` and `investment_holdings` are shared by every
source — API and file ingestion both write to them, which is why the read
endpoints needed no changes when API sync was added.

- `source_files` — one row per ingested file, plus one synthetic row per API
  connector (`file_type='api'`, `filename='trading212:<account_id>'`)
- `transactions` — `external_id` is the stable per-source key that makes
  re-syncing idempotent
- `investment_holdings` — dated snapshots; `report_date` + `account_name`
- `investment_dividends` — unique on `(account_name, reference)`
- `sync_runs` — per-connector outcome: `ok` / `error` / `auth_required`

## Trading 212 integration

`backend/integrations/trading212.py` is pure HTTP (no SQLAlchemy, so it is
testable against a mock transport). `t212_sync.py` does the persistence.

Env vars — one key pair **per account**:

```bash
T212_API_KEY_INVEST / T212_API_SECRET_INVEST
T212_API_KEY_STOCKS_ISA / T212_API_SECRET_STOCKS_ISA
T212_ENV=live            # or demo; keys are environment-specific
```

Sync with `POST /api/investments/sync`. Six things this integration learned the
hard way, all of which have regression tests:

1. **Rate limits are per account, not per endpoint.** Pacing must include a
   global floor between *any* two requests (1.4s), or hitting four different
   endpoints in a row is a burst and returns 403.
2. **`nextPagePath` is inconsistent.** `history/dividends` returns a full path;
   `history/transactions` returns a bare query string. Handle both.
   `history/transactions` also rejects a cursor on the first request.
3. **Amounts arrive unsigned.** Deposits, interest and withdrawals are all
   positive. Direction comes from the type — see `_INCOME_TYPES` /
   `_EXPENSE_TYPES`. Unknown types keep the API's sign and log a warning rather
   than being guessed at.
4. **Row identity is the Trading 212 account id**, never the env var name.
   Trading 212 does not tell you which account a key belongs to, so the variable
   name is only a guess; keying on it means renaming a variable re-imports the
   entire history as new.
5. **Which account a key belongs to is detected**, by overlapping live position
   ISINs against stored snapshots, requiring both a strong match and a clear
   margin. The env var name is a fallback, not the authority.
6. **Tickers need normalising.** London listings are `BTIPl_EQ`, not
   `SYMBOL_EXCHANGE_TYPE`, so `split("_")[0]` yields `BTIPl` where statements
   say `BTIP`. Only lowercase suffixes are stripped, so `GOOGL` keeps its `L`.
   Trading 212 also still returns `FB` for Meta, so a name already recorded
   against an ISIN wins.

Cash is stored as a synthetic `CASH` holding, derived as
`totalValue - sum(stored position values)`, so the portfolio total reconciles
exactly. Its `cost_gbp`/`return_gbp` stay NULL so return figures are unaffected.

## Ingest files

1. Copy CSV/PDF statements into `data/input/`
2. Click **Ingest Files** in the dashboard, or `curl -X POST http://localhost:8000/api/ingest`
3. Files are processed once; re-running skips them. `?force=true` re-does files
   under `INPUT_DIR` and never touches API-sourced rows.

## LLM behaviour

- Column detection: Claude reads CSV headers + sample rows to identify
  date/amount/description columns
- PDF parsing: Claude parses extracted tables/text into structured transactions
- Categorisation: Claude invents category names freely; batched in groups of 50
- API-sourced transactions are categorised deterministically (`confidence=1.0`),
  not by the LLM

## Implemented tiers

**Tier 1 — Overview**: spending treemap, income vs expenses, cash flow
waterfall, transaction table with inline category editing.

**Tier 2 — Trends**: category spend trends with anomaly detection (1.5σ),
recurring expense detection, spending velocity.

**Tier 3 — Investments**: holdings, performance and allocation from the latest
snapshot; portfolio history; dividends; net worth by account type.

**Tier 4 — Insights**: LLM monthly summary, statistical anomaly detection
(z-score > 2), cash flow projection (3/6/12 months), what-if sliders.

**Tier 5 — Summary**: annual review, data quality dashboard, month-over-month
comparison.

## Known issues

**Analytics are not account-type aware.** `income-vs-expenses`,
`annual-summary`, `projection` and `monthly-summary` sum `Transaction.amount`
without filtering `account_type`, so money transferred *into* an investment
account reads as income and inflates the savings rate. With both Trading 212
accounts synced this is currently ~£96k of phantom income. Interest and
dividends are genuine income and should stay.

**Two notions of investment balance.** `/api/analytics/net-worth` derives the
investment line from cumulative transactions, entirely separate from the
`investment_holdings` valuation path. They do not reconcile, and once deposits
sync they double-count. Net worth should prefer real holdings valuations where
they exist and fall back to the cumsum otherwise.

**`frontend/src/components/NetWorth.tsx` is orphaned** — it works and is backed
by `/api/analytics/net-worth`, but `App.tsx` never imports it.

## Security

`data/bookkeeping.db` and `.env` are gitignored and must stay that way — the
database holds real transaction descriptions and holdings, and this repo is
public. `docs/migrate_creditcard_management` is gitignored for the same reason
(real credit limits). Never commit real financial data or credentials.
