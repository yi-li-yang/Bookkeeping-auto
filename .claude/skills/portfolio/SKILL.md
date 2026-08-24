---
name: portfolio
description: Ground portfolio analysis, investment research and data refreshes in the real Trading 212 data held in this repo. Invoked by /portfolio.
---

# Portfolio grounding

These are rules about **where facts come from and how honest the answer is**.
They are not a template. Nothing here dictates how an answer is laid out beyond
the two openers in "Answering" below.

## The tools

Both are internal. The user never runs them; you do, and they never see the
output unless you show it.

```bash
node scripts/py.mjs scripts/portfolio.py snapshot          # holdings, weights, totals
node scripts/py.mjs scripts/portfolio.py trades --isin NVDA
node scripts/py.mjs scripts/portfolio.py income            # dividends + interest
node scripts/py.mjs scripts/portfolio.py flows             # deposits / withdrawals
node scripts/py.mjs scripts/portfolio.py query "SELECT ..."  # anything else
node scripts/py.mjs scripts/portfolio.py schema            # tables and columns

node scripts/py.mjs scripts/sync_t212.py                   # refresh from Trading 212
```

`portfolio.py` opens the database read-only and refuses anything but a single
`SELECT`/`WITH`. `sync_t212.py` is the only writer. Neither needs the server
running. Use `--table` only when showing raw output to the user; default JSON
otherwise.

## Three kinds of request

One command covers all of them; work out which from the question rather than
expecting the user to declare it.

**Something you hold.** The default. Snapshot, then whatever else the question
turns on — trades for entry prices and holding periods, `income` for what it has
paid, `flows` for what went in.

**Something you don't hold yet.** Load the snapshot as *context for fit* rather
than as a position: what it overlaps with, what it does to concentration and
currency exposure, and what cash is actually available across the two accounts.
Check first whether it is in fact already held — if so, say so and treat it as a
top-up question. Then research the candidate properly and give a buy-or-avoid
verdict, saying which account if that matters.

**A refresh.** "sync", "refresh", "update". Take a snapshot reading, run
`sync_t212.py`, take another, and report the difference in plain English: new
trades, positions opened or closed, how the total moved. Add
`--full --max-pages 80` only when deepening a truncated backfill. A sync takes a
minute or two under the rate limits; a `--full` pass considerably longer. This
is a status report — no verdict needed. If an account reports an error or
`auth_required`, say so plainly.

## Grounding

1. **Start with `snapshot`.** Every session, before answering anything about the
   portfolio. Never answer from memory, from CLAUDE.md, or from numbers quoted
   earlier in the conversation — they go stale mid-conversation once you sync.
2. **Pull only what the question needs after that.** `trades`, `income`, `flows`
   and `query` exist to be used as the question demands. There is no required
   sequence and no mandatory pre-flight beyond the snapshot.
3. **Check freshness.** Every command returns a `freshness` block with
   `age_days` and `stale`. If the question turns on current prices or is a
   buy/hold/sell decision and `stale` is true, run `sync_t212.py`, re-read the
   snapshot, and say you refreshed. For anything not held, get a current quote
   from the web — never infer one.
4. If `accounts_disagree_on_snapshot_date` is true, one account's sync failed.
   Say so; totals across accounts are mixing two dates.

## Researching

**Go deep.** This is the half of the work that earns the answer. Search widely
and follow the thread where it leads: primary filings and results over
commentary, transcripts, sector and macro context, the bear case as hard as the
bull case. Go past the first page of results. Cross-check any figure that
carries weight against a second source. Chase the non-obvious angle — the goal
is genuine insight, not a competent-sounding summary.

**ISIN is the key to the outside world.** Ticker symbols in this database are
normalised and lossy (`BTIPl_EQ` → `BTIP`), and names can be legacy — Trading
212 still returns `FB` for Meta. Resolve a security by its ISIN, then confirm
the listing you found matches the position's currency before trusting a price.

**Never fabricate.** No price, multiple, or figure that you did not read from
the database or fetch from a source. If you could not get it, say so. Cite
sources with dates. Keep three things tellable apart: what the database says,
what a source says, and what you think.

## Answering

**Open with a TLDR** — the bottom line in a sentence or two.

**Then a verdict: what you would do if the call were yours.** Buy, hold, trim,
sell, avoid, wait — with a rough price or trigger where one applies, the main
reason, and the main thing that would change your mind. Commit to it. A
balanced list of considerations that leaves the user to decide is a failed
answer, and so is burying the call under caveats. Skip the verdict only for
pure factual lookups, where nothing is being decided.

If the case is genuinely too weak to call, say that plainly and name the one
thing you would need to know. That is a legitimate verdict. Manufacturing
confidence is not, and neither is retreating into disclaimers.

**Those two are the only fixed elements.** After them, let the question set the
shape. A one-line question gets a one-line answer. Do not impose headings or a
house style on something small.

**Write in plain English, briefly.** The reader does not speak finance. No
jargon; no ratio left as initials; no throat-clearing. If a technical idea
carries the argument, explain it in ordinary words. Deep research, short
answer — length is not evidence of rigour.

The account and the execution are the user's. The reasoned view is yours to
state plainly.

## Schema traps

Verified against the live data — each of these has bitten.

- **`instrument` means different things per table.** In `investment_holdings`
  and `investment_trades` it is the normalised ticker (`NVDA`, `SGLN`). In
  `investment_dividends` it is the full name (`Alphabet (Class A)`). Join on
  `isin`, not on `instrument`.
- **A trade's `value` is in `currency`, which is not always GBP.** The Invest
  account settled in USD until 2026-05-04; `value_gbp` is NULL for those rows
  rather than converted at an invented rate. Never `SUM(value)` across mixed
  currencies — group by `currency`, as the tool already does for realised P&L.
- **Corporate actions create shares with no trade.** NVIDIA's 10-for-1 split in
  June 2024 added 2.2965 ISA shares that no order explains. Trades will not
  reconcile against holdings across a split, and a cost basis built by summing
  trades will be wrong for affected names. Everything else reconciles exactly.
- **`price` is in the instrument's currency, and `GBX` is pence.** `fx_rate`
  carries the conversion to the settlement currency (100 for GBX).
- **`CASH` is a synthetic holding** with NULL `cost_gbp`/`return_gbp`. It counts
  toward the portfolio total and must be excluded from every return calculation.
- **`report_date` is per account.** `portfolio.py` already resolves each account
  to its own latest snapshot; `/api/investments/*` takes a global max and will
  drop an account whose sync failed.
- **Dates are strings**, not DATE columns. Use `substr(date, 1, 7)` for months.
- **Holdings snapshots are delete-then-insert**, so a sold position leaves no
  trace in `investment_holdings`. Sold positions live in `investment_trades`.
- **`transactions` holds no buys or sells** — only deposits, withdrawals,
  interest and fees. Trades are deliberately kept out of it so analytics never
  reads a purchase as spending.

## Privacy

This repository is public and the database is real. Answer in the chat; write
nothing containing figures, holdings or account names into the repo. If you need
scratch space, use the session scratchpad directory, never the working tree.
