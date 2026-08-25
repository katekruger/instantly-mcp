# Configuration

Everything is configured through environment variables — nothing is read from a
committed file. `.env.example` is the annotated template; copy it to `.env` for
local work (it is gitignored), or set the same variables as secrets in your
host's dashboard when deploying.

Only `INSTANTLY_API_KEY` is required for local stdio use. Everything else has a
working default.

## Required

| Variable | Default | What it does |
|---|---|---|
| `INSTANTLY_API_KEY` | — | Your Instantly v2 API key. The server exits with a clear error if it is unset. Generate it in Instantly → Settings → Integrations → API. Instantly supports **scoped** keys — start read-only. |

## Transport

| Variable | Default | What it does |
|---|---|---|
| `TRANSPORT` | `stdio` | `stdio` (local, launched by your MCP client), `streamable-http`, or `sse`. Anything other than `stdio` turns on the mandatory inbound-auth checks below. |
| `HOST` | `0.0.0.0` | Bind address. HTTP transports only. |
| `PORT` | `8000` | Bind port. HTTP transports only; hosting platforms usually inject this. |
| `INSTANTLY_BASE_URL` | `https://api.instantly.ai/api/v2` | Override only if Instantly changes their host. |

## Required when hosted (`TRANSPORT` ≠ `stdio`)

The server **refuses to start** if these are missing or malformed — see
[Hosting](hosting.md) for why.

| Variable | Default | What it does |
|---|---|---|
| `MCP_AUTH_TOKEN` | — | The shared secret Claude must present. Minimum 32 chars. Doubles as the passphrase on the `/login` page during the OAuth flow. Generate with `python -c "import secrets; print(secrets.token_urlsafe(32))"`. |
| `PUBLIC_URL` | `RENDER_EXTERNAL_URL` if set | The public `https://` URL clients connect to. Must be `https://` (`http://127.0.0.1` is allowed for local tests) — a bearer token over plain http travels in clear text. |
| `OAUTH_REDIRECT_HOSTS` | `claude.ai,claude.com,anthropic.com` | Comma-separated hosts accepted as OAuth callbacks. A client on an unlisted callback domain fails registration with `invalid_redirect_uri`. |

## Autonomy policy

Full semantics in [Safety and autonomy](autonomy.md).

| Variable | Default | What it does |
|---|---|---|
| `AUTONOMY_LEVEL` | `manual` | `manual`, `assisted`, or `autonomous`. An unrecognized value falls back to `manual`. |
| `INSTANTLY_MAX_LEADS_PER_CALL` | `1000` | Cap on leads in a single `add_leads` call. |
| `INSTANTLY_MAX_LEADS_PER_DAY` | `5000` | Rolling-24h lead cap, computed from the audit log. |
| `INSTANTLY_MAX_EMAILS_PER_DAY` | `50` | Rolling-24h cap on replies and forwards. |
| `INSTANTLY_MAX_CAMPAIGNS_PER_CALL` | `1` | Cap on campaigns touched per call. |
| `INSTANTLY_CAMPAIGN_ALLOWLIST` | empty | Comma-separated campaign UUIDs. If set, autonomous actions may touch **only** these campaigns. |
| `INSTANTLY_CAMPAIGN_DENYLIST` | empty | Comma-separated campaign UUIDs autonomous actions may never touch. |
| `INSTANTLY_AUDIT_LOG` | `audit.log` | Where executed writes are appended. May contain lead email addresses, so it is gitignored. |

Exceeding any cap forces a `confirm` preview even at `AUTONOMY_LEVEL=autonomous`.

---

Next: [Safety and autonomy](autonomy.md) · [Hosting](hosting.md) · [Tool reference](tools.md)
