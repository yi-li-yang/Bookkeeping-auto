"""
Enable Banking client tests.

Runs against httpx.MockTransport with a throwaway RSA key, so no credentials,
no network, and no dependency on an actual Enable Banking account.
"""
import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from backend.integrations.enablebanking import (
    ASPSP,
    EBApplicationInactive,
    EBAuthError,
    EBConsentExpired,
    EBError,
    EBRateLimitError,
    EnableBankingClient,
    build_jwt,
    load_private_key,
)

APP_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


@pytest.fixture(scope="module")
def keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return private_pem, public_pem


class RecordingSleep:
    def __init__(self):
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def make_client(keypair, handler, sleep=None):
    private_pem, _ = keypair
    return EnableBankingClient(
        APP_ID, private_pem,
        transport=httpx.MockTransport(handler),
        sleep=sleep or RecordingSleep(),
    )


# -- JWT -------------------------------------------------------------------

def test_jwt_is_rs256_with_the_app_id_in_the_kid_header(keypair):
    private_pem, public_pem = keypair
    token = build_jwt(APP_ID, private_pem, now=1_000_000)

    header = jwt.get_unverified_header(token)
    assert header["alg"] == "RS256"
    # The application id identifies the key on Enable Banking's side, so it
    # belongs in the header, not the payload.
    assert header["kid"] == APP_ID

    # Fixed timestamp, so skip expiry validation -- this asserts how the
    # claims are built, not that this particular token is currently valid.
    claims = jwt.decode(token, public_pem, algorithms=["RS256"],
                        audience="api.enablebanking.com",
                        options={"verify_exp": False})
    assert claims["iss"] == "enablebanking.com"
    assert claims["aud"] == "api.enablebanking.com"
    assert claims["iat"] == 1_000_000
    assert claims["exp"] == 1_000_000 + 3600     # their one-hour cap


def test_jwt_expiry_never_exceeds_an_hour(keypair):
    private_pem, public_pem = keypair
    token = build_jwt(APP_ID, private_pem)
    claims = jwt.decode(token, public_pem, algorithms=["RS256"],
                        audience="api.enablebanking.com")
    assert claims["exp"] - claims["iat"] <= 3600
    assert claims["exp"] > time.time()


def test_token_is_sent_as_a_bearer_header(keypair):
    seen = []

    def handler(request):
        seen.append(request.headers.get("authorization"))
        return httpx.Response(200, json={"aspsps": []})

    with make_client(keypair, handler) as client:
        client.aspsps()

    assert seen[0].startswith("Bearer ey")


def test_missing_application_id_is_an_actionable_error(keypair):
    private_pem, _ = keypair
    with pytest.raises(EBAuthError) as excinfo:
        EnableBankingClient("", private_pem)
    assert "EB_APPLICATION_ID" in str(excinfo.value)


def test_missing_key_file_names_the_path(tmp_path):
    missing = tmp_path / "nope.pem"
    with pytest.raises(EBAuthError) as excinfo:
        load_private_key(missing)
    assert str(missing) in str(excinfo.value)


# -- ASPSP listing (the coverage check) ------------------------------------

def test_aspsps_are_parsed(keypair):
    def handler(request):
        assert request.url.params["country"] == "GB"
        assert request.url.params["psu_type"] == "personal"
        return httpx.Response(200, json={"aspsps": [
            {"name": "Monzo", "country": "GB", "psu_types": ["personal"],
             "auth_methods": [{"name": "redirect"}]},
            {"name": "Chase", "country": "GB", "psu_types": ["personal"]},
        ]})

    with make_client(keypair, handler) as client:
        banks = client.aspsps(country="GB")

    assert [b.name for b in banks] == ["Monzo", "Chase"]
    assert banks[0].psu_types == ("personal",)
    assert banks[0].auth_methods == ("redirect",)


def test_aspsp_handles_a_sparse_payload():
    aspsp = ASPSP.from_api({"name": "X"})
    assert aspsp.country == "" and aspsp.psu_types == ()


# -- error mapping ---------------------------------------------------------

def test_bad_application_credentials_raise_ebautherror(keypair):
    def handler(request):
        return httpx.Response(401, text="bad key")

    with make_client(keypair, handler) as client:
        with pytest.raises(EBAuthError) as excinfo:
            client.aspsps()
    assert "EB_APPLICATION_ID" in str(excinfo.value)


def test_inactive_application_is_not_reported_as_a_credentials_problem(keypair):
    """
    A freshly registered application 403s on every endpoint until an account is
    linked. The JWT verified fine, so blaming the key sends you debugging a
    working credential -- the real fix is to link an account.
    """
    def handler(request):
        return httpx.Response(403, json={"code": 403,
                                         "message": "Application is not active"})

    with make_client(keypair, handler) as client:
        with pytest.raises(EBApplicationInactive) as excinfo:
            client.aspsps()

    message = str(excinfo.value)
    assert "Link at least one account" in message
    assert "Credentials are fine" in message


def test_inactive_check_wins_over_the_consent_path_heuristic(keypair):
    """
    /accounts/... would otherwise be read as an expired consent, which is the
    wrong advice before the application has ever been activated.
    """
    def handler(request):
        return httpx.Response(403, json={"message": "Application is not active"})

    with make_client(keypair, handler) as client:
        with pytest.raises(EBApplicationInactive):
            client.account_transactions("acct-1")


def test_expired_consent_is_distinguished_from_bad_credentials(keypair):
    """
    Both arrive as 401/403 but need different fixes: one means reauthorise the
    bank, the other means the application key is wrong.
    """
    def handler(request):
        return httpx.Response(403, text="consent expired")

    with make_client(keypair, handler) as client:
        with pytest.raises(EBConsentExpired) as excinfo:
            client.account_transactions("acct-123")
    assert "reauthoris" in str(excinfo.value).lower()


def test_rate_limit_honours_retry_after_then_gives_up(keypair):
    def handler(request):
        return httpx.Response(429, headers={"retry-after": "2"})

    sleep = RecordingSleep()
    with make_client(keypair, handler, sleep=sleep) as client:
        with pytest.raises(EBRateLimitError):
            client.aspsps()
    assert 2.0 in sleep.calls


def test_server_errors_are_retried(keypair):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(502)
        return httpx.Response(200, json={"aspsps": []})

    with make_client(keypair, handler) as client:
        assert client.aspsps() == []
    assert calls["n"] == 3


def test_other_client_errors_raise_plain_eberror(keypair):
    def handler(request):
        return httpx.Response(400, text="bad request")

    with make_client(keypair, handler) as client:
        with pytest.raises(EBError):
            client.aspsps()


# -- consent flow ----------------------------------------------------------

def test_start_authorisation_posts_the_expected_body(keypair):
    captured = {}

    def handler(request):
        import json
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"url": "https://bank.example/authorise"})

    with make_client(keypair, handler) as client:
        result = client.start_authorisation(
            aspsp_name="Monzo", country="GB",
            redirect_url="http://localhost:8000/api/banks/callback",
            state="abc123", valid_until="2027-02-20T00:00:00Z",
        )

    assert result["url"].startswith("https://")
    assert captured["aspsp"] == {"name": "Monzo", "country": "GB"}
    assert captured["state"] == "abc123"
    assert captured["access"]["valid_until"] == "2027-02-20T00:00:00Z"


def test_create_session_exchanges_the_callback_code(keypair):
    def handler(request):
        import json
        assert json.loads(request.content) == {"code": "auth-code"}
        return httpx.Response(200, json={
            "session_id": "sess-1",
            "accounts": [{"uid": "acct-1"}, {"uid": "acct-2"}],
        })

    with make_client(keypair, handler) as client:
        session = client.create_session("auth-code")

    assert session["session_id"] == "sess-1"
    assert len(session["accounts"]) == 2


# -- pagination ------------------------------------------------------------

def test_transactions_follow_the_continuation_key(keypair):
    seen_keys = []

    def handler(request):
        key = request.url.params.get("continuation_key")
        seen_keys.append(key)
        if key is None:
            return httpx.Response(200, json={
                "transactions": [{"entry_reference": "a"}],
                "continuation_key": "K1",
            })
        return httpx.Response(200, json={
            "transactions": [{"entry_reference": "b"}], "continuation_key": None,
        })

    with make_client(keypair, handler) as client:
        txns = client.account_transactions("acct-1")

    assert [t["entry_reference"] for t in txns] == ["a", "b"]
    assert seen_keys == [None, "K1"]


def test_pagination_respects_the_page_cap(keypair):
    def handler(request):
        return httpx.Response(200, json={
            "transactions": [{"entry_reference": "x"}], "continuation_key": "more",
        })

    with make_client(keypair, handler) as client:
        txns = client.account_transactions("acct-1", max_pages=4)

    assert len(txns) == 4


def test_date_from_is_passed_through(keypair):
    def handler(request):
        assert request.url.params["date_from"] == "2026-01-01"
        return httpx.Response(200, json={"transactions": [], "continuation_key": None})

    with make_client(keypair, handler) as client:
        client.account_transactions("acct-1", date_from="2026-01-01")
