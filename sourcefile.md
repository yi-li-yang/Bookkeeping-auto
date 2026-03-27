# Automated Statement Downloader — Monzo & Trading 212

Try to automate: Monzo, Chase, Trading212

## Project Overview

Build a Python CLI tool that automatically downloads transaction data / statements from APIs, outputting structured CSV files to a local directory.

The tool should be runnable on-demand or via cron. It must handle auth token persistence, refresh logic, rate limits, and the 90-day SCA restriction on Monzo gracefully.

---

## Architecture

```
statement-downloader/
├── pyproject.toml              # Project metadata, dependencies (use uv or pip)
├── .env.example                # Template for secrets
├── .env                        # NEVER committed — holds real secrets
├── config.yaml                 # Non-secret runtime config (output dir, date ranges, etc.)
├── src/
│   ├── __init__.py
│   ├── cli.py                  # Click-based CLI entry point
│   ├── config.py               # Loads .env + config.yaml, validates
│   ├── monzo/
│   │   ├── __init__.py
│   │   ├── auth.py             # OAuth2 flow, token storage, refresh
│   │   ├── client.py           # API client — accounts, transactions
│   │   └── exporter.py         # Transforms raw txns → CSV statement
│   ├── trading212/
│   │   ├── __init__.py
│   │   ├── auth.py             # Basic auth header construction
│   │   ├── client.py           # API client — history, exports
│   │   └── exporter.py         # Polls for CSV, downloads
│   └── utils.py                # Shared helpers: dates, file I/O, retry logic
├── data/                       # Default output directory for downloaded statements
│   ├── monzo/
│   └── trading212/
├── tokens/                     # Persisted Monzo OAuth tokens (gitignored)
│   └── monzo_token.json
└── tests/
    ├── test_monzo_client.py
    ├── test_t212_client.py
    └── conftest.py
```

---

## Dependencies

```toml
[project]
name = "statement-downloader"
requires-python = ">=3.11"
dependencies = [
    "httpx>=0.27",          # Async-capable HTTP client (prefer over requests)
    "click>=8.1",           # CLI framework
    "pydantic>=2.0",        # Config & response model validation
    "pydantic-settings>=2", # .env loading
    "pyyaml>=6.0",          # config.yaml parsing
    "python-dotenv>=1.0",   # .env fallback
    "tenacity>=8.2",        # Retry with backoff (rate limits, transient errors)
    "rich>=13",             # Terminal output, progress bars, tables
]

[project.optional-dependencies]
dev = ["pytest>=8", "pytest-httpx>=0.30", "ruff>=0.4"]
```

---

## Configuration

### `.env` (secrets — NEVER commit)

```env
# Monzo OAuth2 — obtain from https://developers.monzo.com
MONZO_CLIENT_ID=oauth2client_xxxxx
MONZO_CLIENT_SECRET=mnzconf.xxxxx
MONZO_REDIRECT_URI=http://localhost:9876/callback

# Trading 212 — generate from the app: Settings > API
T212_API_KEY=your_api_key_here
T212_API_SECRET=your_api_secret_here
T212_ENVIRONMENT=live   # "live" or "demo"
```

### `config.yaml` (non-secret runtime config)

```yaml
output_dir: ./data
monzo:
  account_type: uk_retail       # uk_retail | uk_retail_joint
  lookback_days: 90             # SCA restriction: max 90 days without fresh auth
  token_file: ./tokens/monzo_token.json
trading212:
  include_orders: true
  include_dividends: true
  include_transactions: true
  include_interest: true
  lookback_days: 365            # No SCA restriction — go as far back as you want
  poll_interval_seconds: 10     # How often to check CSV export status
  poll_timeout_seconds: 120     # Give up after this long
```

---

## Monzo Integration

### Critical constraint: Strong Customer Authentication (SCA)

After initial OAuth, the user has **5 minutes** to pull full transaction history. After that window, only the **last 90 days** are accessible. Refreshing the token does NOT reset this. Only a full re-auth (user approves in Monzo app) resets it.

**Design implication:** The default mode should assume 90-day access. Provide a `--full-history` flag that warns the user they need to complete auth quickly and then pulls everything within the 5-min window.

### Auth flow (`src/monzo/auth.py`)

1. **Initial auth (interactive, one-time setup):**
   - Start a temporary local HTTP server on `localhost:9876`
   - Open browser to: `https://auth.monzo.com/?client_id={id}&redirect_uri={uri}&response_type=code&state={random}`
   - Receive callback with `?code=xxx&state=yyy`
   - Validate state matches
   - Exchange code for tokens: `POST https://api.monzo.com/oauth2/token`
     ```
     grant_type=authorization_code
     client_id=...
     client_secret=...
     redirect_uri=...
     code=...
     ```
   - Response: `{ access_token, refresh_token, expires_in (21600 = 6hrs), token_type, user_id }`
   - **User must then approve in the Monzo app** (push notification) — the token has no permissions until this happens
   - Persist tokens + timestamp to `monzo_token.json`

2. **Token refresh (automated, for confidential clients):**
   - Before each API call, check if token is expired (compare stored timestamp + expires_in vs now)
   - If expired: `POST https://api.monzo.com/oauth2/token`
     ```
     grant_type=refresh_token
     client_id=...
     client_secret=...
     refresh_token=...
     ```
   - Response: new access_token + new refresh_token (old ones are invalidated)
   - Update persisted token file
   - **Important:** Refresh is one-time per refresh_token. Always store the NEW refresh_token.

3. **Token file format (`monzo_token.json`):**
   ```json
   {
     "access_token": "...",
     "refresh_token": "...",
     "expires_in": 21600,
     "obtained_at": "2025-03-26T10:00:00Z",
     "user_id": "user_xxx",
     "sca_approved_at": "2025-03-26T10:01:00Z"
   }
   ```

### API client (`src/monzo/client.py`)

**Base URL:** `https://api.monzo.com`

**Auth header:** `Authorization: Bearer {access_token}`

**Endpoints to implement:**

1. **Verify auth:** `GET /ping/whoami` → `{ authenticated: bool, client_id, user_id }`
   - Call this after loading tokens to verify they're still valid before attempting refresh

2. **List accounts:** `GET /accounts?account_type={uk_retail}`
   - Response: `{ accounts: [{ id, description, created }] }`
   - Cache the account_id for subsequent calls

3. **List transactions:** `GET /transactions?account_id={id}&since={rfc3339}&before={rfc3339}`
   - Paginate using `since` (RFC3339 timestamp or tx ID) and `before` (RFC3339)
   - `limit` param: default 30, max 100 — set to 100 for efficiency
   - Response fields per transaction:
     ```
     id, created, description, amount (minor units, negative = debit),
     currency, settled, category, merchant (id or expanded object),
     notes, is_load, decline_reason (only if declined), metadata
     ```
   - **Amount is in MINOR UNITS** (pennies for GBP). Divide by 100 for display.
   - Paginate forward: set `since` to the `id` of the last transaction received
   - Continue until response returns fewer items than `limit`

4. **Read balance (optional):** `GET /balance?account_id={id}`
   - Useful for adding summary to statement header

### Exporter (`src/monzo/exporter.py`)

Transform raw transaction list into a CSV statement:

```csv
Date,Time,Transaction ID,Type,Description,Category,Amount,Currency,Balance,Notes,Settled
2025-03-25,14:32:18,tx_xxx,Debit,TESCO STORES,groceries,-12.50,GBP,,Salmon,2025-03-26T14:32:18Z
2025-03-25,09:00:00,tx_yyy,Credit,SALARY,income,3200.00,GBP,,Monthly salary,2025-03-25T09:00:00Z
```

- Sort transactions by `created` ascending
- Convert `amount` from minor units to decimal (amount / 100)
- Type: "Debit" if amount < 0, "Credit" if amount > 0, "Top-up" if is_load
- Skip declined transactions (those with `decline_reason`)
- Filename format: `monzo_statement_{account_id}_{YYYY-MM-DD}_to_{YYYY-MM-DD}.csv`

### Rate limits

Monzo doesn't publish exact rate limits but returns `429` with `Retry-After` header. Use tenacity:

```python
@retry(
    retry=retry_if_exception_type(httpx.HTTPStatusError),
    wait=wait_exponential(multiplier=1, min=2, max=60),
    stop=stop_after_attempt(5),
    before_sleep=before_sleep_log(logger, logging.WARNING),
)
async def _request(self, method, path, **kwargs):
    response = await self.http.request(method, path, **kwargs)
    if response.status_code == 429:
        retry_after = int(response.headers.get("Retry-After", 5))
        raise httpx.HTTPStatusError(
            f"Rate limited, retry after {retry_after}s",
            request=response.request,
            response=response,
        )
    response.raise_for_status()
    return response.json()
```

---

## Trading 212 Integration

### Auth (`src/trading212/auth.py`)

Much simpler than Monzo — static API key + secret, no OAuth.

```python
import base64

def build_auth_header(api_key: str, api_secret: str) -> dict:
    credentials = f"{api_key}:{api_secret}"
    encoded = base64.b64encode(credentials.encode("utf-8")).decode("utf-8")
    return {"Authorization": f"Basic {encoded}"}
```

**Base URLs:**
- Live: `https://live.trading212.com/api/v0`
- Demo: `https://demo.trading212.com/api/v0`

### API client (`src/trading212/client.py`)

**Endpoints to implement:**

1. **Account summary:** `GET /equity/account/summary`
   - Useful for confirmation / logging

2. **Request CSV export:** `POST /equity/history/exports`
   - Rate limit: **1 request per 30 seconds**
   - Request body (JSON):
     ```json
     {
       "dataIncluded": {
         "includeDividends": true,
         "includeInterest": true,
         "includeOrders": true,
         "includeTransactions": true
       },
       "timeFrom": "2024-03-26T00:00:00Z",
       "timeTo": "2025-03-26T23:59:59Z"
     }
     ```
   - Response: `{ "reportId": 12345 }`

3. **Poll export status:** `GET /equity/history/exports`
   - Rate limit: **1 request per 60 seconds** — respect this strictly
   - Returns array of all exports. Find yours by `reportId`.
   - Response item fields:
     ```
     reportId, status, downloadLink, timeFrom, timeTo,
     dataIncluded { includeDividends, includeInterest, includeOrders, includeTransactions }
     ```
   - Status enum: `Queued` → `Processing` → `Running` → `Finished` (or `Failed`/`Canceled`)
   - When status is `Finished`, `downloadLink` contains a URL to download the CSV

4. **Download CSV:** `GET {downloadLink}` — just fetch the URL and save to disk

5. **Historical orders (alternative/supplement):** `GET /equity/history/orders`
   - Uses cursor-based pagination
   - Params: `limit` (max 50), `cursor` (from `nextPagePath`)
   - Paginate until `nextPagePath` is `null`

6. **Historical dividends:** `GET /equity/history/dividends`
   - Same cursor-based pagination

7. **Historical transactions:** `GET /equity/history/transactions`
   - Same cursor-based pagination

### Exporter (`src/trading212/exporter.py`)

The CSV export endpoint gives you a ready-made file. The exporter just needs to:

1. Request the export with configured date range and data types
2. Poll until `Finished` (with timeout)
3. Download the CSV to `data/trading212/`
4. Filename format: `t212_export_{YYYY-MM-DD}_to_{YYYY-MM-DD}.csv`

For the paginated endpoints (orders, dividends, transactions), implement a generic paginator:

```python
async def paginate_all(self, path: str, limit: int = 50) -> list[dict]:
    items = []
    url = f"{path}?limit={limit}"
    while url:
        data = await self._request("GET", url)
        items.extend(data.get("items", []))
        next_path = data.get("nextPagePath")
        url = next_path  # Already contains full path with cursor
    return items
```

### Rate limit handling

Trading 212 returns rate limit headers on every response:

```
x-ratelimit-limit: 6
x-ratelimit-period: 60
x-ratelimit-remaining: 5
x-ratelimit-reset: 1711468800
x-ratelimit-used: 1
```

Implement a rate-limit-aware wrapper:

```python
import time

async def _request(self, method: str, path: str, **kwargs):
    response = await self.http.request(method, self.base_url + path, **kwargs)
    
    remaining = int(response.headers.get("x-ratelimit-remaining", 1))
    reset_ts = int(response.headers.get("x-ratelimit-reset", 0))
    
    if response.status_code == 429:
        wait = max(reset_ts - time.time(), 1)
        logger.warning(f"Rate limited. Waiting {wait:.0f}s")
        await asyncio.sleep(wait)
        return await self._request(method, path, **kwargs)  # retry once
    
    response.raise_for_status()
    
    # Proactive backoff: if nearly exhausted, sleep until reset
    if remaining <= 1 and reset_ts > time.time():
        wait = reset_ts - time.time() + 0.5
        logger.info(f"Proactive rate limit pause: {wait:.0f}s")
        await asyncio.sleep(wait)
    
    return response
```

---

## CLI Interface (`src/cli.py`)

Use Click to build the CLI:

```
Usage: statements [OPTIONS] COMMAND [ARGS]...

Commands:
  monzo          Download Monzo transactions as CSV
  t212           Download Trading 212 statement CSV
  all            Download from all configured sources
  monzo-auth     Run interactive Monzo OAuth setup
  status         Show auth status for all sources
```

### Command details

```
statements monzo [--days 90] [--from 2025-01-01] [--to 2025-03-26] [--full-history]
statements t212 [--days 365] [--from 2024-03-26] [--to 2025-03-26] [--orders-only] [--dividends-only]
statements all [--days 90]
statements monzo-auth          # Interactive OAuth setup
statements status              # Check token validity, account info
```

**Output behaviour:**
- Print progress with `rich` (spinner while polling T212, progress bar for Monzo pagination)
- On success, print the output file path
- On auth failure, print clear instructions on how to fix (re-run `monzo-auth`, check API key, etc.)

---

## Error Handling Strategy

| Scenario | Handling |
|---|---|
| Monzo token expired, refresh available | Auto-refresh, persist new tokens, retry |
| Monzo token expired, no refresh (non-confidential) | Print error + instructions to re-auth |
| Monzo SCA not approved | Detect via 403 on transactions endpoint → prompt user to approve in app |
| Monzo 429 rate limit | Exponential backoff via tenacity, respect Retry-After header |
| T212 export status `Failed` | Log error, suggest retry, do not retry automatically |
| T212 export timeout (>120s) | Log warning, print reportId so user can check later |
| T212 429 rate limit | Read `x-ratelimit-reset`, sleep until then, retry |
| Network errors (any) | Retry 3x with exponential backoff, then fail with clear message |
| Invalid/missing .env values | Fail fast at startup with pydantic validation errors |
| Token file corrupted/missing | Detect at startup, prompt to re-auth (Monzo) or check key (T212) |

---

## Testing

### Unit tests with mocked HTTP

Use `pytest-httpx` to mock API responses:

```python
# test_monzo_client.py
async def test_list_transactions(httpx_mock):
    httpx_mock.add_response(
        url="https://api.monzo.com/transactions",
        json={"transactions": [
            {"id": "tx_001", "amount": -510, "currency": "GBP", "created": "2025-03-25T14:32:18Z", ...}
        ]}
    )
    client = MonzoClient(access_token="fake")
    txns = await client.list_transactions("acc_001", since="2025-03-01T00:00:00Z")
    assert len(txns) == 1
    assert txns[0]["amount"] == -510
```

### Integration test mode

Add a `--dry-run` flag to the CLI that:
- Validates config + auth
- Makes one lightweight API call (whoami / account summary) to verify connectivity
- Prints what it WOULD download without actually doing it

---

## Security Notes

- `.env` and `tokens/` directory must be in `.gitignore`
- Token file permissions should be `600` (owner read/write only)
- Trading 212 supports IP restriction on API keys — recommend users enable this
- Never log access tokens or API secrets, even at DEBUG level. Mask them in logs.
- The Monzo client_secret and refresh_token are the crown jewels — treat as passwords

---

## Automation (Cron / Scheduler)

For unattended scheduled runs, the **Trading 212 side works fully unattended** (static API key).

**Monzo requires periodic manual re-auth** — the refresh token chain works for weeks/months, but eventually Monzo may invalidate it or SCA may be required again. Design for this:

1. The cron job runs `statements all --days 30`
2. If Monzo auth fails, the tool writes a notification file / sends a webhook / exits with code 2
3. User manually runs `statements monzo-auth` to re-authenticate
4. Next cron run succeeds

Example crontab (1st of each month at 8am):
```
0 8 1 * * cd /path/to/statement-downloader && /path/to/python -m src.cli all --days 31 >> /var/log/statements.log 2>&1
```

---

## Implementation Order

Build in this sequence — each step is independently testable:

1. **Config + project scaffolding** — pydantic settings, .env loading, config.yaml, directory structure
2. **Trading 212 auth + client** — simplest auth, get account summary working
3. **Trading 212 CSV export flow** — request → poll → download, with rate limit handling
4. **Trading 212 paginated history** — orders, dividends, transactions as supplementary data
5. **Monzo auth flow** — local HTTP server, OAuth exchange, token persistence
6. **Monzo client** — accounts, transactions with pagination
7. **Monzo exporter** — transform transactions to CSV
8. **CLI** — Click commands wiring everything together
9. **Error handling hardening** — all edge cases from the table above
10. **Tests** — unit tests with mocked HTTP for both clients