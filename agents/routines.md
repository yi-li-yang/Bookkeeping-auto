# Routines

Each routine runs on a schedule in Yili's finance channel in Claude Duet. A
scheduled run has nobody there to say yes, so routines never sign in, pay,
send, merge or change settings. They read, save statement rows to the
connector's own database, update the Bookkeeping auto page's data, and add
tasks to the channel's Tasks list. They post only when there is something
to say.

Amounts and balances never go into agent memory or this repository. Memory
keeps only dates and status, for example "Amex 2026-10 statement saved".

Times are Asia/Tashkent while Yili travels; move them to Europe/London when
home.

## 1. Money check — weekly, Monday 08:30

> Check Yili's money sources and say what needs attention. Use the yili-mcp-connector connector: call get_sync_status, get_balances, get_monthly_summary (months 2) and get_ledger_summary (months 2). If any source failed or has not synced for over 24 hours, add a task naming the source and the fix the status gives (for example, approve access again in the Monzo app). If a payment in get_ledger_summary's upcoming_due falls within 10 days, add a task with its date unless one exists. Then post at most five short lines: spending this month so far against last month, anything unusual, and the tasks you added. Post nothing if all is normal and nothing is due. Never sign in anywhere, pay, send or change any setting. Do not write amounts to memory.

## 2. Bills, statements and income — monthly, 6th at 09:00

> Save last month's statements and bills into the connector. 1) Call get_ledger_summary to see which source and statement pairs are already saved. 2) Search Gmail for statements, bills and income from the last 40 days: Chase, American Express, Bank of Scotland, M&S Bank, Klarna, Octopus Energy, the rent, Insite, payslips and invoices. Also look in Google Drive for statement files added in that time. 3) For each one not yet saved: if the amounts are in the email body, read them; if they are in an attachment, download it to the session's scratch space, clone yi-li-yang/bookkeeping-auto and run `python -m backend.agent_tools.statement_to_ledger FILE --source <source> --statement <YYYY-MM>` (add --charges-positive for card statements, --year for PDFs). Check the rows against the statement's own total; if they do not match, save nothing for that statement and add a task saying which one needs a look. 4) Add a category to each row and save with save_ledger_entries, kind transaction for card and bank lines, bill for bills with their due_date, income for pay, statement_total for a card's balance due with its due_date. 5) Add a task for each payment due in the next 14 days. 6) Post one short summary: which statements were saved, which could not be read, and the payment dates. Never commit files or amounts to any repository, never sign in, pay or send. Delete downloaded files when done.

## 3. Cards, memberships and offers — weekly, Thursday 09:00

> Watch Yili's cards and memberships for dates and offers. Read the Google Sheet "Bookkeeping auto - cards" in Yili's Drive. For each card or membership, if a 0% period, annual fee or renewal date is within 30 days, add a task naming it and the date, unless one already exists. Then search Gmail from the last 7 days for card offers, cashback, rate changes, fee notices and membership renewals from those providers. Add a task only for something Yili can act on, such as a rate change, a fee, a renewal, or an offer that fits a card they already hold. Check any offer against the provider's own page with web search before mentioning it. Post at most five lines, or nothing. Never apply for, open, close or change any card or account, never sign in, and never copy numbers from emails into the sheet.

## 4. Pension values — quarterly, 1 Jan, 1 Apr, 1 Jul, 1 Oct at 10:00

> Check whether Yili's pension values are current. Call get_pensions on the yili-mcp-connector connector. For each pot whose value is missing or more than 80 days old, add one task: "Update pension values on the connector's pension page" naming the providers (Aviva, Standard Life, Moneybox). Never sign in to a provider. Post one line only if a task was added.
