"""
LLM-powered processing.

Two responsibilities:
1. Column detection  — given raw CSV rows or PDF text/tables, identify date/amount/description
2. Batch categorisation — assign dynamic category labels to transactions

Every LLM feature funnels through `_call`, so the backend is a single switch:

- LLM_BACKEND=api (default) bills the Anthropic API, model set by LLM_MODEL.
- LLM_BACKEND=claude_code shells out to the Claude Code CLI, billing the
  user's subscription instead. Measured locally at ~17k input tokens of harness
  overhead per invocation regardless of prompt size, so it suits occasional
  analysis and not bulk categorisation.
"""
import json
import logging
import os
import re
import shutil
import subprocess
import time
from typing import Optional

import anthropic

from backend.config import settings

logger = logging.getLogger(__name__)

_client: Optional[anthropic.Anthropic] = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=settings.require_anthropic_key())
    return _client


def _call(prompt: str, max_tokens: int = 2048) -> str:
    """
    One prompt in, one text response out. Every LLM feature routes through here,
    so the backend and model are switchable in a single place.
    """
    if settings.llm_backend == "claude_code":
        return _call_claude_code(prompt)
    return _call_api(prompt, max_tokens)


def _call_claude_code(prompt: str) -> str:
    """
    Run the prompt through the Claude Code CLI, billing the user's subscription
    instead of the API.

    Costly per call: the CLI loads its full harness -- system prompt, tool
    definitions, CLAUDE.md -- so a trivial prompt still measured ~17k input
    tokens locally. Fine for the occasional analysis, wrong for bulk
    categorisation, which is why the backend is configurable rather than
    switched wholesale.
    """
    cli = settings.claude_cli_path or shutil.which("claude")
    if not cli:
        raise RuntimeError(
            "LLM_BACKEND=claude_code but the Claude Code CLI was not found. "
            "Install it with `npm i -g @anthropic-ai/claude-code`, or set "
            "CLAUDE_CLI_PATH to its full path."
        )

    try:
        completed = subprocess.run(
            [
                cli, "-p", "--output-format", "json",
                # The CLI is an agent and defaults to conversational replies.
                # _extract_json tolerates trailing prose, but suppressing it
                # keeps responses cheap and unambiguous.
                "--append-system-prompt",
                "Answer with the requested output only. No preamble, no "
                "explanation, no follow-up questions.",
            ],
            input=prompt, capture_output=True, text=True,
            timeout=settings.claude_cli_timeout, encoding="utf-8",
            # .cmd shim on Windows needs a shell-resolved launch.
            shell=(os.name == "nt" and str(cli).lower().endswith((".cmd", ".bat"))),
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"Claude Code CLI timed out after {settings.claude_cli_timeout}s."
        ) from exc

    if completed.returncode != 0:
        raise RuntimeError(
            f"Claude Code CLI failed ({completed.returncode}): "
            f"{(completed.stderr or completed.stdout)[:300]}"
        )

    # The CLI prints warnings before the JSON, so parse from the first brace
    # rather than assuming stdout is pure JSON.
    out = completed.stdout or ""
    start = out.find("{")
    if start == -1:
        raise RuntimeError(f"Claude Code CLI returned no JSON: {out[:300]}")

    payload = json.loads(out[start:])
    if payload.get("is_error"):
        raise RuntimeError(f"Claude Code CLI reported an error: {payload.get('result')}")
    return payload.get("result") or ""


def _call_api(prompt: str, max_tokens: int = 2048) -> str:
    client = _get_client()
    delay = 30
    for attempt in range(6):
        try:
            message = client.messages.create(
                model=settings.llm_model,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            return message.content[0].text
        except anthropic.APIStatusError as e:
            if e.status_code == 529 and attempt < 5:
                logger.warning(f"Claude overloaded (attempt {attempt + 1}), retrying in {delay}s…")
                time.sleep(delay)
                delay = min(delay * 2, 300)
            else:
                raise


def _extract_json(text: str):
    """
    Pull the first JSON object or array out of a model response.

    Two things this has to survive, both seen in practice:

    - Trailing prose. The Claude Code backend is agentic and tends to add a
      sentence after the payload, so the parse must stop at the end of the first
      complete value instead of requiring the whole remainder to be JSON.
    - An array whose first element is an object. The previous version preferred
      `{` over `[` whenever a brace appeared anywhere, so `[{"id": 0}]` started
      parsing at the brace and died on the trailing `]` with "Extra data".
    """
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        try:
            return json.loads(fence.group(1).strip())
        except json.JSONDecodeError:
            pass        # fall through to scanning the raw text

    decoder = json.JSONDecoder()
    for match in re.finditer(r"[{\[]", text):
        try:
            value, _ = decoder.raw_decode(text, match.start())
        except json.JSONDecodeError:
            continue
        return value

    raise ValueError(f"No JSON found in response: {text[:300]}")


# ---------------------------------------------------------------------------
# CSV column detection
# ---------------------------------------------------------------------------

def detect_csv_columns(rows: list[dict]) -> dict:
    """
    Given the first N rows from a CSV, ask Claude which columns hold
    date, amount, and description.

    Returns: {"date": col_name, "amount": col_name, "description": col_name}
    """
    sample = rows[:10]
    sample_text = json.dumps(sample, indent=2)

    prompt = f"""You are a financial data parser. Given these CSV rows from a bank or credit card statement, identify which column name corresponds to each of these fields:
- date (the transaction date)
- amount (the monetary value; may be a single column or split into debit/credit — pick the most useful single column, or note both)
- description (the merchant name or transaction description)

Rows:
{sample_text}

Respond with ONLY a JSON object like:
{{"date": "column_name", "amount": "column_name", "description": "column_name"}}

If amount is split across two columns (e.g. Debit and Credit), return:
{{"date": "column_name", "amount_debit": "column_name", "amount_credit": "column_name", "description": "column_name"}}
"""
    response = _call(prompt)
    return _extract_json(response)


# ---------------------------------------------------------------------------
# PDF parsing into structured transactions
# ---------------------------------------------------------------------------

def parse_pdf_transactions(pdf_data: dict, filename: str) -> list[dict]:
    """
    Given the output of pdf_parser.parse_pdf (tables + text), ask Claude to
    extract a list of transactions.

    Returns list of dicts: [{date, amount, description}, ...]
    """
    # Prefer tables; fall back to raw text
    if pdf_data["tables"]:
        content = "Tables extracted from PDF:\n" + json.dumps(pdf_data["tables"][:5], indent=2)
    else:
        # Truncate text to avoid huge prompts
        content = "Raw text from PDF:\n" + pdf_data["text"][:6000]

    prompt = f"""You are a financial data parser. The following content is from a bank/investment statement PDF named "{filename}".

{content}

Extract ALL transactions and return them as a JSON array. Each item must have:
- "date": string in YYYY-MM-DD format (infer year from context if not explicit)
- "amount": number — negative for money going out (debits/expenses), positive for money coming in (credits/income)
- "description": string — merchant name or transaction description

Return ONLY the JSON array, no explanation. If you cannot parse any transactions, return an empty array [].
"""
    response = _call(prompt, max_tokens=4096)
    result = _extract_json(response)
    if isinstance(result, dict) and "transactions" in result:
        result = result["transactions"]
    return result if isinstance(result, list) else []


# ---------------------------------------------------------------------------
# Investment portfolio holdings extraction (Trading 212 / broker PDFs)
# ---------------------------------------------------------------------------

def extract_investment_holdings(pdf_text: str, filename: str) -> list[dict]:
    """
    Given the full text of an investment statement PDF (e.g. Trading 212 Activity Statement),
    extract all portfolio positions into structured holding records.

    Returns list of dicts:
    [
      {
        "account_name": "Invest",
        "instrument": "AAPL",
        "isin": "US0378331005",
        "currency": "USD",
        "quantity": 3.14,
        "avg_price": 150.00,
        "current_price": 175.00,
        "fx_rate": 1.27,
        "cost_gbp": 371.20,
        "value_gbp": 432.50,
        "return_gbp": 61.30,
        "report_date": "2024-07-13"
      },
      ...
    ]
    """
    # Truncate to avoid token limits — 12 000 chars is ~3k tokens, sufficient for T212 PDFs
    truncated = pdf_text[:12000]

    prompt = f"""You are a financial data parser. The following text is extracted from an investment broker statement PDF named "{filename}".

Text:
{truncated}

Your task: extract every portfolio position (open investment holding) mentioned in this document.

For each position return a JSON object with these fields (use null if a value is not found):
- "account_name": string — name of the account/sub-account this position belongs to (e.g. "Invest", "Stocks ISA", "ISA")
- "instrument": string — ticker symbol (e.g. "AAPL", "NVDA", "VUSA")
- "isin": string or null — ISIN code if present
- "currency": string or null — original trading currency (e.g. "USD", "GBP")
- "quantity": number or null — number of shares/units held
- "avg_price": number or null — average cost per share in original currency
- "current_price": number or null — current price per share in original currency
- "fx_rate": number or null — FX rate used to convert to GBP
- "cost_gbp": number or null — total cost basis in GBP
- "value_gbp": number — current market value in GBP (required)
- "return_gbp": number or null — unrealised return/gain in GBP (positive = profit)
- "report_date": string — date of this statement in YYYY-MM-DD format

Return ONLY a JSON array of these objects. If no investment positions are found, return [].
"""
    response = _call(prompt, max_tokens=4096)
    result = _extract_json(response)
    if isinstance(result, dict) and "holdings" in result:
        result = result["holdings"]
    return result if isinstance(result, list) else []


# ---------------------------------------------------------------------------
# Batch categorisation
# ---------------------------------------------------------------------------

BATCH_SIZE = 50


def categorise_transactions(transactions: list[dict]) -> list[dict]:
    """
    Given a list of {"id": ..., "date": ..., "amount": ..., "description": ...},
    ask Claude to assign a category and confidence to each.

    Returns list of {"id": ..., "category": ..., "confidence": float 0-1}
    """
    results = []
    for i in range(0, len(transactions), BATCH_SIZE):
        batch = transactions[i : i + BATCH_SIZE]
        results.extend(_categorise_batch(batch))
    return results


def _categorise_batch(batch: list[dict]) -> list[dict]:
    # `date` is included deliberately: without it the model cannot tell a
    # recurring monthly charge from a one-off payment to the same payee, which
    # is exactly what separates rent from a deposit.
    items_text = json.dumps(
        [
            {
                "id": t["id"],
                "date": t.get("date"),
                "description": t["description"],
                "amount": t["amount"],
            }
            for t in batch
        ],
        indent=2,
    )

    prompt = f"""You are a personal finance categoriser for a UK user. Assign a clear, human-readable category to each transaction below.

Rules:
- Use consistent, natural category names (e.g. "Groceries", "Dining Out", "Transport", "Subscriptions", "Salary", "Rent", "Utilities", "Healthcare", "Shopping", "Transfers", "Entertainment", "Travel", "Insurance")
- Invent new categories freely if needed — but keep them consistent
- Positive amounts are typically income; negative amounts are expenses
- confidence: float 0.0-1.0 indicating how certain you are

Housing payments need care, because several different things go to the same
landlord or letting agent and they are not the same expense:
- "Rent" is ONLY the recurring periodic charge. Look across the batch: a rent
  payment repeats at a regular interval, and its amount is stable until the
  tenancy changes.
- A one-off payment to a landlord or letting agent that is NOT part of that
  recurring series is something else. Use "Rental Deposit" for a returnable
  security deposit (often larger than, or a multiple of, the recurring amount),
  and "Letting Fees" for holding fees, referencing and admin charges (usually
  much smaller).
- Two payments to the same payee on the same day are a strong signal that only
  one of them is rent.
- Do not classify a payment as Rent merely because the payee is a landlord.

Use "Transfers" for money moving between the user's own accounts, including
funding a joint account. These are not spending, and misfiling one as a real
expense double-counts whatever the receiving account then pays for.

Transactions:
{items_text}

Respond with ONLY a JSON array:
[{{"id": <id>, "category": "<category>", "confidence": <float>}}, ...]
"""
    response = _call(prompt, max_tokens=2048)
    result = _extract_json(response)
    return result if isinstance(result, list) else []
