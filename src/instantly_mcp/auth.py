"""Inbound authentication for the hosted (HTTP) transports.

Over stdio the only possible caller is a process on this machine, so inbound
auth is meaningless and we skip it. The moment the server is reachable over
HTTP, every tool -- including ``launch_campaign``, ``add_leads`` and
``reply_to_email`` -- is exposed to anyone who can resolve the URL. The
autonomy policy in ``policy.py`` is *not* a substitute: ``confirm=true`` is
supplied by the caller, so an anonymous caller simply sets it.

``build_server`` therefore fails closed. An HTTP transport with no
``MCP_AUTH_TOKEN`` refuses to start rather than booting an unauthenticated
relay to the workspace's Instantly account.
"""

from __future__ import annotations

import hmac
import os
import sys

from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions
from mcp.server.fastmcp import FastMCP

from .oauth import SingleUserOAuthProvider

SERVER_NAME = "instantly"

# The scope every issued token carries. Single-tenant, so one scope is enough.
SCOPE = "instantly"

# Shorter than this is not worth calling a secret. 32 chars of urlsafe base64
# is ~192 bits, which is what `secrets.token_urlsafe(32)` produces.
_MIN_TOKEN_LEN = 32

_LOCAL_HOSTS = ("http://127.0.0.1", "http://localhost", "http://0.0.0.0")


class StaticTokenVerifier(TokenVerifier):
    """Verify a single shared bearer token supplied via ``MCP_AUTH_TOKEN``.

    Deliberately not OAuth: there is one client (the Claude connector) and one
    resource owner (this workspace), so a shared secret is the honest primitive
    rather than an authorization-server dance with no second party. Comparison
    is constant-time so a wrong token cannot be recovered by timing replies.
    """

    def __init__(self, token: str) -> None:
        self._token = token

    async def verify_token(self, token: str) -> AccessToken | None:
        if not token or not hmac.compare_digest(token, self._token):
            return None
        return AccessToken(
            token=token,
            client_id="instantly-mcp-connector",
            scopes=[SCOPE],
        )


def _fail(problems: list[str]) -> None:
    """Refuse to start, explaining every problem at once rather than one per run."""
    print(
        "ERROR: refusing to start an HTTP transport without working inbound auth.\n"
        "Every Instantly tool would be exposed to anyone who can reach the URL.\n",
        file=sys.stderr,
    )
    for p in problems:
        print(f"  - {p}", file=sys.stderr)
    print(
        "\nGenerate a token with:\n"
        "  python -c \"import secrets; print(secrets.token_urlsafe(32))\"\n"
        "then set MCP_AUTH_TOKEN and PUBLIC_URL in the host's environment.",
        file=sys.stderr,
    )
    sys.exit(1)


def build_server() -> FastMCP:
    """Construct the FastMCP instance, wiring inbound auth for HTTP transports.

    stdio gets a bare server (local-only, nothing to authenticate). Any HTTP
    transport gets a bearer-token resource server, or no server at all.
    """
    transport = os.environ.get("TRANSPORT", "stdio").strip().lower()
    if transport == "stdio":
        return FastMCP(SERVER_NAME)

    token = os.environ.get("MCP_AUTH_TOKEN", "").strip()
    # Render injects RENDER_EXTERNAL_URL with the service's real https URL. Fall
    # back to it so the first deploy boots: the URL isn't knowable until the
    # service exists, and without this the service would fail closed forever on
    # a chicken-and-egg. An explicit PUBLIC_URL always wins (custom domains).
    public_url = (
        os.environ.get("PUBLIC_URL", "").strip()
        or os.environ.get("RENDER_EXTERNAL_URL", "").strip()
    ).rstrip("/")

    problems: list[str] = []
    if not token:
        problems.append("MCP_AUTH_TOKEN is not set.")
    elif len(token) < _MIN_TOKEN_LEN:
        problems.append(
            f"MCP_AUTH_TOKEN is only {len(token)} chars; use at least {_MIN_TOKEN_LEN}."
        )
    if not public_url:
        problems.append("PUBLIC_URL is not set (the https URL Claude will connect to).")
    elif not (
        public_url.startswith("https://") or public_url.startswith(_LOCAL_HOSTS)
    ):
        problems.append(
            f"PUBLIC_URL must be https:// (got {public_url!r}). "
            "Plain http would send the bearer token in clear text."
        )
    if problems:
        _fail(problems)

    # We act as our own authorization server. A bare token_verifier is not
    # enough: Claude's connector performs dynamic client registration and
    # aborts if there is no registration endpoint to talk to. FastMCP wraps the
    # provider's load_access_token for verification, so the static
    # MCP_AUTH_TOKEN keeps working alongside issued tokens (see oauth.py).
    provider = SingleUserOAuthProvider(
        passphrase=token, public_url=public_url, scope=SCOPE
    )
    server = FastMCP(
        SERVER_NAME,
        host=os.environ.get("HOST", "0.0.0.0"),
        # Hosting platforms inject $PORT; honour it so the container binds right.
        port=int(os.environ.get("PORT", "8000")),
        auth_server_provider=provider,
        auth=AuthSettings(
            issuer_url=public_url,
            resource_server_url=public_url,
            required_scopes=[SCOPE],
            client_registration_options=ClientRegistrationOptions(
                enabled=True, valid_scopes=[SCOPE], default_scopes=[SCOPE]
            ),
        ),
    )
    # Stashed so the login routes in server.py can reach the same instance.
    server._instantly_oauth = provider  # type: ignore[attr-defined]
    return server
