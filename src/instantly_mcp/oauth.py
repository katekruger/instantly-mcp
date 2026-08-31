"""A single-user OAuth authorization server.

Claude's connector refuses a bare shared secret: it performs dynamic client
registration against the authorization server named in our protected-resource
metadata, and gives up if that server isn't there. So the token has to be
*issued* through a real flow rather than pre-shared.

The security hinge is ``authorize``. Dynamic client registration is open by
design -- that is how Claude enrolls itself without pre-created credentials --
so if authorization auto-approved, anyone who found the URL could register a
client, walk the flow, mint a valid token and drive the Instantly account. That
would be strictly worse than the shared secret it replaces, while looking more
official. ``authorize`` therefore hands off to a passphrase-gated login page and
only mints a code once the operator proves they hold ``MCP_AUTH_TOKEN``.

State is deliberately in-memory: this is one operator with one connector, and a
restart costing a re-login is a better trade than persisting credentials to a
disk that (on Render's free tier) is ephemeral anyway.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from typing import Any
from urllib.parse import urlparse

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    RegistrationError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyUrl

# Hosts allowed as OAuth callbacks. Override via OAUTH_REDIRECT_HOSTS (comma
# separated) if a client uses a callback domain not listed here -- a rejected
# registration shows up as "invalid_redirect_uri" at connect time.
ALLOWED_REDIRECT_HOSTS = tuple(
    h.strip().lower()
    for h in os.environ.get(
        "OAUTH_REDIRECT_HOSTS", "claude.ai,claude.com,anthropic.com"
    ).split(",")
    if h.strip()
)

# Used when rebuilding a client we no longer have in memory.
DEFAULT_REDIRECT_URIS = (
    "https://claude.ai/api/mcp/auth_callback",
    "https://claude.com/api/mcp/auth_callback",
)

AUTH_CODE_TTL = 300           # 5 min: just long enough to finish a redirect.
# The parked authorization request, by contrast, waits on a *human* finding and
# pasting a token. Five minutes is not enough for that and produces a confusing
# dead link; fifteen is still short enough that an abandoned request goes stale.
LOGIN_REQUEST_TTL = 900
ACCESS_TOKEN_TTL = 3600       # 1 hour.
REFRESH_TOKEN_TTL = 60 * 60 * 24 * 30

# A wrong passphrase is cheap to retry over the network, so make it expensive
# after a burst. Single operator, so a low ceiling costs nothing legitimate.
_MAX_ATTEMPTS = 8
_LOCKOUT_SECONDS = 300


class LoginThrottle:
    """Crude global throttle on passphrase attempts.

    Not per-IP: behind a proxy the client address is easily spoofed, and with a
    single legitimate operator a global lockout is the conservative choice --
    it fails closed for an attacker and costs the operator a five-minute wait.
    """

    def __init__(self) -> None:
        self._failures = 0
        self._locked_until = 0.0

    def locked(self) -> bool:
        return time.time() < self._locked_until

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= _MAX_ATTEMPTS:
            self._locked_until = time.time() + _LOCKOUT_SECONDS
            self._failures = 0

    def record_success(self) -> None:
        self._failures = 0


class SingleUserOAuthProvider(
    OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]
):
    """OAuth server for exactly one resource owner, gated by a shared passphrase."""

    def __init__(self, passphrase: str, public_url: str, scope: str) -> None:
        self._passphrase = passphrase
        self._public_url = public_url.rstrip("/")
        self._scope = scope
        self.throttle = LoginThrottle()

        self._clients: dict[str, OAuthClientInformationFull] = {}
        self._codes: dict[str, AuthorizationCode] = {}
        self._access: dict[str, AccessToken] = {}
        self._refresh: dict[str, RefreshToken] = {}
        # Authorization requests parked while the operator logs in.
        self._pending: dict[str, tuple[str, AuthorizationParams, float]] = {}

    # --- client registration ------------------------------------------------
    def _derive_secret(self, client_id: str) -> str:
        """Deterministic client secret, so registrations survive a restart.

        Storing registrations in memory meant every restart invalidated the
        connector's saved client_id -- and it will not re-register, because it
        believes it already did, so the connector wedges permanently. On a host
        that spins down when idle that happens within the hour. Deriving the
        secret from the client_id instead makes registration stateless: any
        client_id we ever issued can be re-validated after a cold start without
        a database.
        """
        return hmac.new(
            self._passphrase.encode(), client_id.encode(), hashlib.sha256
        ).hexdigest()

    def _redirect_allowed(self, uri: str) -> bool:
        host = (urlparse(str(uri)).hostname or "").lower()
        if host in ("localhost", "127.0.0.1"):
            return True
        return any(host == d or host.endswith("." + d) for d in ALLOWED_REDIRECT_HOSTS)

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        cached = self._clients.get(client_id)
        if cached:
            return cached
        # Not in memory: either a different process or a post-restart request.
        # Rebuild it. An attacker cannot exploit this by inventing a client_id,
        # because the token endpoint checks the derived secret, which requires
        # the passphrase to compute.
        return OAuthClientInformationFull(
            client_id=client_id,
            client_secret=self._derive_secret(client_id),
            redirect_uris=[AnyUrl(u) for u in DEFAULT_REDIRECT_URIS],
            scope=self._scope,
            # Must be set explicitly: it defaults to None, which the token
            # endpoint's client authenticator treats as an unsupported auth
            # method and rejects. The registration handler defaults new clients
            # to client_secret_post, so match that.
            token_endpoint_auth_method="client_secret_post",
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
        )

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        # Registration is open by design, so without this an attacker could
        # register a client pointing at their own callback, phish the operator
        # into logging in, and receive the authorization code. Constraining
        # redirect targets is what stops this being an open redirector.
        for uri in client_info.redirect_uris or []:
            if not self._redirect_allowed(str(uri)):
                raise RegistrationError(
                    error="invalid_redirect_uri",
                    error_description=f"redirect_uri host not allowed: {uri}",
                )
        # client_id is populated by the registration handler before this is
        # called, per the SDK's own registration flow -- not optional in
        # practice, only in the field's general type.
        assert client_info.client_id is not None
        # Mutating here propagates to the registration response the client
        # stores, which is what makes the secret reproducible later.
        client_info.client_secret = self._derive_secret(client_info.client_id)
        client_info.client_secret_expires_at = 0  # never expires
        self._clients[client_info.client_id] = client_info

    # --- authorization ------------------------------------------------------
    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        """Park the request and send the operator to the passphrase gate.

        Returning the login URL rather than a redirect back to the client is
        what makes this a real gate: no code exists until the passphrase checks
        out in :meth:`complete_login`.
        """
        request_id = secrets.token_urlsafe(24)
        # The SDK's own client model allows client_id to be None in general,
        # but this method is only ever called with a client this provider
        # itself issued an id to (via register_client/get_client) -- both
        # always populate it. A missing id here would mean the SDK called us
        # out of its documented sequence, which is a bug worth a loud failure
        # rather than silently parking an unusable request.
        assert client.client_id is not None
        self._pending[request_id] = (
            client.client_id,
            params,
            time.time() + LOGIN_REQUEST_TTL,
        )
        return f"{self._public_url}/login?rq={request_id}"

    def pending_exists(self, request_id: str) -> bool:
        entry = self._pending.get(request_id)
        return bool(entry) and entry[2] >= time.time()

    def complete_login(self, request_id: str, passphrase: str) -> tuple[str | None, str]:
        """Verify the passphrase; return ``(redirect_url, reason)``.

        ``reason`` distinguishes an expired link from a wrong passphrase. That
        does technically reveal whether a request id is live, but ids are 192
        bits of randomness, so guessing one is infeasible and the oracle is
        worthless -- whereas telling a stale link "incorrect token" sends the
        operator hunting for a credential problem that does not exist.
        """
        if self.throttle.locked():
            return None, "locked"

        entry = self._pending.get(request_id)
        if not entry or entry[2] < time.time():
            # Not a failed credential attempt, so it must not count toward the
            # lockout -- otherwise a few stale tabs lock out the real operator.
            self._pending.pop(request_id, None)
            return None, "expired"

        if not passphrase or not hmac.compare_digest(passphrase, self._passphrase):
            self.throttle.record_failure()
            return None, "bad_passphrase"

        self.throttle.record_success()
        client_id, params, _ = self._pending.pop(request_id)

        code = AuthorizationCode(
            code=secrets.token_urlsafe(32),
            scopes=params.scopes or [self._scope],
            expires_at=time.time() + AUTH_CODE_TTL,
            client_id=client_id,
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource,
            subject="operator",
        )
        self._codes[code.code] = code
        return (
            construct_redirect_uri(
                str(params.redirect_uri), code=code.code, state=params.state
            ),
            "ok",
        )

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        code = self._codes.get(authorization_code)
        if not code or code.client_id != client.client_id:
            return None
        if code.expires_at < time.time():
            self._codes.pop(authorization_code, None)
            return None
        return code

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        # Single-use: burn the code on exchange so a replayed redirect is inert.
        self._codes.pop(authorization_code.code, None)
        assert client.client_id is not None  # see authorize()'s comment
        return self._issue(
            client_id=client.client_id,
            scopes=authorization_code.scopes,
            resource=authorization_code.resource,
        )

    # --- refresh ------------------------------------------------------------
    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        tok = self._refresh.get(refresh_token)
        if not tok or tok.client_id != client.client_id:
            return None
        if tok.expires_at and tok.expires_at < time.time():
            self._refresh.pop(refresh_token, None)
            return None
        return tok

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        self._refresh.pop(refresh_token.token, None)  # rotate on use
        assert client.client_id is not None  # see authorize()'s comment
        return self._issue(
            client_id=client.client_id,
            scopes=scopes or refresh_token.scopes,
            resource=None,
        )

    # --- token verification -------------------------------------------------
    async def load_access_token(self, token: str) -> AccessToken | None:
        # The static MCP_AUTH_TOKEN stays valid alongside issued tokens. It is
        # the same secret as the login passphrase, so this adds no exposure, and
        # it keeps the deployment curl-testable without walking a browser flow.
        if hmac.compare_digest(token, self._passphrase):
            return AccessToken(
                token=token,
                client_id="static-operator-token",
                scopes=[self._scope],
                subject="operator",
            )

        tok = self._access.get(token)
        if not tok:
            return None
        if tok.expires_at and tok.expires_at < time.time():
            self._access.pop(token, None)
            return None
        return tok

    async def revoke_token(self, token: Any) -> None:
        value = getattr(token, "token", token)
        self._access.pop(value, None)
        self._refresh.pop(value, None)

    # --- internals ----------------------------------------------------------
    def _issue(self, client_id: str, scopes: list[str], resource: str | None) -> OAuthToken:
        now = int(time.time())
        access = AccessToken(
            token=secrets.token_urlsafe(32),
            client_id=client_id,
            scopes=scopes,
            expires_at=now + ACCESS_TOKEN_TTL,
            resource=resource,
            subject="operator",
        )
        refresh = RefreshToken(
            token=secrets.token_urlsafe(32),
            client_id=client_id,
            scopes=scopes,
            expires_at=now + REFRESH_TOKEN_TTL,
            subject="operator",
        )
        self._access[access.token] = access
        self._refresh[refresh.token] = refresh
        return OAuthToken(
            access_token=access.token,
            expires_in=ACCESS_TOKEN_TTL,
            scope=" ".join(scopes),
            refresh_token=refresh.token,
        )
