# Hosting the server

Running over stdio needs no hosting at all and is the right default. Host it
only when you need the server to exist while your machine is off — chiefly to
receive Instantly webhooks. Hosting introduces a threat model stdio does not
have, so read this page before deploying.

Transport selection lives in `server.py:main()`, which reads `TRANSPORT`
(default `stdio`). All Instantly logic is in `client.py` + the tools, so switching
is a config change, not a rewrite.

> **Hosting adds a threat model that stdio does not have.** Over stdio the only
> caller is a process on your machine. Over HTTP, every tool — `launch_campaign`,
> `add_leads`, `reply_to_email` — is reachable by anyone who can resolve the URL.
> **`AUTONOMY_LEVEL` does not protect you here:** `confirm` is a parameter the
> *caller* supplies, so an anonymous caller simply sets it to `true`. The blast
> radius is mail sent from your domain, i.e. your sending reputation.

Inbound auth is therefore mandatory for HTTP transports and enforced by
`auth.py`: a bearer token (`MCP_AUTH_TOKEN`) checked in constant time, exposed
as an OAuth resource server so MCP clients negotiate it natively. The server
**refuses to start** on an HTTP transport if the token is missing, shorter than
32 chars, or if `PUBLIC_URL` is not `https://`. It fails closed by design — a
misconfigured deploy is a dead server, never an open one.

```bash
export TRANSPORT=streamable-http   # or: sse
export HOST=0.0.0.0
export PORT=8000
export MCP_AUTH_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export PUBLIC_URL=https://your-app.example.com
instantly-mcp
```

Verify enforcement after deploying — no token must be rejected, the real token accepted:

```bash
BODY='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"probe","version":"1"}}}'
H='-H Content-Type:application/json -H Accept:application/json,text/event-stream'

# expect 401
curl -s -o /dev/null -w '%{http_code}\n' -X POST "$PUBLIC_URL/mcp" $H -d "$BODY"
# expect 200
curl -s -o /dev/null -w '%{http_code}\n' -X POST "$PUBLIC_URL/mcp" $H \
  -H "Authorization: Bearer $MCP_AUTH_TOKEN" -d "$BODY"
```

A `Dockerfile` is included (non-root, reads `$PORT`). Set `INSTANTLY_API_KEY`,
`MCP_AUTH_TOKEN` and `PUBLIC_URL` as **secrets** in your host's dashboard — never
commit them.

If you only need this on your own machine, prefer stdio: it needs no hosting, no
public URL, and no token, because there is no network exposure to defend.

## Connecting Claude to the hosted server

Claude's connector will not accept a bare shared secret: it performs dynamic
client registration against whatever authorization server the protected-resource
metadata names, and gives up if there isn't one. So the server is its own
authorization server (`oauth.py`), and the token is *issued* through a real flow.

1. In Claude, add a custom connector pointing at `https://your-app.example.com/mcp`.
2. Claude registers itself and sends you to the server's `/login` page.
3. **Enter your `MCP_AUTH_TOKEN` on that page.** It doubles as the login
   passphrase — there is no separate password.
4. The server issues an access token and redirects Claude back. The tools appear.

Why a login page at all: dynamic client registration is open by design — that's
how Claude enrolls without pre-created credentials. If authorization
auto-approved, anyone who found the URL could register a client, walk the flow,
mint a valid token, and send mail from your domain. The passphrase gate is the
only thing standing between an anonymous visitor and that token, so it is
rate-limited and every failure renders an identical message (a wrong passphrase,
an unknown request id and an expired request are indistinguishable to a prober —
though the operator is told which of "expired", "locked" or "incorrect" applies).

Issued tokens live in memory only. A restart costs a re-login, which is a better
trade than writing credentials to a disk that is ephemeral on most hosts anyway.
Registered clients *do* survive restarts, so Claude does not have to re-register.

`GET /healthz` is unauthenticated and returns `{"status": "ok"}` and nothing
else — point your platform's health check there, never at `/mcp`.

## Webhooks

Create webhooks with `create_webhook(url, event_types=[...])` pointing at your with `create_webhook(url, event_types=[...])` pointing at your
public URL. Receiving webhooks requires (a) a publicly reachable URL and (b) an
Instantly plan tier that includes webhooks. Minimal receiver stub:

```python
# webhook_receiver.py — run alongside the hosted server
from http.server import BaseHTTPRequestHandler, HTTPServer
import json

class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        print("Instantly event:", payload.get("event_type"), payload)
        # TODO: enqueue for your agent to react to (e.g. reply_received)
        self.send_response(200); self.end_headers(); self.wfile.write(b"ok")

HTTPServer(("0.0.0.0", 9000), Handler).serve_forever()
```

---

---

Next: [Configuration](configuration.md) · [Safety and autonomy](autonomy.md)
