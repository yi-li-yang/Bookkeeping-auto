"""
LLM-powered processing using the Anthropic Claude API.

Two responsibilities:
1. Column detection  — given raw CSV rows or PDF text/tables, identify date/amount/description
2. Batch categorisation — assign dynamic category labels to transactions
"""
import json
import os
import re
from typing import Optional

import anthropic

_client: Optional[anthropic.Anthropic] = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


def _call(prompt: str, max_tokens: int = 2048) -> str:
    client = _get_client()
    message = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


def _extract_json(text: str):
    """Pull the first JSON object or array out of a Claude response."""
    # Try to find ```json ... ``` block first
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        return json.loads(fence.group(1).strip())
    # Otherwise try to parse the whole response
    start = text.find("{") if "{" in text else text.find("[")
    if start == -1:
        raise ValueError(f"No JSON found in response: {text[:300]}")
    return json.loads(text[start:])


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
    items_text = json.dumps(
        [{"id": t["id"], "description": t["description"], "amount": t["amount"]} for t in batch],
        indent=2,
    )

    prompt = f"""You are a personal finance categoriser for a UK user. Assign a clear, human-readable category to each transaction below.

Rules:
- Use consistent, natural category names (e.g. "Groceries", "Dining Out", "Transport", "Subscriptions", "Salary", "Rent", "Utilities", "Healthcare", "Shopping", "Transfers", "Entertainment", "Travel", "Insurance")
- Invent new categories freely if needed — but keep them consistent
- Positive amounts are typically income; negative amounts are expenses
- confidence: float 0.0–1.0 indicating how certain you are

Transactions:
{items_text}

Respond with ONLY a JSON array:
[{{"id": <id>, "category": "<category>", "confidence": <float>}}, ...]
"""
    response = _call(prompt, max_tokens=2048)
    result = _extract_json(response)
    return result if isinstance(result, list) else []
