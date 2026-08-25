"""OAuth provider tests, weighted toward the ways it could hand out a token.

The dangerous failure here is not "login is broken" -- that is loud. It is
"authorization succeeds without the passphrase", which is silent and hands an
anonymous caller the ability to send mail from the operator's domain.
"""

from __future__ import annotations

import time

import pytest
from mcp.server.auth.provider import AuthorizationParams
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl

from instantly_mcp.oauth import SingleUserOAuthProvider

PASSPHRASE = "correct-horse-battery-staple-correct-horse"
REDIRECT = "https://claude.ai/api/mcp/auth_callback"

# Captured before any test patches time.time, so expiry tests have a fixed base.
real_now = time.time()


@pytest.fixture
def provider():
    return SingleUserOAuthProvider(
        passphrase=PASSPHRASE, public_url="https://mcp.example.com", scope="instantly"
    )


@pytest.fixture
def client():
    return OAuthClientInformationFull(
        client_id="client-123",
        client_secret="shh",
        redirect_uris=[AnyUrl(REDIRECT)],
        scope="instantly",
    )


def params(**kw):
    base = dict(
        state="state-abc",
        scopes=["instantly"],
        code_challenge="challenge-xyz",
        redirect_uri=AnyUrl(REDIRECT),
        redirect_uri_provided_explicitly=True,
    )
    base.update(kw)
    return AuthorizationParams(**base)


async def start(provider, client, **kw):
    """Begin an authorization and return its request id."""
    url = await provider.authorize(client, params(**kw))
    return url.split("rq=")[1]


# --- the gate ---------------------------------------------------------------
async def test_authorize_does_not_redirect_to_client(provider, client):
    """authorize must send the user to the gate, never straight back with a code."""
    url = await provider.authorize(client, params())
    assert url.startswith("https://mcp.example.com/login?rq=")
    assert "code=" not in url
    assert REDIRECT not in url


async def test_correct_passphrase_yields_code(provider, client):
    rq = await start(provider, client)
    redirect, _ = provider.complete_login(rq, PASSPHRASE)
    assert redirect is not None
    assert redirect.startswith(REDIRECT)
    assert "code=" in redirect and "state=state-abc" in redirect


@pytest.mark.parametrize(
    "wrong",
    ["", "nope", PASSPHRASE[:-1], PASSPHRASE + "x", PASSPHRASE.upper()],
)
async def test_wrong_passphrase_yields_nothing(provider, client, wrong):
    rq = await start(provider, client)
    assert provider.complete_login(rq, wrong)[0] is None


def test_unknown_request_id_rejected(provider):
    assert provider.complete_login("never-issued", PASSPHRASE)[0] is None


async def test_expired_request_rejected(provider, client, monkeypatch):
    rq = await start(provider, client)
    # Jump past the parking window rather than sleeping through it.
    monkeypatch.setattr(time, "time", lambda: real_now + 3600)
    assert provider.complete_login(rq, PASSPHRASE)[0] is None


async def test_brute_force_locks_out(provider, client):
    rq = await start(provider, client)
    for _ in range(8):
        provider.complete_login(rq, "wrong")
    assert provider.throttle.locked()
    # Even the correct passphrase is refused while locked.
    rq2 = await start(provider, client)
    assert provider.complete_login(rq2, PASSPHRASE)[0] is None


# --- code handling ----------------------------------------------------------
async def test_code_is_single_use(provider, client):
    rq = await start(provider, client)
    redirect, _ = provider.complete_login(rq, PASSPHRASE)
    code_value = redirect.split("code=")[1].split("&")[0]

    code = await provider.load_authorization_code(client, code_value)
    assert code is not None
    await provider.exchange_authorization_code(client, code)
    # Replaying the same redirect must not mint a second token.
    assert await provider.load_authorization_code(client, code_value) is None


async def test_code_not_usable_by_another_client(provider, client):
    rq = await start(provider, client)
    redirect, _ = provider.complete_login(rq, PASSPHRASE)
    code_value = redirect.split("code=")[1].split("&")[0]

    other = OAuthClientInformationFull(
        client_id="attacker", client_secret="x",
        redirect_uris=[AnyUrl(REDIRECT)], scope="instantly",
    )
    assert await provider.load_authorization_code(other, code_value) is None


async def test_login_request_is_single_use(provider, client):
    rq = await start(provider, client)
    assert provider.complete_login(rq, PASSPHRASE)[0] is not None
    assert provider.complete_login(rq, PASSPHRASE)[0] is None


# --- token verification -----------------------------------------------------
async def test_issued_token_verifies(provider, client):
    rq = await start(provider, client)
    redirect, _ = provider.complete_login(rq, PASSPHRASE)
    code_value = redirect.split("code=")[1].split("&")[0]
    code = await provider.load_authorization_code(client, code_value)
    tok = await provider.exchange_authorization_code(client, code)

    loaded = await provider.load_access_token(tok.access_token)
    assert loaded is not None and loaded.scopes == ["instantly"]


async def test_static_token_still_accepted(provider):
    """Keeps the deployment curl-testable; same secret, so no added exposure."""
    loaded = await provider.load_access_token(PASSPHRASE)
    assert loaded is not None and loaded.client_id == "static-operator-token"


async def test_garbage_token_rejected(provider):
    assert await provider.load_access_token("not-a-real-token") is None


async def test_revoked_token_stops_working(provider, client):
    rq = await start(provider, client)
    redirect, _ = provider.complete_login(rq, PASSPHRASE)
    code_value = redirect.split("code=")[1].split("&")[0]
    code = await provider.load_authorization_code(client, code_value)
    tok = await provider.exchange_authorization_code(client, code)

    loaded = await provider.load_access_token(tok.access_token)
    await provider.revoke_token(loaded)
    assert await provider.load_access_token(tok.access_token) is None


async def test_refresh_rotates(provider, client):
    rq = await start(provider, client)
    redirect, _ = provider.complete_login(rq, PASSPHRASE)
    code_value = redirect.split("code=")[1].split("&")[0]
    code = await provider.load_authorization_code(client, code_value)
    tok = await provider.exchange_authorization_code(client, code)

    rt = await provider.load_refresh_token(client, tok.refresh_token)
    assert rt is not None
    await provider.exchange_refresh_token(client, rt, ["instantly"])
    # Old refresh token must not be reusable after rotation.
    assert await provider.load_refresh_token(client, tok.refresh_token) is None


# --- failure reasons are distinguishable -----------------------------------
async def test_expired_link_reports_expired_not_bad_token(provider, client, monkeypatch):
    """A stale link must not be reported as a credential problem."""
    rq = await start(provider, client)
    monkeypatch.setattr(time, "time", lambda: real_now + 100000)
    redirect, reason = provider.complete_login(rq, PASSPHRASE)
    assert redirect is None and reason == "expired"


async def test_wrong_passphrase_reports_bad_passphrase(provider, client):
    rq = await start(provider, client)
    assert provider.complete_login(rq, "wrong")[1] == "bad_passphrase"


async def test_expired_links_do_not_trigger_lockout(provider, client, monkeypatch):
    """Stale tabs must not lock the real operator out."""
    monkeypatch.setattr(time, "time", lambda: real_now + 100000)
    for _ in range(20):
        provider.complete_login("stale-id", PASSPHRASE)
    assert not provider.throttle.locked()


async def test_login_window_is_generous_enough_for_a_human(provider, client, monkeypatch):
    """Six minutes of hunting for the token must still work."""
    rq = await start(provider, client)
    monkeypatch.setattr(time, "time", lambda: real_now + 360)
    assert provider.complete_login(rq, PASSPHRASE)[0] is not None


# --- registrations survive a restart ---------------------------------------
async def fresh(provider_):
    """A brand-new provider with the same passphrase = the process restarted."""
    return SingleUserOAuthProvider(
        passphrase=PASSPHRASE, public_url="https://mcp.example.com", scope="instantly"
    )


async def test_client_survives_restart(provider, client):
    """The connector's saved client_id must still work after a cold start."""
    info = OAuthClientInformationFull(
        client_id="stable-id", client_secret="placeholder",
        redirect_uris=[AnyUrl(REDIRECT)], scope="instantly",
    )
    await provider.register_client(info)
    issued_secret = info.client_secret

    restarted = await fresh(provider)          # memory wiped
    recovered = await restarted.get_client("stable-id")
    assert recovered is not None
    assert recovered.client_secret == issued_secret


async def test_registration_secret_is_not_the_passphrase(provider):
    """A registered client must never learn the operator's passphrase."""
    info = OAuthClientInformationFull(
        client_id="c1", client_secret="x",
        redirect_uris=[AnyUrl(REDIRECT)], scope="instantly",
    )
    await provider.register_client(info)
    assert PASSPHRASE not in info.client_secret


async def test_secrets_differ_per_client(provider):
    a = OAuthClientInformationFull(client_id="a", client_secret="x",
        redirect_uris=[AnyUrl(REDIRECT)], scope="instantly")
    b = OAuthClientInformationFull(client_id="b", client_secret="x",
        redirect_uris=[AnyUrl(REDIRECT)], scope="instantly")
    await provider.register_client(a)
    await provider.register_client(b)
    assert a.client_secret != b.client_secret


@pytest.mark.parametrize("bad", [
    "https://evil.example.com/callback",
    "https://claude.ai.evil.com/callback",   # suffix-confusion attempt
    "http://attacker.test/cb",
])
async def test_hostile_redirect_uri_rejected(provider, bad):
    """Open redirect = phish the operator and steal the authorization code."""
    from mcp.server.auth.provider import RegistrationError
    info = OAuthClientInformationFull(
        client_id="c", client_secret="x", redirect_uris=[AnyUrl(bad)], scope="instantly",
    )
    with pytest.raises(RegistrationError):
        await provider.register_client(info)


@pytest.mark.parametrize("good", [
    "https://claude.ai/api/mcp/auth_callback",
    "https://claude.com/api/mcp/auth_callback",
    "http://localhost:8080/cb",
])
async def test_legitimate_redirect_uri_accepted(provider, good):
    info = OAuthClientInformationFull(
        client_id="c", client_secret="x", redirect_uris=[AnyUrl(good)], scope="instantly",
    )
    await provider.register_client(info)  # must not raise


async def test_rebuilt_client_is_token_endpoint_usable(provider):
    """token_endpoint_auth_method defaults to None, which the token endpoint
    rejects as unsupported -- so a rebuilt client silently failed /token even
    though its secret was correct. Pin the field."""
    rebuilt = await provider.get_client("some-id-not-in-memory")
    assert rebuilt.token_endpoint_auth_method == "client_secret_post"
    assert "authorization_code" in rebuilt.grant_types
