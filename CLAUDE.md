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

Six tables. `transactions` and `investment_holdings` are shared by every
source — API and file ingestion both write to them, which is why the read
endpoints needed no changes when API sync was added.

- `source_files` — one row per ingested file, plus one synthetic row per API
  connector (`file_type='api'`, `filename='trading212:<account_id>'`)
- `transactions` — `external_id` is the stable per-source key that makes
  re-syncing idempotent
- `investment_holdings` — dated snapshots; `report_date` + `account_name`
- `investment_dividends` — unique on `(account_name, reference)`
- `sync_runs` — per-connector outcome: `ok` / `error` / `auth_required`
- `investment_trades` — executed buys and sells from the broker's order
  history; unique per *fill*, not per order

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

### Trade history

`history_orders` backfills `investment_trades`. Five things it has to get right:

1. **The payload is nested**: `{"order": {...}, "fill": {...}}`. The `fill` key
   is *absent* on anything that never executed, so its presence — not the status
   string — is what marks a real trade. A cancelled order still reports the
   quantity it wanted; storing it would invent purchases.
2. **One order can produce several fills**, so rows are keyed on the fill id.
   Keying on the order id silently merges a partially-filled order into one row.
3. **`quantity` is signed** (negative on a sell) but `netValue` is not. Magnitude
   goes in `quantity`, direction in `side`.
4. **`walletImpact.netValue` follows the wallet the trade settled in, which is
   not always GBP.** The Invest account settled in USD until 2026-05-04.
   `value` + `currency` record what was reported; `value_gbp` is filled in only
   when that currency really is GBP, because converting the rest would mean
   inventing a historical FX rate.
5. **Prices are in the instrument's currency, and `GBX` is pence.** `fxRate`
   carries the conversion (100 for GBX), so the price is stored as quoted.

`--full` on `scripts/sync_t212.py` turns off the stop-at-first-known
short-circuit. Routine syncs want that short-circuit, but it also means a
backfill truncated by `max_pages` can never be deepened without it — the second
run would stop at the newest record the first one stored.

Trades are deliberately **not** written to `transactions`: a £5,000 buy is not
spending, and all 13 analytics endpoints read that table.

Trades reconcile against holdings for every position except across a **corporate
action** — NVIDIA's June 2024 10-for-1 split added 2.2965 ISA shares that no
order explains. A cost basis built by summing trades is wrong for affected names.

Cash is stored as a synthetic `CASH` holding, derived as
`totalValue - sum(stored position values)`, so the portfolio total reconciles
exactly. Its `cost_gbp`/`return_gbp` stay NULL so return figures are unaffected.

## Bank accounts

Six UK accounts: Bank of Scotland, Chase, Amex, M&S Bank, Monzo, Klarna.
**Only Monzo is reachable by API.** Everything else is statement import.

This is not an oversight. Post-Brexit the UK sits outside PSD2, and the free
routes to UK open banking have closed:

- **Enable Banking** covers the EEA only -- 30 markets, Austria to Sweden, no
  UK. `backend/integrations/enablebanking.py` is a complete, tested, working
  client that can reach none of these accounts. It is kept dormant, not
  deleted. Nothing imports it.
- **GoCardless / Nordigen**, the old free tier, has closed new signups.
- **Chase UK** serves its interface only to FCA-registered TPPs.
- **Klarna** is a TPP, not an ASPSP -- its Open Banking Platform reads *your*
  bank to fund BNPL. BNPL is not a PSD2 payment account, so there is nothing to
  read. Its instalments already appear on whichever card funds them.

Run `scripts/check_bank_coverage.py` before writing any Enable Banking sync
code. That ordering is the point of the script; ignoring it is how a full
client got built against a provider that cannot reach these accounts.

### Monzo

`backend/integrations/monzo.py` is pure HTTP; `monzo_auth.py` handles token
persistence; `monzo_sync.py` does the mapping and writes.

```bash
MONZO_CLIENT_ID / MONZO_CLIENT_SECRET      # confidential client, or no refresh token
MONZO_REDIRECT_URI=http://localhost:8000/api/banks/monzo/callback
```

Connect at `/api/banks/monzo/connect`, then approve the push notification in
the app. Sync with `POST /api/banks/monzo/sync`.

Five things this integration has to get right:

1. **Full history is available for five minutes after linking**, then only the
   last 90 days, and re-authenticating does not reopen the window. The first
   sync therefore sends no `since` and pulls everything; later syncs are
   incremental. Get this wrong once and the history is unreachable until the
   next consent.
2. **Only confidential clients get refresh tokens.** A non-confidential client
   needs manual re-linking every few hours.
3. **A refresh invalidates the previous refresh token**, so the new pair must be
   persisted before the next call. All reads go through
   `monzo_auth.access_token()` so there is one place that can forget; writes are
   atomic via `os.replace`.
4. **Amounts are minor units and already signed** the way this schema wants
   (negative for money out) -- unlike Trading 212, no sign inference.
5. **Pot transfers and top-ups are categorised deterministically as
   `Transfers`**, bypassing the LLM. They move the user's own money, and the
   analytics layer excludes that category from income and expenses.

Declined transactions carry the amount they would have been; they are skipped,
or a declined GBP500 payment reads as GBP500 of spending. `account_label()`
deliberately ignores Monzo's `description` field, which holds the account
holder's real name on personal accounts.

## Portfolio analysis in Claude Code

Ad-hoc portfolio Q&A and investment research, driven entirely from the chat
panel — no terminal, no server, no dashboard. **One command**, `/portfolio`,
which covers holdings questions, research on a candidate not yet owned, and
refreshing the data; the intent is read from the question rather than declared.

`.claude/skills/portfolio/SKILL.md` holds the grounding and honesty rules and
is where all the behaviour lives; the command is a thin wrapper over it. Two
internal helpers back it, called by Claude and never by hand:

- `scripts/portfolio.py` — read-only queries. Opens SQLite with `mode=ro` and
  refuses anything but a single `SELECT`/`WITH`, so an analysis session cannot
  alter the books. Resolves each account to *its own* latest `report_date`,
  unlike `/api/investments/*` which takes a global max and drops an account
  whose sync failed.
- `scripts/sync_t212.py` — the writer. Works without `npm run start`.

Answers stay in the chat. The database holds real positions and this repo is
public, so nothing with figures in it is ever written to the working tree.

## Ingest files

1. Copy CSV/PDF statements into `data/input/`
2. `backend/watcher.py` polls that folder every 20s and ingests new files
   automatically. Click **Ingest Files** or `curl -X POST /api/ingest` to force
   a run.
3. Files are processed once; re-running skips them. `?force=true` re-does files
   under `INPUT_DIR` and never touches API-sourced rows.

The watcher is a poll, not a filesystem-event library: no extra dependency,
identical behaviour on every filesystem, and `scan_and_ingest` already skips
processed files. Two rules it enforces that the manual path does not:

- **A file is read only after two consecutive polls see it unchanged.** A
  browser download is visible from its first byte, and half a PDF parses into
  plausible nonsense rather than failing.
- **A file whose `status` is `error` is never retried automatically.** Each
  attempt costs an LLM call and the poll is every 20s. `POST /api/ingest` still
  retries it on request.

This reverses commit 77c3d25 ("changed ingestion method to manual only"), which
removed a thread that re-ingested on *every startup*. The watcher reacts to a
new file instead, and `AUTO_INGEST=false` turns it off.

## LLM behaviour

- Column detection: Claude reads CSV headers + sample rows to identify
  date/amount/description columns
- PDF parsing: Claude parses extracted tables/text into structured transactions
- Categorisation: Claude invents category names freely; batched in groups of 50
- API-sourced transactions are categorised deterministically (`confidence=1.0`)
  where the answer is structural, and by the LLM otherwise

**Every LLM feature funnels through `llm_processor._call`**, so the backend is
one switch:

| `LLM_BACKEND` | Bills | Notes |
|---|---|---|
| `api` (default) | Anthropic API, model from `LLM_MODEL` | Cheap per call |
| `claude_code` | Your Claude subscription | Needs the CLI installed |

`claude_code` shells out to `claude -p --output-format json`. It carries **~17k
input tokens of harness overhead per invocation** regardless of prompt size --
the CLI loads its system prompt, tool definitions and CLAUDE.md every time.
Measured locally at $0.18 for a one-word reply. Suits the occasional analysis
call; wasteful for bulk categorisation, which is why the switch exists rather
than a wholesale move.

`LLM_MODEL` defaults to `claude-sonnet-5`. It was `claude-opus-4-6` hardcoded,
which is expensive for what categorisation is: mechanical labelling of short
strings.

`_extract_json` must tolerate both leading and trailing prose. The Claude Code
backend is an agent and adds commentary around the payload even when told not
to, so parsing stops at the end of the first complete JSON value.

## Categorisation rules that are load-bearing

**Internal transfers must not be spending.** Funding a joint account looks
exactly like paying a person -- same `p2p_payment` scheme, and the counterparty
is a person's name. Left to the LLM it gets categorised as whatever the joint
account then pays for. Two transfers of GBP2000 and GBP800 into a joint account
that paid GBP1950 of rent were all filed as `Rent`, reporting GBP4750 of rent in
a month where GBP1950 left the household.

`monzo_sync.deterministic_category()` settles it structurally: if the
counterparty's `account_id` is an account we also sync (`own_account_ids()`,
derived from the `monzo:<id>` SourceFile rows), the money never left.
`recategorise_internal_transfers()` repairs rows stored before the rule existed,
and never touches a row the user has edited.

**Rent is not everything paid to a landlord.** Deposits, holding fees and the
recurring charge all go to the same payee. The categoriser prompt asks for
`Rent` only for the recurring series, with `Rental Deposit` and `Letting Fees`
for one-offs, and flags two payments to the same payee on the same day as a
signal that only one is rent. This needs the transaction `date` in the payload
-- it was being collected and then dropped before reaching the prompt, leaving
the model no way to tell recurring from one-off.

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

**Transfers are excluded by category, not by account type.** All 13
analytics endpoints filter out the `Transfers` category, which is what stops a
brokerage deposit reading as income (it was inflating total income by ~£96k).
Filtering on `account_type` instead would have been wrong -- it would also drop
genuine interest and fees. The cost is that anything miscategorised as a
transfer vanishes from income and spending. `net-worth` and `projection`'s
`current_balance` opt out deliberately: they track balances, where a transfer
really does move money.

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
