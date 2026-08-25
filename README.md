# Instantly MCP server (local)

A local [MCP](https://modelcontextprotocol.io) server that wraps the
**Instantly.ai v2 API** so an MCP client (Claude Desktop / Cowork) can read
analytics and manage leads, campaigns, the Unibox, sender accounts, the
blocklist, and webhooks.

- **Transport:** local stdio (launched by your MCP client — no hosting, no public URL). Switchable to hosted HTTP/SSE via one env var (see [Going hosted later](#going-hosted-later)).
- **Auth:** Instantly v2 Bearer token, read from `INSTANTLY_API_KEY` (never hardcoded).
- **Base URL:** `https://api.instantly.ai/api/v2`
- **Safety:** every write/destructive action is gated behind `confirm=true`, plus a configurable [autonomy policy](#autonomy-policy) with volume caps, a hard-block list, and an audit log — all enforced in code.

Endpoint paths and shapes were verified against the live reference at
<https://developer.instantly.ai/>. Places where the real API differed from a
naive guess are marked with `# NOTE:` comments in the source (and summarized at
the bottom of this file).

---

## Repository layout

```
src/instantly_mcp/
  server.py      MCP server: tool definitions, transport selection, entry point
  client.py      Instantly v2 API client (httpx) — all HTTP lives here
  models.py      Pydantic models for requests/responses
  policy.py      Risk tiers, autonomy levels, volume caps, audit log
  auth.py        Inbound bearer-token auth for HTTP transports
  oauth.py       OAuth resource/authorization server for MCP clients
  formatting.py  Human-readable previews and result rendering
tests/           Fully mocked — never hits the live API
Dockerfile       Container image for hosted deployment
render.yaml      Render blueprint (secrets prompted, never committed)
.env.example     Every supported env var, documented
```

## Install

Requires **Python 3.11+**.

```bash
git clone https://github.com/katekruger/instantlymcp.git
cd instantlymcp

# Option A — uv (preferred)
uv sync

# Option B — venv + pip
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Get an API key

1. In Instantly, go to **Settings → Integrations → API**.
2. Create a v2 API key. Instantly supports **scoped** keys — start with a
   **read-only** key to try analytics safely, then widen scopes once you trust it.
3. Set it in your environment:

```bash
export INSTANTLY_API_KEY=your_key_here
```

## Smoke test (no network calls at import)

```bash
# Fails cleanly if the key is unset:
unset INSTANTLY_API_KEY
instantly-mcp        # -> "ERROR: INSTANTLY_API_KEY is not set", exit 1

# Starts (stdio server waits on stdin) with any non-empty key:
INSTANTLY_API_KEY=dummy instantly-mcp
# It will not make any HTTP calls until a tool is invoked. Ctrl-C to stop.
```

Run the tests (all mocked — never hits the live API):

```bash
pytest -q
```

---

## Register it with Claude (local stdio)

Add this block to your MCP client config. Adjust the absolute path.

```json
{
  "mcpServers": {
    "instantly": {
      "command": "uv",
      "args": ["--directory", "/absolute/path/to/instantly-mcp", "run", "instantly-mcp"],
      "env": { "INSTANTLY_API_KEY": "your_key_here" }
    }
  }
}
```

If you installed with venv + pip instead of uv, point `command` at the venv's
entry point and drop `args`:

```json
{
  "mcpServers": {
    "instantly": {
      "command": "/absolute/path/to/instantly-mcp/.venv/bin/instantly-mcp",
      "env": { "INSTANTLY_API_KEY": "your_key_here" }
    }
  }
}
```

**Where the config file lives (macOS, this machine):**
- **Claude Desktop:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Claude Code (CLI):** register with
  `claude mcp add instantly -e INSTANTLY_API_KEY=your_key_here -- uv --directory /absolute/path/to/instantly-mcp run instantly-mcp`
  (or edit `~/.claude.json`).

Restart the client, confirm the `instantly` tools appear, and run a **read-only**
call first (e.g. "list my Instantly campaigns"). Then try a write — it will show
you a **preview** before executing.

---

## Tools

Reads are always allowed. Writes are gated (see the autonomy tiers below).

### Analytics (read)
| Tool | What it does |
|---|---|
| `get_campaign_analytics` | Counts + rates for one campaign over a date range |
| `get_account_analytics` | Workspace daily sending analytics, aggregated |
| `get_campaign_steps_analytics` | Per-step / per-variant breakdown |
| `list_campaigns` | id, name, status, created (with status filter) |
| `get_campaign` | Full campaign config |

### Leads (read + write)
| Tool | Tier |
|---|---|
| `list_leads`, `get_lead`, `search_leads_by_email` | READ |
| `add_leads` | LOW_WRITE |
| `update_lead`, `set_lead_interest_status` | LOW_WRITE |
| `move_lead` | HIGH_WRITE |
| `delete_lead` | HIGH_WRITE · **hard-blocked** |

### Lead lists (read + write)
`list_lead_lists` (READ) · `create_lead_list` (LOW_WRITE)

### Campaign control (write)
`update_campaign`, `create_campaign`, `launch_campaign`, `pause_campaign` — all HIGH_WRITE.

`preview_campaign_build` is READ-only and performs zero HTTP calls. It renders the campaign,
variants, schedule, sender allocation, tags, and lead-list mapping before creation. Creation
still starts paused; launching remains a separate confirmed action.

### Emails / Unibox (read + write)
| Tool | Tier |
|---|---|
| `list_emails`, `get_email`, `count_unread` | READ |
| `mark_thread_read` | LOW_WRITE |
| `reply_to_email`, `forward_email` | HIGH_WRITE |

### Sender accounts / mailboxes (read + write)
| Tool | Tier |
|---|---|
| `list_accounts`, `get_account` | READ |
| `resume_account`, `update_account` | HIGH_WRITE |
| `pause_account` | HIGH_WRITE · **hard-blocked** |

### Blocklist (read + write)
| Tool | Tier |
|---|---|
| `list_blocklist` | READ |
| `add_to_blocklist` | LOW_WRITE |
| `remove_from_blocklist` | HIGH_WRITE · **hard-blocked** |

### Deliverability, workspace, webhooks
`verify_email` (spends a credit) · `get_workspace` (READ) ·
`list_webhooks`, `list_webhook_event_types` (READ) ·
`create_webhook` (HIGH_WRITE) · `delete_webhook` (HIGH_WRITE · **hard-blocked**).

---

## Write-action safety (the `confirm` gate)

Every create/update/launch/pause/move/delete tool takes `confirm: bool = False`.

- With `confirm=false` (default) and no autonomous permission, the tool **does not
  call the API**. It returns a plain-language **preview** of exactly what it would
  do — e.g. `"Would PAUSE campaign camp-1. … Re-call with confirm=true to execute."`
  Previews are **network-free**, so they're instant and safe.
- With `confirm=true`, it executes and returns the result.
- Destructive tools say **DESTRUCTIVE** in the preview.

## Autonomy policy

The `confirm` gate is the manual model. On top of it, an **autonomy policy** lets
routine work run without a human confirming each call, while hard-blocking risky
actions. Configure it with env vars (see `.env.example`).

**Risk tiers** (assigned per tool):
- **READ** — always allowed, no confirm, no cap.
- **LOW_WRITE** — reversible, low blast radius (`add_leads`, `set_lead_interest_status`, `update_lead`, `mark_thread_read`, `add_to_blocklist`, `create_lead_list`).
- **HIGH_WRITE** — high blast radius or irreversible (`launch_campaign`, `pause_campaign`, `create_campaign`, `update_campaign`, `move_lead`, `delete_lead`, `reply_to_email`, `forward_email`, `pause_account`, `resume_account`, `update_account`, `remove_from_blocklist`, `create_webhook`, `delete_webhook`).

**`AUTONOMY_LEVEL`** (operator chooses):

| Level | LOW_WRITE | HIGH_WRITE |
|---|---|---|
| `manual` (default) | needs `confirm=true` | needs `confirm=true` |
| `assisted` | runs autonomously within caps | needs `confirm=true` |
| `autonomous` | runs within caps | runs within caps, **except the hard-block list** |

**Hard-block list** (never runs without `confirm=true`, at any level):
`delete_lead`, `delete_webhook`, `pause_account`, `remove_from_blocklist`, and bulk deletes.

**Volume caps** (env-configurable, enforced in code — exceeding one forces a
`confirm` preview even in `autonomous` mode):
`INSTANTLY_MAX_LEADS_PER_CALL` (1000), `INSTANTLY_MAX_LEADS_PER_DAY` (5000),
`INSTANTLY_MAX_EMAILS_PER_DAY` (50), `INSTANTLY_MAX_CAMPAIGNS_PER_CALL` (1).
Rolling-24h usage is computed from the audit log.

**Allow/deny lists:** `INSTANTLY_CAMPAIGN_ALLOWLIST` / `INSTANTLY_CAMPAIGN_DENYLIST`
(comma-separated campaign UUIDs) restrict which campaigns autonomous actions may touch.

**Audit log:** every executed write is appended to `audit.log` (timestamp, tool,
args minus secrets, result, and whether it ran autonomously or was confirmed).
This is your paper trail for anything the agent did unattended. It may contain
lead emails, so it's gitignored.

**Idempotency:** `add_leads` dedupes by email within a call and honors
`skip_if_in_workspace`, so re-running a scheduled job doesn't double-load leads.

---

## Running independently

Two different meanings of "independent":

1. **Acting without you confirming each step** — solved by the autonomy policy.
   Set `AUTONOMY_LEVEL=assisted` (or `autonomous`) with caps and an allowlist, and
   the agent does routine work (pull analytics, add enriched leads, triage the
   Unibox, blocklist bad addresses) on its own while irreversible actions stay
   gated and everything is logged.

2. **Running when you're not driving / your machine is off** — local stdio has a
   hard ceiling here (no public URL, so it can't receive webhooks; it only exists
   while your machine + client run). Two ways to close the gap:
   - **Scheduled polling (do this first, works with local):** use your client's
     scheduled tasks to run a prompt on a cadence — e.g. every morning: *"pull
     yesterday's Instantly analytics, list new Unibox replies, and flag anything
     that needs me."*
   - **Hosted + webhooks (true always-on):** deploy this same codebase to a small
     always-on box and point Instantly webhooks at it (see below).

## Going hosted later

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
commit them. To connect from Claude, add a custom connector pointing at
`$PUBLIC_URL/mcp` and supply the bearer token.

If you only need this on your own machine, prefer stdio: it needs no hosting, no
public URL, and no token, because there is no network exposure to defend.

Then create webhooks with `create_webhook(url, event_types=[...])` pointing at your
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

## Notes / caveats

- The API key stays in env / headers only — never committed. `.env` and `audit.log`
  are gitignored; only `.env.example` is committed.
- Autonomy caps and the hard-block list are enforced in code, so a prompt can't
  talk the server past them.
- If Instantly changes their v2 API, patch `client.py` and the affected tool.
- Webhooks and some features require higher Instantly plan tiers — the server
  degrades gracefully with a clear error if the key's plan/scope can't reach an
  endpoint.

### Where the live docs differed from a naive spec (handled in code)

- **`add_leads`** → `POST /leads/add` (campaign_id/list_id at top level; rich
  response with `leads_uploaded`, `skipped_count`, etc.). `skip_if_in_workspace` exists.
- **`list_leads`** → `POST /leads/list` (not GET); campaign filter field is
  `campaign`, envelope is `{items, next_starting_after}`.
- **Campaign analytics** → `GET /campaigns/analytics` returns an **array**; fields
  are `emails_sent_count`, `open_count_unique`, `reply_count_unique`,
  `link_click_count_unique`, `bounced_count`, `unsubscribed_count`,
  `total_opportunities` (mapped to "opportunities"; there's no literal
  "interested" count).
- **launch/pause** → `POST /campaigns/{id}/activate` and `/pause`.
- **`set_lead_interest_status`** → `POST /leads/update-interest-status`, keyed by
  **`lead_email`** (not lead id) with `interest_value`.
- **`move_lead`** → `POST /leads/move` is **async** (returns a BackgroundJob) and
  needs the source campaign/list as well as the destination.
- **Blocklist** → paths are `/block-lists-entries` with `{"bl_values": [...]}`.
- **Emails** → reply/forward require `eaccount` + `subject` + a `body` object; the
  tools auto-derive these from the original email when omitted. `mark_thread_read`
  is `POST /emails/threads/{thread_id}/mark-as-read`. `count_unread` →
  `/emails/unread/count`.
- **Accounts** → keyed by **email** in the path; analytics is
  `GET /accounts/analytics/daily`.
- **Webhooks** → `POST /webhooks` takes a **singular** `event_type`, so
  `create_webhook` creates one webhook per requested type.
- **Workspace** → `GET /workspaces/current`.
- **Inbox-placement test create/get** were omitted — the public reference didn't
  document a clean create/get pair; the inbox-placement *analytics* endpoints exist
  and can be added later if needed.
```
