# Instantly MCP server

[![CI](https://github.com/katekruger/instantly-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/katekruger/instantly-mcp/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![MCP](https://img.shields.io/badge/MCP-server-6f42c1.svg)](https://modelcontextprotocol.io)

An [MCP](https://modelcontextprotocol.io) server that puts your **Instantly.ai**
cold-email workspace in front of an AI client. Ask Claude for last week's reply
rate, load enriched leads into a campaign, triage the Unibox, pause a mailbox
that's burning reputation — 40 tools over the Instantly v2 API.

**The point of the project is the safety model.** Every write is gated behind an
explicit `confirm`, autonomy is a tiered policy with volume caps and a
hard-block list, and all of it is enforced in code — not asked for in a prompt.
An agent cannot talk its way past a cap, because the cap is an `if` statement.

```
You:    "Launch the Design Partners campaign."
Claude: → launch_campaign(campaign_id="camp-1")
        ← "Would LAUNCH (activate) campaign camp-1 — it will start sending.
           AUTONOMY_LEVEL=manual — every write needs confirm=true.
           Re-call with confirm=true to execute."
        This will start sending from your mailboxes. Confirm?
You:    "Yes."
Claude: → launch_campaign(campaign_id="camp-1", confirm=true)   ← now it runs
```

The preview costs **zero HTTP calls**, so nothing reaches Instantly until you say so.

- **Transport:** local stdio by default — no hosting, no public URL, no token. One env var switches it to hosted HTTP/SSE ([Hosting](docs/hosting.md)).
- **Auth:** your Instantly v2 key, read from `INSTANTLY_API_KEY`, never hardcoded and never logged.
- **Verified:** paths and payload shapes checked against the live v2 reference; every place the real API differs from the obvious guess is [written down](docs/api-notes.md).
- **Tested:** the suite is fully mocked and never touches the live API.

---

## Quickstart

Requires **Python 3.11+** and an Instantly account with v2 API access.

**1. Install**

```bash
git clone https://github.com/katekruger/instantly-mcp.git
cd instantly-mcp

# Option A — uv (preferred)
uv sync

# Option B — venv + pip
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

**2. Get an API key**

In Instantly, go to **Settings → Integrations → API** and create a v2 key.
Instantly supports **scoped** keys — start with a **read-only** key to try
analytics safely, then widen the scopes once you trust it.

```bash
export INSTANTLY_API_KEY=your_key_here
```

**3. Check it starts**

```bash
# Fails cleanly if the key is unset:
unset INSTANTLY_API_KEY
instantly-mcp        # -> "ERROR: INSTANTLY_API_KEY is not set", exit 1

# Starts (stdio server waits on stdin) with any non-empty key:
INSTANTLY_API_KEY=dummy instantly-mcp
```

No HTTP call is made until a tool is actually invoked. Ctrl-C to stop.

**4. Register it with your MCP client**

Add this to your client config, adjusting the absolute path:

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

**Where that config lives (macOS):**

- **Claude Desktop:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Claude Code:** `claude mcp add instantly -e INSTANTLY_API_KEY=your_key_here -- uv --directory /absolute/path/to/instantly-mcp run instantly-mcp`

**5. Try it**

Restart the client, confirm the `instantly` tools appear, and start with a read:
*"list my Instantly campaigns"*. Then try a write — you'll get a preview first.

---

## What it can do

40 tools. Reads run freely; writes are tiered. Full signatures and tiers in the
[tool reference](docs/tools.md).

| Area | Tools | Highest tier |
|---|---|---|
| **Analytics** | Campaign, account, and per-step/variant analytics with computed open/reply/click/bounce rates | READ |
| **Leads** | List, get, search by email, add (deduped), update, set interest status, move, delete | HIGH_WRITE |
| **Lead lists** | List, create | LOW_WRITE |
| **Campaigns** | List, get, preview a build with zero writes, create (starts paused), update, launch, pause | HIGH_WRITE |
| **Emails / Unibox** | List, get, count unread, mark thread read, reply, forward | HIGH_WRITE |
| **Sender accounts** | List, get, pause, resume, update | HIGH_WRITE |
| **Blocklist** | List, add, remove | HIGH_WRITE |
| **Deliverability & workspace** | Verify an email, read workspace, list/create/delete webhooks | HIGH_WRITE |

Four of them are **hard-blocked** — `delete_lead`, `delete_webhook`,
`pause_account`, `remove_from_blocklist` — and never run without `confirm=true`,
at any autonomy level, no matter what the policy says.

## The safety model

| Level | LOW_WRITE (reversible) | HIGH_WRITE (irreversible / wide blast radius) |
|---|---|---|
| `manual` *(default)* | needs `confirm=true` | needs `confirm=true` |
| `assisted` | runs unattended, within caps | needs `confirm=true` |
| `autonomous` | runs within caps | runs within caps, **except the hard-block list** |

On top of the tiers: per-call and rolling-24h volume caps (leads, emails,
campaigns), optional campaign allow/deny lists, and an append-only `audit.log`
of every executed write with secrets redacted. Exceeding a cap forces a preview
even at `autonomous`. Details in [Safety and autonomy](docs/autonomy.md).

## Hosting

Local stdio needs no hosting and is the right default — there is no network
exposure to defend. Host it only when the server must exist while your machine
is off, chiefly to receive Instantly webhooks. Over HTTP, inbound auth is
mandatory and the server **fails closed**: no token, a token under 32 chars, or
a non-`https` public URL and it refuses to start. A `Dockerfile` and a Render
blueprint are included. See [Hosting](docs/hosting.md).

---

## Documentation

| Page | What's in it |
|---|---|
| [Tool reference](docs/tools.md) | All 40 tools, grouped by area, with risk tiers |
| [Safety and autonomy](docs/autonomy.md) | The `confirm` gate, autonomy levels, caps, hard-blocks, audit log |
| [Configuration](docs/configuration.md) | Every environment variable, its default, and what it does |
| [Hosting](docs/hosting.md) | HTTP transports, the threat model, OAuth login flow, Docker/Render, webhooks |
| [Implementation notes](docs/api-notes.md) | Where the live Instantly v2 API differs from the obvious reading |
| [Security policy](SECURITY.md) | Reporting a vulnerability; what this server does and doesn't protect |
| [Contributing](CONTRIBUTING.md) | Running the tests and linter, and what a good change looks like |
| [AGENTS.md](AGENTS.md) | Conventions for AI coding agents working in this repo — commands, layout, and the autonomy-tier model as a non-negotiable |
| [Changelog](CHANGELOG.md) | What changed in each release |
| [Code of Conduct](CODE_OF_CONDUCT.md) | Community standards and how to report a violation |

## Repository layout

```
src/instantly_mcp/
  server.py      MCP server: the 40 tool definitions, login route, transport selection
  client.py      Instantly v2 API client (httpx) — all HTTP lives here
  models.py      Pydantic input models and normalizers
  policy.py      Risk tiers, autonomy levels, volume caps, audit log
  auth.py        Inbound bearer-token auth for HTTP transports; fails closed
  oauth.py       Single-user OAuth authorization server for MCP clients
  formatting.py  Compact, LLM-friendly summaries of raw API responses
tests/           Fully mocked — never hits the live API
docs/            The pages listed above
Dockerfile       Container image for hosted deployment (non-root, reads $PORT)
render.yaml      Render blueprint; secrets are prompted, never committed
.env.example     Annotated template for every supported variable
```

## Development

```bash
pytest -q                 # all mocked, no network, no API key needed
ruff check src tests      # lint
```

CI runs both on every push and pull request against Python 3.11, 3.12 and 3.13.

## See also

[segment-mcp](https://github.com/katekruger/segment-mcp) — a read-first MCP
server for Twilio Segment, built on the same read-only-by-default,
risk-tiered safety model this project's `policy.py` pioneered.

## License

[MIT](LICENSE).
