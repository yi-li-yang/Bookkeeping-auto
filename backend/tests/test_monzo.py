"""
Monzo client and sync tests.

Everything runs against httpx.MockTransport and a throwaway SQLite file, so no
credentials and no network.
"""
import json
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.db.database import Base
from backend.db.models import SourceFile, Transaction
from backend.integrations import monzo_auth, monzo_sync
from backend.integrations.monzo import (
    MonzoApprovalRequired,
    MonzoAuthError,
    MonzoClient,
    MonzoError,
    MonzoRateLimitError,
    MonzoTokens,
    authorize_url,
    exchange_code,
    refresh_tokens,
)


class RecordingSleep:
    def __init__(self):
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def make_client(handler, sleep=None):
    return MonzoClient(
        "test-token",
        transport=httpx.MockTransport(handler),
        sleep=sleep or RecordingSleep(),
    )


def txn(tx_id: str, amount: int, created: str = "2026-08-01T10:00:00Z", **extra):
    """A Monzo transaction: amount in minor units, negative for money out."""
    return {"id": tx_id, "amount": amount, "created": created,
            "currency": "GBP", "description": "RAW STRING", **extra}


# -- OAuth ------------------------------------------------------------------

def test_authorize_url_carries_the_required_params():
    url = authorize_url("cli_123", "http://localhost:8000/cb", "st4te")
    parsed = urlparse(url)
    params = parse_qs(parsed.query)

    assert parsed.netloc == "auth.monzo.com"
    assert params["client_id"] == ["cli_123"]
    assert params["response_type"] == ["code"]
    assert params["redirect_uri"] == ["http://localhost:8000/cb"]
    assert params["state"] == ["st4te"]


def test_authorize_url_without_a_client_id_is_actionable():
    with pytest.raises(MonzoAuthError) as excinfo:
        authorize_url("", "http://localhost:8000/cb", "s")
    assert "MONZO_CLIENT_ID" in str(excinfo.value)


def test_exchange_code_posts_the_grant_and_computes_expiry():
    captured = {}

    def handler(request):
        captured.update(parse_qs(request.content.decode()))
        return httpx.Response(200, json={
            "access_token": "acc_tok", "refresh_token": "ref_tok",
            "expires_in": 21600, "user_id": "user_1",
        })

    tokens = exchange_code(
        "cli", "sec", "http://localhost:8000/cb", "the-code",
        transport=httpx.MockTransport(handler), now=1000.0,
    )

    assert captured["grant_type"] == ["authorization_code"]
    assert captured["code"] == ["the-code"]
    assert tokens.access_token == "acc_tok"
    assert tokens.expires_at == 1000.0 + 21600


def test_refresh_uses_the_refresh_grant():
    captured = {}

    def handler(request):
        captured.update(parse_qs(request.content.decode()))
        return httpx.Response(200, json={
            "access_token": "new_acc", "refresh_token": "new_ref", "expires_in": 21600,
        })

    tokens = refresh_tokens(
        "cli", "sec", "old_ref", transport=httpx.MockTransport(handler), now=0.0
    )
    assert captured["grant_type"] == ["refresh_token"]
    assert captured["refresh_token"] == ["old_ref"]
    assert tokens.refresh_token == "new_ref"


def test_a_rejected_token_exchange_raises_rather_than_returning_junk():
    def handler(request):
        return httpx.Response(400, text="invalid_grant")

    with pytest.raises(MonzoAuthError):
        exchange_code("cli", "sec", "cb", "bad",
                      transport=httpx.MockTransport(handler))


def test_expiry_uses_a_leeway_so_a_token_cannot_die_mid_sync():
    tokens = MonzoTokens("a", "r", expires_at=1000.0)
    assert not tokens.is_expired(now=600.0)
    # Still nominally valid, but inside the leeway, so treated as expired.
    assert tokens.is_expired(now=800.0)
    assert tokens.is_expired(now=1001.0)


# -- token persistence ------------------------------------------------------

class FakeSettings:
    def __init__(self, tmp_path, client_id="cli", client_secret="sec"):
        self.monzo_token_path = tmp_path / "monzo.json"
        self.monzo_client_id = client_id
        self.monzo_client_secret = client_secret

    @property
    def monzo_configured(self):
        return bool(self.monzo_client_id and self.monzo_client_secret)


def test_tokens_round_trip_through_the_store(tmp_path):
    s = FakeSettings(tmp_path)
    monzo_auth.save_tokens(s, MonzoTokens("a", "r", 123.0, "user_1"))

    loaded = monzo_auth.load_tokens(s)
    assert loaded.access_token == "a"
    assert loaded.refresh_token == "r"
    assert loaded.user_id == "user_1"


def test_a_corrupt_token_file_is_reported_not_raised(tmp_path):
    """A truncated file must degrade to 'not connected', not crash every sync."""
    s = FakeSettings(tmp_path)
    s.monzo_token_path.write_text("{not json")
    assert monzo_auth.load_tokens(s) is None


def test_no_token_file_means_not_connected(tmp_path):
    s = FakeSettings(tmp_path)
    with pytest.raises(monzo_auth.MonzoNotConnected):
        monzo_auth.access_token(s)


def test_an_expired_token_is_refreshed_and_the_new_pair_persisted(tmp_path, monkeypatch):
    """
    The critical path: Monzo invalidates the old refresh token on use, so
    failing to persist the new one silently kills the connection.
    """
    s = FakeSettings(tmp_path)
    monzo_auth.save_tokens(s, MonzoTokens("old_acc", "old_ref", expires_at=0.0))

    monkeypatch.setattr(
        monzo_auth, "refresh_tokens",
        lambda cid, sec, ref, now=None: MonzoTokens("new_acc", "new_ref", 99999.0),
    )

    assert monzo_auth.access_token(s, now=100.0) == "new_acc"
    assert monzo_auth.load_tokens(s).refresh_token == "new_ref"


def test_a_live_token_is_not_refreshed(tmp_path, monkeypatch):
    s = FakeSettings(tmp_path)
    monzo_auth.save_tokens(s, MonzoTokens("live", "r", expires_at=10_000.0))
    monkeypatch.setattr(monzo_auth, "refresh_tokens", lambda *a, **k: pytest.fail(
        "refreshed a token that was still valid"))
    assert monzo_auth.access_token(s, now=100.0) == "live"


def test_no_refresh_token_explains_the_confidential_client_setting(tmp_path):
    s = FakeSettings(tmp_path)
    monzo_auth.save_tokens(s, MonzoTokens("acc", None, expires_at=0.0))
    with pytest.raises(monzo_auth.MonzoNotConnected) as excinfo:
        monzo_auth.access_token(s, now=100.0)
    assert "confidential" in str(excinfo.value)


# -- error mapping ----------------------------------------------------------

def test_pending_in_app_approval_is_not_reported_as_a_broken_token():
    """
    Monzo 403s until the push notification is accepted. Telling the user to
    reconnect would be wrong -- and reconnecting would not fix it.
    """
    def handler(request):
        return httpx.Response(403, json={"code": "forbidden.verification_required"})

    with make_client(handler) as client:
        with pytest.raises(MonzoApprovalRequired) as excinfo:
            client.accounts()
    assert "approve" in str(excinfo.value).lower()


def test_the_code_a_fresh_client_actually_returns_is_recognised():
    """
    Observed live: a newly authorised client gets
    `forbidden.insufficient_permissions`, not the `verification_required` the
    community threads describe. Matching only the documented string reported a
    hard error and stopped the retry loop.
    """
    def handler(request):
        return httpx.Response(403, json={
            "code": "forbidden.insufficient_permissions",
            "message": "Access forbidden due to insufficient permissions",
        })

    with make_client(handler) as client:
        with pytest.raises(MonzoApprovalRequired):
            client.accounts()


def test_an_expired_access_token_raises_autherror():
    def handler(request):
        return httpx.Response(401, json={"code": "unauthorized.bad_access_token.expired"})

    with make_client(handler) as client:
        with pytest.raises(MonzoAuthError):
            client.accounts()


def test_rate_limit_honours_retry_after_then_gives_up():
    def handler(request):
        return httpx.Response(429, headers={"retry-after": "3"})

    sleep = RecordingSleep()
    with make_client(handler, sleep=sleep) as client:
        with pytest.raises(MonzoRateLimitError):
            client.accounts()
    assert 3.0 in sleep.calls


def test_server_errors_are_retried():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"accounts": []})

    with make_client(handler) as client:
        assert client.accounts() == []
    assert calls["n"] == 3


# -- accounts ---------------------------------------------------------------

def test_closed_accounts_are_filtered_out():
    def handler(request):
        return httpx.Response(200, json={"accounts": [
            {"id": "acc_1", "type": "uk_retail"},
            {"id": "acc_old", "type": "uk_retail", "closed": True},
        ]})

    with make_client(handler) as client:
        assert [a["id"] for a in client.accounts()] == ["acc_1"]


def test_account_label_does_not_leak_the_account_holders_name():
    """
    Monzo sets `description` to the holder's real name on personal accounts.
    Using it would print that on every transaction row and chart legend.
    """
    label = monzo_sync.account_label(
        {"id": "acc_1", "type": "uk_retail", "description": "Jane Q Public"}
    )
    assert label == "Monzo"
    assert "Jane" not in label


def test_every_account_type_gets_a_distinct_label():
    """
    One label for all of them merged a current account, a Flex credit account
    and a rewards pot into a single indistinguishable history -- and broke
    credit-card balance matching, which keys on account_name.
    """
    labels = [
        monzo_sync.account_label({"type": t})
        for t in ("uk_retail", "uk_retail_joint", "uk_monzo_flex", "uk_rewards")
    ]
    assert labels == ["Monzo", "Monzo Joint", "Monzo Flex", "Monzo Rewards"]
    assert len(set(labels)) == len(labels)


def test_an_unknown_account_type_still_gets_its_own_name():
    """A type Monzo adds later must not silently merge into the current account."""
    assert monzo_sync.account_label({"type": "uk_savings_pot"}) == "Monzo Savings Pot"
    assert monzo_sync.account_label({}) == "Monzo"


def test_monzo_flex_is_a_credit_card_not_a_bank_account():
    """
    Flex is instalment credit. Typed as `bank` it counts as cash in net worth
    and never matches its credit_cards row.
    """
    assert monzo_sync.account_kind({"type": "uk_monzo_flex"}) == "credit_card"
    assert monzo_sync.account_kind({"type": "uk_retail"}) == "bank"
    assert monzo_sync.account_kind({"type": "uk_retail_joint"}) == "bank"


# -- pagination -------------------------------------------------------------

def test_pagination_follows_the_last_transaction_id():
    seen = []

    def handler(request):
        since = request.url.params.get("since")
        seen.append(since)
        if since is None:
            return httpx.Response(200, json={
                "transactions": [txn(f"tx_{i}", -100) for i in range(100)]
            })
        return httpx.Response(200, json={"transactions": [txn("tx_last", -100)]})

    with make_client(handler) as client:
        items = client.transactions("acc_1")

    assert len(items) == 101
    assert seen == [None, "tx_99"]


def test_a_short_page_ends_pagination():
    def handler(request):
        return httpx.Response(200, json={"transactions": [txn("tx_1", -100)]})

    with make_client(handler) as client:
        assert len(client.transactions("acc_1")) == 1


def test_a_repeating_cursor_stops_rather_than_looping():
    """A full page whose last id never advances would otherwise spin forever."""
    def handler(request):
        return httpx.Response(200, json={
            "transactions": [txn("same_id", -100) for _ in range(100)]
        })

    with make_client(handler) as client:
        items = client.transactions("acc_1", max_pages=50)

    assert len(items) == 200      # first page, then one repeat, then it stops


def test_merchant_is_expanded_in_the_request():
    def handler(request):
        assert request.url.params.get("expand[]") == "merchant"
        return httpx.Response(200, json={"transactions": []})

    with make_client(handler) as client:
        client.transactions("acc_1")


# -- mapping ----------------------------------------------------------------

def test_description_prefers_the_merchant_name_over_the_card_string():
    assert monzo_sync.transaction_description(
        txn("t", -250, merchant={"name": "Transport for London"})
    ) == "Transport for London"


def test_description_falls_back_through_counterparty_then_raw():
    assert monzo_sync.transaction_description(
        txn("t", -250, counterparty={"name": "A Friend"})
    ) == "A Friend"
    assert monzo_sync.transaction_description(txn("t", -250)) == "RAW STRING"


def test_declined_transactions_are_skipped():
    """A declined payment shows its would-be amount; counting it invents spending."""
    assert monzo_sync.is_skippable(txn("t", -50000, decline_reason="INSUFFICIENT_FUNDS"))
    assert not monzo_sync.is_skippable(txn("t", -50000))


def test_zero_amount_rows_are_skipped():
    assert monzo_sync.is_skippable(txn("t", 0))


def test_pot_transfers_and_top_ups_are_categorised_as_transfers():
    """
    Both move the user's own money. Filed anywhere else they would register as
    income or spending, which the analytics layer specifically excludes.
    """
    assert monzo_sync.deterministic_category(
        txn("t", -10000, scheme="uk_retail_pot")) == "Transfers"
    assert monzo_sync.deterministic_category(
        txn("t", 5000, is_load=True)) == "Transfers"
    assert monzo_sync.deterministic_category(txn("t", -500)) is None


# -- sync -------------------------------------------------------------------

@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'm.db'}")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture
def no_llm(monkeypatch):
    """Categorise everything as Uncategorised, without calling Anthropic."""
    from backend.ingestion import llm_processor

    monkeypatch.setattr(
        llm_processor, "categorise_transactions",
        lambda rows: [{"id": r["id"], "category": "Shopping", "confidence": 0.8}
                      for r in rows],
    )


ACCOUNT = {"id": "acc_1", "type": "uk_retail", "description": "Jane Q Public"}


def test_sync_writes_transactions_with_pounds_and_the_right_sign(db, no_llm):
    def handler(request):
        return httpx.Response(200, json={"transactions": [
            txn("tx_1", -1250, merchant={"name": "Tesco"}),
            txn("tx_2", 250000, "2026-08-02T09:00:00Z"),
        ]})

    with make_client(handler) as client:
        result = monzo_sync.sync_account(db, client, ACCOUNT)

    assert result["written"] == 2
    rows = {t.description: t for t in db.query(Transaction).all()}

    # 1250 pennies out -> -12.50, and the sign already matches this schema.
    assert rows["Tesco"].amount == pytest.approx(-12.50)
    assert rows["Tesco"].date == "2026-08-01"
    assert rows["Tesco"].account_type == "bank"
    assert rows["Tesco"].account_name == "Monzo"
    assert rows["Tesco"].external_id == "monzo:tx_1"
    assert db.query(Transaction).filter(Transaction.amount > 0).one().amount == 2500.0


def test_resync_does_not_duplicate(db, no_llm):
    def handler(request):
        return httpx.Response(200, json={"transactions": [txn("tx_1", -1250)]})

    with make_client(handler) as client:
        monzo_sync.sync_account(db, client, ACCOUNT)
        second = monzo_sync.sync_account(db, client, ACCOUNT)

    assert second["written"] == 0
    assert db.query(Transaction).count() == 1


def test_pot_transfers_bypass_the_llm_and_land_as_transfers(db, monkeypatch):
    """The deterministic rows must not be sent to the categoriser at all."""
    from backend.ingestion import llm_processor

    asked = []

    def spy(rows):
        asked.extend(r["description"] for r in rows)
        return [{"id": r["id"], "category": "Shopping", "confidence": 0.8} for r in rows]

    monkeypatch.setattr(llm_processor, "categorise_transactions", spy)

    def handler(request):
        return httpx.Response(200, json={"transactions": [
            txn("tx_pot", -10000, scheme="uk_retail_pot"),
            txn("tx_shop", -500, merchant={"name": "Argos"}),
        ]})

    with make_client(handler) as client:
        monzo_sync.sync_account(db, client, ACCOUNT)

    pot = db.query(Transaction).filter_by(external_id="monzo:tx_pot").one()
    assert pot.category == "Transfers"
    assert pot.confidence == 1.0
    assert asked == ["Argos"]


def test_a_categorisation_failure_still_stores_the_transactions(db, monkeypatch):
    """Losing a category is recoverable; losing the transaction is not."""
    from backend.ingestion import llm_processor

    def boom(rows):
        raise RuntimeError("no API key")

    monkeypatch.setattr(llm_processor, "categorise_transactions", boom)

    def handler(request):
        return httpx.Response(200, json={"transactions": [txn("tx_1", -1250)]})

    with make_client(handler) as client:
        result = monzo_sync.sync_account(db, client, ACCOUNT)

    assert result["written"] == 1
    stored = db.query(Transaction).one()
    assert stored.category is None
    assert stored.amount == pytest.approx(-12.50)


def test_the_first_sync_asks_for_everything(db, no_llm):
    """
    Monzo serves full history only in the minutes after linking. If the first
    sync sends a `since`, that history is unreachable from then on.
    """
    seen = []

    def handler(request):
        seen.append(request.url.params.get("since"))
        return httpx.Response(200, json={"transactions": [txn("tx_1", -100)]})

    with make_client(handler) as client:
        monzo_sync.sync_account(db, client, ACCOUNT, full_history=True)

    assert seen == [None]


def test_later_syncs_are_incremental(db, no_llm):
    seen = []

    def handler(request):
        seen.append(request.url.params.get("since"))
        return httpx.Response(200, json={"transactions": [txn("tx_1", -100)]})

    with make_client(handler) as client:
        monzo_sync.sync_account(db, client, ACCOUNT, full_history=True)
        monzo_sync.sync_account(db, client, ACCOUNT, full_history=False)

    assert seen[0] is None
    assert seen[1] is not None and seen[1].endswith("Z")


def test_the_synthetic_source_file_is_reused_across_syncs(db, no_llm):
    def handler(request):
        return httpx.Response(200, json={"transactions": [txn("tx_1", -100)]})

    with make_client(handler) as client:
        monzo_sync.sync_account(db, client, ACCOUNT)
        monzo_sync.sync_account(db, client, ACCOUNT)

    files = db.query(SourceFile).all()
    assert len(files) == 1
    assert files[0].filename == "monzo:acc_1"
    assert files[0].file_type == "api"


def test_the_raw_payload_is_kept(db, no_llm):
    """Monzo's own category and FX fields are not modelled; keep them recoverable."""
    def handler(request):
        return httpx.Response(200, json={"transactions": [
            txn("tx_1", -1250, category="groceries",
                local_amount=-1500, local_currency="USD"),
        ]})

    with make_client(handler) as client:
        monzo_sync.sync_account(db, client, ACCOUNT)

    raw = json.loads(db.query(Transaction).one().raw_row)
    assert raw["category"] == "groceries"
    assert raw["local_currency"] == "USD"


def test_flex_transactions_are_stored_as_credit_card(db, no_llm):
    """End to end: the account type must reach the stored row, not stop at the label."""
    def handler(request):
        return httpx.Response(200, json={"transactions": [txn("tx_f", -2000)]})

    flex = {"id": "acc_flex", "type": "uk_monzo_flex", "description": "monzoflex_x"}
    with make_client(handler) as client:
        monzo_sync.sync_account(db, client, flex)

    row = db.query(Transaction).one()
    assert row.account_name == "Monzo Flex"
    assert row.account_type == "credit_card"


# -- internal transfers -----------------------------------------------------

def joint_transfer(tx_id: str, amount: int, joint_id: str = "acc_joint"):
    """Funding a joint account: p2p_payment, counterparty is an account we own."""
    return txn(tx_id, amount, scheme="p2p_payment", counterparty={
        "account_id": joint_id, "name": "Hoi Lee & Yili Yang",
        "user_id": "anonuser_x",
    })


def test_funding_your_own_joint_account_is_a_transfer():
    """
    The failure this prevents: two transfers of 2000 and 800 into a joint
    account that paid 1950 of rent were all categorised Rent, reporting 4750 of
    rent in a month where 1950 left the household.
    """
    own = {"acc_personal", "acc_joint"}
    assert monzo_sync.deterministic_category(
        joint_transfer("tx_1", -200000), own) == "Transfers"


def test_paying_an_actual_person_is_not_a_transfer():
    """
    Distinguished only by the counterparty account id -- a payment to a friend
    has the same scheme and a person's name too.
    """
    own = {"acc_personal"}
    assert monzo_sync.deterministic_category(
        joint_transfer("tx_1", -5000, joint_id="acc_someone_else"), own) is None


def test_paying_a_business_is_not_a_transfer():
    """Real rent leaving the joint account: a sort code, no counterparty account id."""
    rent = txn("tx_rent", -195000, scheme="payport_faster_payments", counterparty={
        "account_number": "33666581", "sort_code": "400602",
        "name": "Districts Properties Ltd", "beneficiary_account_type": "Business",
    })
    assert monzo_sync.deterministic_category(rent, {"acc_personal", "acc_joint"}) is None


def test_transfer_detection_is_inert_without_known_accounts():
    """No own-account list must never mean 'everything is a transfer'."""
    assert monzo_sync.deterministic_category(joint_transfer("tx_1", -100), set()) is None
    assert monzo_sync.deterministic_category(joint_transfer("tx_1", -100), None) is None


def test_own_account_ids_come_from_the_synced_source_files(db):
    db.add(SourceFile(filename="monzo:acc_a", file_type="api", status="done"))
    db.add(SourceFile(filename="monzo:acc_b", file_type="api", status="done"))
    db.add(SourceFile(filename="trading212:12345", file_type="api", status="done"))
    db.add(SourceFile(filename="statement.csv", file_type="csv", status="done"))
    db.commit()

    assert monzo_sync.own_account_ids(db) == {"acc_a", "acc_b"}


def test_internal_transfers_bypass_the_llm_during_sync(db, monkeypatch):
    from backend.ingestion import llm_processor

    asked = []

    def spy(rows):
        asked.extend(r["description"] for r in rows)
        return [{"id": r["id"], "category": "Rent", "confidence": 0.9} for r in rows]

    monkeypatch.setattr(llm_processor, "categorise_transactions", spy)

    # The joint account must already be known, which is what makes the
    # counterparty recognisable.
    db.add(SourceFile(filename="monzo:acc_joint", file_type="api", status="done"))
    db.commit()

    def handler(request):
        return httpx.Response(200, json={"transactions": [
            joint_transfer("tx_fund", -200000),
            txn("tx_shop", -500, merchant={"name": "Argos"}),
        ]})

    with make_client(handler) as client:
        monzo_sync.sync_account(db, client, ACCOUNT)

    fund = db.query(Transaction).filter_by(external_id="monzo:tx_fund").one()
    assert fund.category == "Transfers"
    assert fund.confidence == 1.0
    assert asked == ["Argos"]


def test_recategorise_repairs_rows_stored_before_the_rule_existed(db):
    sf = SourceFile(filename="monzo:acc_personal", file_type="api", status="done")
    db.add(sf)
    db.add(SourceFile(filename="monzo:acc_joint", file_type="api", status="done"))
    db.flush()

    db.add(Transaction(
        date="2026-08-01", description="Hoi Lee & Yili Yang", amount=-2000.0,
        category="Rent", account_name="Monzo", account_type="bank",
        source_file_id=sf.id, external_id="monzo:tx_fund",
        raw_row=json.dumps(joint_transfer("tx_fund", -200000)),
    ))
    db.commit()

    changed = monzo_sync.recategorise_internal_transfers(db)

    assert len(changed) == 1
    assert changed[0]["from"] == "Rent" and changed[0]["to"] == "Transfers"
    assert db.query(Transaction).one().category == "Transfers"


def test_recategorise_never_overrides_a_manual_edit(db):
    sf = SourceFile(filename="monzo:acc_personal", file_type="api", status="done")
    db.add(sf)
    db.add(SourceFile(filename="monzo:acc_joint", file_type="api", status="done"))
    db.flush()
    db.add(Transaction(
        date="2026-08-01", description="Hoi Lee & Yili Yang", amount=-2000.0,
        category="Gift", account_name="Monzo", account_type="bank",
        source_file_id=sf.id, external_id="monzo:tx_fund", is_user_edited=True,
        raw_row=json.dumps(joint_transfer("tx_fund", -200000)),
    ))
    db.commit()

    assert monzo_sync.recategorise_internal_transfers(db) == []
    assert db.query(Transaction).one().category == "Gift"
