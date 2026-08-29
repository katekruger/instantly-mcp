# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `tests/test_policy.py`: dedicated tests for the autonomy engine — every
  hard-blocked tool (not just one), the rolling 24h volume caps actually
  reading the audit log (including a stale-record window, a malformed line,
  and cross-metric isolation), the campaign allow/denylist, `confirm=true`
  bypassing a hard block, `PolicyConfig.from_env` parsing real environment
  variables, and audit-log secret redaction. `policy.py` is the module the
  project's safety claims rest on; it now has 100% line and branch coverage.
- Coverage measurement (`pytest-cov`) in CI, with a 95% floor specific to
  `policy.py` — a regression in the safety module fails the build even though
  the project-wide number does not have a threshold yet.
- `CODE_OF_CONDUCT.md`, `.github/dependabot.yml`, and
  `.github/ISSUE_TEMPLATE/` (bug report form + a security-advisory redirect).

### Fixed

- `pyproject.toml` pinned `mcp>=1.2.0` with no upper bound, which now resolves
  to mcp 2.x. That release renamed `FastMCP` to `MCPServer` and reshaped the
  auth-provider APIs `auth.py` and `oauth.py` are built on, so a clean install
  failed at import (`ModuleNotFoundError` chained from
  `mcp.server.fastmcp`). Pinned to `mcp>=1.2.0,<2`. Migrating to the 2.x API
  is tracked separately.

### Changed

- Repository renamed `instantlymcp` -> `instantly-mcp`; every clone command,
  badge, and URL in the docs updated to match.
- The project shipped as `instantly-mcp.zip` committed directly to the repo,
  with no browsable source. Unpacked into a real tree (`src/`, `tests/`,
  `docs/`) and the README split into a landing page plus five docs pages
  (tools, autonomy, configuration, hosting, API notes) — see
  [`docs/`](docs/).

## [0.1.0] - 2026-08-29

First release.

### Added

- **40 tools** over the Instantly.ai v2 API: campaign and account analytics,
  leads and lead lists, campaign creation/control, the Unibox, sender
  accounts, the blocklist, and webhooks. Full catalogue in
  [`docs/tools.md`](docs/tools.md).
- **The `confirm` gate**: every create/update/launch/pause/move/delete tool
  takes `confirm: bool = False`. Without it, the tool returns a
  plain-language preview and makes zero HTTP calls.
- **An autonomy policy** (`manual` / `assisted` / `autonomous`) layered on
  top: risk tiers per tool (READ / LOW_WRITE / HIGH_WRITE), an always-on
  hard-block list (`delete_lead`, `delete_webhook`, `pause_account`,
  `remove_from_blocklist`) that never runs without `confirm=true` regardless
  of level, per-call and rolling-24h volume caps, optional campaign
  allow/deny lists, and an append-only `audit.log` with secrets redacted.
  Enforced in code — see [`docs/autonomy.md`](docs/autonomy.md).
- **Two transports**: local stdio (the default — no hosting, no public URL,
  no token) or hosted HTTP/SSE via one env var.
- **Inbound auth for HTTP transports**, enforced by `auth.py`: a bearer token
  checked in constant time, exposed as a single-user OAuth authorization
  server (`oauth.py`) so MCP clients that require dynamic client registration
  can connect. The server refuses to start on an HTTP transport without a
  working token and a `https://` `PUBLIC_URL` — see
  [`docs/hosting.md`](docs/hosting.md).
- **A `Dockerfile`** (non-root, reads `$PORT`) and a Render blueprint
  (`render.yaml`) for hosted deployment.
- **Endpoint behavior verified against the live Instantly v2 API**, with every
  place the real API differed from the obvious reading documented in
  [`docs/api-notes.md`](docs/api-notes.md).
- **A fully mocked test suite** — never touches the live API.

[Unreleased]: https://github.com/katekruger/instantly-mcp/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/katekruger/instantly-mcp/releases/tag/v0.1.0
