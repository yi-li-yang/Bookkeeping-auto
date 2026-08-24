"""
Trading 212 client tests.

Everything runs against httpx.MockTransport, so no credentials and no network.
`sleep` is injected and recorded rather than actually slept, which lets the
pacing and backoff behaviour be asserted without slowing the suite down.
"""
import base64
import json

import httpx
import pytest

from backend.integrations.trading212 import (
    GLOBAL_MIN_INTERVAL,
    RATE_LIMITS,
    T212Account,
    T212AuthError,
    T212Error,
    T212RateLimitError,
    Trading212Client,
    auth_header,
    base_url,
    _min_interval,
)

ACCOUNT = T212Account(slug="invest", api_key_id="key-id", api_secret="secret-value")


class RecordingSleep:
    def __init__(self):
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)

    @property
    def total(self) -> float:
        return sum(self.calls)


def make_client(handler, sleep=None, env="live") -> Trading212Client:
    return Trading212Client(
        ACCOUNT,
        env=env,
        transport=httpx.MockTransport(handler),
        sleep=sleep or RecordingSleep(),
    )


# -- auth and URLs ---------------------------------------------------------

def test_auth_header_is_basic_base64_of_key_and_secret():
    assert auth_header("abc", "xyz") == "Basic " + base64.b64encode(b"abc:xyz").decode()
    assert auth_header("abc", "xyz") == "Basic YWJjOnh5eg=="


def test_auth_header_is_sent_on_every_request():
    seen = []

    def handler(request):
        seen.append(request.headers.get("authorization"))
        return httpx.Response(200, json={"id": 1})

    with make_client(handler) as client:
        client.account_summary()

    assert seen == [auth_header("key-id", "secret-value")]


def test_base_url_accepts_live_and_demo_only():
    assert base_url("live") == "https://live.trading212.com"
    assert base_url("demo") == "https://demo.trading212.com"
    assert base_url("LIVE") == "https://live.trading212.com"
    with pytest.raises(ValueError):
        base_url("production")


# -- rate-limit pacing -----------------------------------------------------

def test_min_interval_uses_longest_prefix_match():
    assert _min_interval("/equity/account/summary") == 5.0
    assert _min_interval("/equity/positions") == 1.0
    assert _min_interval("/equity/history/transactions") == 1.2
    # Unknown endpoints still get paced, never fired freely.
    assert _min_interval("/equity/something/new") == 1.2


def test_repeated_calls_to_same_endpoint_are_paced():
    def handler(request):
        return httpx.Response(200, json={"id": 1})

    sleep = RecordingSleep()
    with make_client(handler, sleep=sleep) as client:
        client.account_summary()
        client.account_summary()

    # First call has no predecessor, so exactly one wait, of the summary interval.
    assert len(sleep.calls) == 1
    assert sleep.calls[0] == pytest.approx(RATE_LIMITS["/equity/account/summary"], abs=0.1)


def test_different_endpoints_are_paced_independently():
    """Distinct endpoints do not inherit each other's per-endpoint interval."""
    def handler(request):
        if "positions" in request.url.path:
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={"id": 1})

    sleep = RecordingSleep()
    with make_client(handler, sleep=sleep) as client:
        client.account_summary()
        client.positions()

    # Only the global floor applies, not account/summary's 5s interval.
    assert len(sleep.calls) == 1
    assert sleep.calls[0] == pytest.approx(GLOBAL_MIN_INTERVAL, abs=0.1)


# -- error mapping ---------------------------------------------------------

def test_unauthorized_raises_t212autherror_naming_the_environment():
    def handler(request):
        return httpx.Response(401, json={"error": "nope"})

    with make_client(handler) as client:
        with pytest.raises(T212AuthError) as excinfo:
            client.account_summary()

    # 401 is a credentials problem; environment mismatch is the usual cause.
    assert "environment-specific" in str(excinfo.value)


def test_rate_limit_is_retried_then_raises():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(429, headers={"x-ratelimit-reset": "0"})

    sleep = RecordingSleep()
    with make_client(handler, sleep=sleep) as client:
        with pytest.raises(T212RateLimitError):
            client.account_summary()

    assert calls["n"] == 3  # the full retry budget


def test_rate_limit_recovers_when_the_next_attempt_succeeds():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"x-ratelimit-reset": "0"})
        return httpx.Response(200, json={"id": 42})

    with make_client(handler) as client:
        assert client.account_summary() == {"id": 42}
    assert calls["n"] == 2


def test_server_errors_are_retried_with_backoff():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"id": 7})

    sleep = RecordingSleep()
    with make_client(handler, sleep=sleep) as client:
        assert client.account_summary() == {"id": 7}

    assert calls["n"] == 3
    # Exponential: 1s then 2s, on top of any pacing wait.
    assert 1.0 in sleep.calls and 2.0 in sleep.calls


def test_client_errors_other_than_auth_raise_plain_error():
    def handler(request):
        return httpx.Response(422, text="bad request")

    with make_client(handler) as client:
        with pytest.raises(T212Error) as excinfo:
            client.account_summary()
    assert "422" in str(excinfo.value)


def test_absurd_ratelimit_reset_is_clamped_not_trusted():
    """A reset header in the wrong units must not cause an enormous sleep."""
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            # Far-future timestamp: reading this literally would sleep for years.
            return httpx.Response(429, headers={"x-ratelimit-reset": "99999999999"})
        return httpx.Response(200, json={"ok": True})

    sleep = RecordingSleep()
    with make_client(handler, sleep=sleep) as client:
        client.account_summary()

    assert max(sleep.calls) <= 60.0


def test_exhausted_window_sleeps_before_returning():
    def handler(request):
        return httpx.Response(
            200,
            json={"id": 1},
            headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "0"},
        )

    sleep = RecordingSleep()
    with make_client(handler, sleep=sleep) as client:
        client.account_summary()

    assert sleep.calls, "should proactively wait when the window is exhausted"


# -- pagination ------------------------------------------------------------

def test_first_page_sends_only_limit_and_later_pages_follow_nextpagepath():
    """
    The transactions endpoint rejects a cursor on the first request, so page one
    must be bare and subsequent pages must reuse nextPagePath verbatim.
    """
    requests: list[str] = []

    def handler(request):
        requests.append(str(request.url))
        if "cursor" not in str(request.url):
            return httpx.Response(200, json={
                "items": [{"reference": "a"}, {"reference": "b"}],
                "nextPagePath": "/api/v0/equity/history/transactions?limit=50&cursor=XYZ",
            })
        return httpx.Response(200, json={"items": [{"reference": "c"}], "nextPagePath": None})

    with make_client(handler) as client:
        items = client.history_transactions()

    assert [i["reference"] for i in items] == ["a", "b", "c"]
    assert "cursor" not in requests[0]
    assert "limit=50" in requests[0]
    assert "cursor=XYZ" in requests[1]


def test_pagination_respects_max_pages_cap():
    def handler(request):
        return httpx.Response(200, json={
            "items": [{"reference": "x"}],
            "nextPagePath": "/api/v0/equity/history/transactions?limit=50&cursor=n",
        })

    with make_client(handler) as client:
        items = client.history_transactions(max_pages=3)

    assert len(items) == 3  # one item per page, capped


def test_stop_when_halts_pagination_early():
    """Incremental sync: stop at the first already-known record."""
    def handler(request):
        return httpx.Response(200, json={
            "items": [{"reference": "new"}, {"reference": "known"}, {"reference": "older"}],
            "nextPagePath": "/api/v0/equity/history/transactions?limit=50&cursor=n",
        })

    with make_client(handler) as client:
        items = client.history_transactions(
            stop_when=lambda i: i["reference"] == "known"
        )

    assert [i["reference"] for i in items] == ["new"]


def test_positions_returns_empty_list_when_payload_is_not_a_list():
    def handler(request):
        return httpx.Response(200, json={"unexpected": "shape"})

    with make_client(handler) as client:
        assert client.positions() == []


def test_different_endpoints_still_respect_a_global_floor():
    """
    Rate limits are per account, so four different endpoints fired back-to-back
    is still a burst. This is what produced a live 403 before the global floor
    was added -- per-endpoint pacing alone let them all through with no gap.
    """
    def handler(request):
        if "positions" in request.url.path:
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={"items": [], "nextPagePath": None})

    sleep = RecordingSleep()
    with make_client(handler, sleep=sleep) as client:
        client.positions()
        client.history_transactions()
        client.history_dividends()

    # Three distinct endpoints, so per-endpoint pacing contributes nothing;
    # every gap here comes from the global floor.
    assert len(sleep.calls) == 2
    for wait in sleep.calls:
        assert wait == pytest.approx(GLOBAL_MIN_INTERVAL, abs=0.1)


def test_forbidden_is_reported_as_permissions_or_burst_not_environment():
    def handler(request):
        return httpx.Response(403)

    with make_client(handler) as client:
        with pytest.raises(T212AuthError) as excinfo:
            client.positions()

    message = str(excinfo.value)
    assert "permission" in message.lower()
    # The environment-mismatch advice belongs to 401 and would mislead here.
    assert "environment-specific" not in message
