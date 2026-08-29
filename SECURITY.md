# Security policy

## Reporting a vulnerability

Report security issues privately through
[GitHub's private advisory form](https://github.com/katekruger/instantly-mcp/security/advisories/new)
rather than opening a public issue. Please include what you did, what happened,
and what you expected. This is a personal project, so response times are
best-effort.

## What this server is protecting

The blast radius of a compromise here is **mail sent from your domain** — that
is, your sending reputation — plus read access to your lead data. The design
assumes an LLM will call these tools unsupervised at some point, so the
guardrails live in code where a prompt cannot reach them.

**Enforced in code, not in prompt text:**

- Every create/update/launch/pause/move/delete tool requires `confirm=true`.
  Without it the tool returns a preview and makes zero HTTP calls.
- `delete_lead`, `delete_webhook`, `pause_account` and `remove_from_blocklist`
  are hard-blocked: they always require `confirm=true`, at any autonomy level.
- Per-call and rolling-24h volume caps. Exceeding one forces a preview even at
  `AUTONOMY_LEVEL=autonomous`.
- Optional campaign allow/deny lists that bound what autonomous actions may touch.
- Every executed write is appended to an audit log with secret-looking keys
  (`key`, `token`, `secret`, `password`, `authorization`) redacted.

**On HTTP transports**, inbound auth is mandatory and the server fails closed —
it refuses to start if `MCP_AUTH_TOKEN` is missing or under 32 characters, or if
`PUBLIC_URL` is not `https://`. A misconfigured deploy is a dead server, never
an open one. The OAuth authorization endpoint is gated behind a rate-limited
passphrase page; see [docs/hosting.md](docs/hosting.md).

## What it is not protecting against

- **A malicious or careless operator.** `confirm=true` is always an escape hatch.
- **A caller who already has your bearer token.** The token is the whole
  perimeter on HTTP transports; `AUTONOMY_LEVEL` is not a second factor, because
  `confirm` is supplied by the caller.
- **Your Instantly API key's own scopes.** The server cannot do more than the key
  allows — use a read-only key until you need writes.

## Handling secrets

- Never commit `INSTANTLY_API_KEY`, `MCP_AUTH_TOKEN`, or a deploy token. `.env`
  and `audit.log` are gitignored; only `.env.example` is committed.
- Set secrets in your host's dashboard. `render.yaml` marks them `sync: false`
  so Render prompts for them instead of reading them from the repo.
- The audit log may contain lead email addresses. Treat it as personal data.
- If a secret does reach a public commit, rotate it — deleting the file does not
  remove it from git history.
