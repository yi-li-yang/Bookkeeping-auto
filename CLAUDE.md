# Bookkeeping Auto — CLAUDE.md

## Project overview

Personal finance automation tool: ingest bank/credit card/investment statements (CSV + PDF), use Claude to auto-categorise transactions, store in SQLite, visualise in a React dashboard.

## Tech stack

- **Backend**: Python 3.12, FastAPI, SQLAlchemy, pdfplumber, pandas
- **Frontend**: React 18, TypeScript, Vite, Recharts, Axios
- **LLM**: Anthropic Claude (`claude-opus-4-6`) via `anthropic` SDK
- **Database**: SQLite at `data/bookkeeping.db`
- **Deployment**: Docker + docker-compose

## Key directories

- `backend/` — FastAPI app
- `backend/ingestion/` — file scanning, CSV/PDF parsing, LLM processing
- `backend/api/` — route handlers
- `backend/db/` — SQLAlchemy models and session
- `frontend/src/` — React app
- `frontend/src/components/` — dashboard widgets
- `data/input/` — **drop statement files here** before running ingest

## How to run (Docker)

```bash
cp .env.example .env
# Edit .env and add your ANTHROPIC_API_KEY
docker-compose up --build
```

- Dashboard: <http://localhost:3000>
- API docs: <http://localhost:8000/docs>

## Ingest files

1. Copy CSV/PDF bank statements into `data/input/`, or use API to retrieve.
2. In the dashboard, click **⬆ Ingest Files**
   — or via CLI: `curl -X POST http://localhost:8000/api/ingest`
3. Files are processed only once; re-running skips already-processed files

## Implemented tiers

**Tier 1 — Overview**: spending treemap, income vs expenses bar chart, cash flow waterfall, transaction table with inline category editing.

**Tier 2 — Trends**: category spend trends with anomaly detection (1.5σ), recurring expense detection, spending velocity (cumulative daily spend vs prior month).

**Tier 3 — Investments**: net worth over time as stacked area chart by account type (bank / investment / credit_card), current balance breakdown.

**Tier 4 — Insights**: LLM monthly summary (Claude generates plain-English report on demand), statistical anomaly detection (z-score > 2), cash flow projection (3/6/12 months), what-if scenario sliders.

**Tier 5 — Summary**: annual review with key stats and top-category bar chart, data quality dashboard (coverage %, confidence, user corrections), month-over-month comparison chart.

## LLM behaviour

- Column detection: Claude reads CSV headers + sample rows to identify date/amount/description columns
- PDF parsing: Claude parses extracted tables/text into structured transactions
- Categorisation: Claude freely invents category names; batched in groups of 50
- Model: `claude-opus-4-6`
