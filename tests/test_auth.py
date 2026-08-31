"""Inbound auth tests: token verification and fail-closed server construction.

These lock in a security property, not a convenience: an HTTP transport must
never come up without a working bearer token. A regression here silently turns
the deployment into an open relay to the workspace's Instantly account, so the
refusal cases matter more than the happy path.
"""

from __future__ import annotations

import secrets

import pytest

from instantly_mcp.auth import SCOPE, StaticTokenVerifier, build_server

GOOD = secrets.token_urlsafe(32)
HTTPS = "https://instantly.example.com"


def http_env(monkeypatch, **overrides):
    """Base env for a hosted deploy; pass None to unset a key."""
    env = {"TRANSPORT": "streamable-http", "MCP_AUTH_TOKEN": GOOD, "PUBLIC_URL": HTTPS}
    env.update(overrides)
    for k, v in env.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)


# --- token verification -----------------------------------------------------
async def test_correct_token_accepted():
    result = await StaticTokenVerifier(GOOD).verify_token(GOOD)
    assert result is not None
    assert result.scopes == [SCOPE]


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "wrong",
        GOOD[:-1],          # truncated
        GOOD[:-1] + "X",    # near-miss, differs only in last char
        GOOD + "X",         # extended
        " " + GOOD,         # whitespace-padded
    ],
)
async def test_bad_tokens_rejected(bad):
    assert await StaticTokenVerifier(GOOD).verify_token(bad) is None


# --- fail-closed construction ----------------------------------------------
def test_stdio_needs_no_token(monkeypatch):
    """Local stdio has no network exposure, so it must not demand a token."""
    monkeypatch.setenv("TRANSPORT", "stdio")
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("PUBLIC_URL", raising=False)
    assert build_server().settings.auth is None


def test_http_with_good_config_builds(monkeypatch):
    http_env(monkeypatch)
    auth = build_server().settings.auth
    assert auth is not None and auth.required_scopes == [SCOPE]


@pytest.mark.parametrize(
    "overrides",
    [
        {"MCP_AUTH_TOKEN": None},              # no token at all
        {"MCP_AUTH_TOKEN": "short"},           # below the entropy floor
        {"PUBLIC_URL": None},                  # nowhere to anchor the resource
        {"PUBLIC_URL": "http://evil.test"},    # would leak the token in clear text
    ],
    ids=["no-token", "short-token", "no-url", "plain-http-url"],
)
def test_http_refuses_to_start(monkeypatch, overrides):
    http_env(monkeypatch, **overrides)
    with pytest.raises(SystemExit) as exc:
        build_server()
    assert exc.value.code == 1


def test_localhost_http_allowed_for_testing(monkeypatch):
    """http is tolerable when it cannot leave the machine."""
    http_env(monkeypatch, PUBLIC_URL="http://127.0.0.1:8000")
    assert build_server().settings.auth is not None


def test_render_external_url_used_as_fallback(monkeypatch):
    """First Render deploy must boot before PUBLIC_URL can possibly be known."""
    http_env(monkeypatch, PUBLIC_URL=None)
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://instantly-mcp.onrender.com")
    assert build_server().settings.auth is not None


def test_explicit_public_url_beats_render_fallback(monkeypatch):
    http_env(monkeypatch, PUBLIC_URL="https://custom.example.com")
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://ignored.onrender.com")
    auth = build_server().settings.auth
    assert auth is not None
    assert str(auth.resource_server_url).rstrip("/") == "https://custom.example.com"


def test_render_fallback_still_rejects_plain_http(monkeypatch):
    """The fallback must not become a hole in the https requirement."""
    http_env(monkeypatch, PUBLIC_URL=None)
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "http://insecure.onrender.com")
    with pytest.raises(SystemExit):
        build_server()
