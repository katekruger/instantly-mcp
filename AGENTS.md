# AGENTS.md

## Stop and read this before you write code

This repo has conventions. Violating them wastes a review cycle. `instantly-mcp`
is an MCP server wrapping the Instantly.ai v2 API — 40 tools for analytics,
leads, campaigns, the Unibox, sender accounts, and webhooks — where every
write is gated behind an explicit `confirm` and a code-enforced autonomy
policy, not a prompt asking nicely.

## Commands

- Install: `pip install -e ".[dev]"` (or `uv sync` — see README)
- Test: `pytest -q`
- Test with coverage: `pytest -q --cov=src/instantly_mcp --cov-report=term-missing`
- Lint: `ruff check src tests`
- Types: `pyright`
- All of it: `ruff check src tests && pyright && pytest -q --cov=src/instantly_mcp --cov-report=term-missing`
- Build: `python -m build` (produces `dist/*.whl` + `dist/*.tar.gz`)

`ruff format --check` is **not** part of this list. This codebase predates a
formatter pass and has never been run through one; adopting it means
reformatting every file in its own reviewed change, not a side effect of
something else. Don't add it to CI or run `ruff format` across the tree
without calling that out explicitly.

## Layout

- `src/instantly_mcp/server.py` — the 40 `@mcp.tool()` definitions, the
  `/login` and `/healthz` routes, and `main()` (console entry point,
  `instantly-mcp` per `[project.scripts]`).
- `src/instantly_mcp/client.py` — `InstantlyClient`, the only thing that
  makes HTTP calls. New Instantly endpoint? It goes through here.
- `src/instantly_mcp/policy.py` — the autonomy-tier model. Read the section
  below before touching this file.
- `src/instantly_mcp/auth.py` / `oauth.py` — inbound auth for HTTP
  transports. See **Credentials** below.
- `src/instantly_mcp/models.py` — Pydantic input models + normalizers. Tool
  signatures accept plain `dict`/`list[dict]` on purpose (simpler JSON
  schemas for FastMCP) and validate through here.
- `src/instantly_mcp/formatting.py` — shapes raw API responses into compact,
  LLM-friendly summaries. Every list tool caps output here.
- `tests/` mirrors `src/instantly_mcp/`, plus `test_server.py` for CLI/entry-
  point behavior. Never put tests inside the package.
- `docs/` — `tools.md`, `autonomy.md`, `configuration.md`, `hosting.md`,
  `api-notes.md`. If you change behavior those describe, update the page,
  not just the README.

**A new tool goes in `server.py`**, follows an existing tool's shape
(docstring states the tier; `confirm: bool = False` for any write; call
`p.evaluate(...)` before touching the client), and gets a row in
`docs/tools.md`'s table. See **The autonomy-tier model** for which tier and
whether it belongs in `HARD_BLOCK`.

## Credentials

- `INSTANTLY_API_KEY` — the outbound Instantly v2 key. Read from the
  environment only (`client.py`'s `from_env()`), **never** accepted as a
  tool argument, never logged, never written to a config file. Redacted from
  the audit log by key-name matching (`policy.py`'s `_redact`) as a second
  line of defense.
- Two inbound auth paths, both only relevant when `TRANSPORT` is an HTTP
  transport (stdio has no inbound auth at all — the only caller is a local
  process):
  1. **The raw `MCP_AUTH_TOKEN`**, presented directly as a bearer token.
     Checked first, in constant time, in `oauth.py`'s `load_access_token`.
  2. **A token issued through the OAuth flow** — dynamic client
     registration, then a passphrase-gated `/login` page (the *same*
     `MCP_AUTH_TOKEN` doubles as the passphrase), then a real
     authorization-code exchange. This exists because Claude's connector
     refuses a bare shared secret and performs dynamic client registration;
     see `oauth.py`'s module docstring for why auto-approving that
     registration would be worse than no auth at all.
  - `auth.py` also defines `StaticTokenVerifier`. It is unit-tested but
     **not wired into the running server** — `build_server()` always
     constructs `SingleUserOAuthProvider` for HTTP transports. Don't assume
     it's an active second code path; it currently isn't reachable at
     runtime.
  - `MCP_AUTH_TOKEN` under 32 chars, or a `PUBLIC_URL` that isn't
     `https://`, and the server **refuses to start** on an HTTP transport
     (`auth.py`'s `_fail`). Never relax this to make a demo boot faster.

## The autonomy-tier model — non-negotiable

This is the point of the project. Everything else is a client for the
Instantly API; this is what makes it safe to hand to an agent that can call
its own tools unsupervised.

- **Three risk tiers** (`policy.py`): `READ` (always allowed, no gate),
  `LOW_WRITE` (reversible, small blast radius), `HIGH_WRITE` (irreversible or
  wide blast radius).
- **`confirm: bool = False`** on every write tool. Without it, and without
  the autonomy policy explicitly permitting it, the tool returns a preview
  string and makes **zero HTTP calls**. `confirm=True` always executes — it
  is the human's manual escape hatch, and it works even on a hard-blocked
  tool. Hard-blocked means "cannot run *autonomously*," not "cannot run."
- **`AUTONOMY_LEVEL`** (`manual` default / `assisted` / `autonomous`)
  decides what may skip the confirm gate. `assisted` only ever grants
  `LOW_WRITE` — never promote a `HIGH_WRITE` tool to run unattended under
  `assisted`, that boundary is load-bearing, not a default that happens to
  be conservative.
- **`HARD_BLOCK`** (`delete_lead`, `delete_leads_bulk`, `delete_webhook`,
  `pause_account`, `remove_from_blocklist`) is a frozenset no `AUTONOMY_LEVEL`
  can override. `delete_leads_bulk` currently names no real tool — the
  actual bulk-delete tool is `remove_from_blocklist`, already covered under
  its own name — so that entry is inert, not a gap. If you ever add a tool
  under that name, or any bulk-delete tool, it MUST land in `HARD_BLOCK` on
  the same PR that adds it, not as a follow-up.
- **Volume caps** are read from the *audit log itself* — a rolling 24h
  window, not an in-memory counter that resets on restart. `_usage_last_24h`
  must keep failing open to zero usage (not raising) on a missing or
  unreadable log; a cap check that can crash a tool call is worse than no
  cap.
- **Every executed write is audited**, with secret-shaped argument names
  redacted before the line ever reaches disk (`_redact`). A new write tool
  that skips `p.record(...)` after executing breaks the audit trail
  silently — nothing else will catch that in review.

**This pattern is depended on outside this repo.**
[`segment-mcp`](https://github.com/katekruger/segment-mcp) reuses it —
reimplemented natively there rather than taken as a dependency, renamed to
its own vocabulary (`SEGMENT_MCP_MODE`'s `read`/`write`/`admin` instead of
`AUTONOMY_LEVEL`'s `manual`/`assisted`/`autonomous`, its own Tier 1–4 instead
of `READ`/`LOW_WRITE`/`HIGH_WRITE`) — see the module docstring at
`src/segment_mcp/modes.py:4`. A change to the *shape* of this model here —
what a tier permits, how confirm interacts with autonomy, what a hard block
means — has a reader in that repo who will not see this diff. State the
change and its rationale explicitly in the PR body even though nothing here
technically imports from there.

## Deployment

A `Dockerfile` (non-root, reads `$PORT`) and `render.yaml` exist and are
verified working — built, boot-tested, and probed live (`/healthz` → 200
unauthenticated, `/mcp` → 401 not 403 without a token, OAuth discovery
endpoints open) as of 2026-08-29/31. **Nothing is currently deployed.** The
operator deliberately chose to stay local-stdio-only rather than pursue a
public Smithery/MCP-Registry listing, weighing the discoverability upside
against a public URL, a bearer token, and the Instantly API key sitting in a
third-party host's dashboard. Don't deploy this, suggest deploying it, or
treat the Docker/Render path as the "real" way to run it without the
operator raising that again — `docs/hosting.md` documents the mechanics for
if/when that changes, not a plan already in motion.

## Release process

- Version lives in exactly one place: `__version__` in
  `src/instantly_mcp/__init__.py`. `pyproject.toml`'s `version` is
  `dynamic`, sourced from there via `[tool.hatch.version]` — don't add a
  second literal.
- Before tagging: bump `__version__`, add a `## [x.y.z] - YYYY-MM-DD`
  section to `CHANGELOG.md` (`release.yml` fails the build if it's missing),
  commit.
- Tag `vX.Y.Z` and push it. `.github/workflows/release.yml` verifies the tag
  matches the package version and the changelog has a section, reruns full
  CI, builds, publishes to PyPI via **Trusted Publishing (OIDC — no API
  token anywhere)** behind a manually-approved `release` environment, and
  creates the GitHub Release from that changelog section.
- **Before the first tag**, the PyPI Trusted Publisher for this
  repo/workflow/environment has to already exist on PyPI's side — the
  publish step has nothing to authenticate against otherwise. This is a
  one-time setup only the account owner can do (pypi.org login required).
- No MCP Registry job in `release.yml`, unlike `segment-mcp`. See
  **Deployment** — publishing a package listing is a smaller, different
  decision than the hosted-remote one already declined, but it hasn't been
  made yet either; don't add that job speculatively.

## What this deliberately does not do

- No public hosting, no Smithery listing, no MCP Registry entry (see
  **Deployment**).
- No `ruff format` adoption yet (see **Commands**).
- No `pyright --strict`. `basic` mode already found and fixed 27 real gaps
  on its first run; `strict` would additionally demand annotations the `mcp`
  SDK's own partially-typed surface and this project's deliberate plain
  `dict`/`Any` tool signatures can't satisfy without an unrelated rewrite.
  See the comment in `pyproject.toml`'s `[tool.pyright]`.
- No credentials accepted as tool arguments, ever — env vars only (see
  **Credentials**).
- No coverage fail-under gate on the whole project yet, only on `policy.py`
  specifically (see the comment in `pyproject.toml`'s `[tool.coverage.report]`).

## Before opening a PR

- [ ] `ruff check src tests`, `pyright`, and `pytest -q` all pass locally
- [ ] A new or changed write tool has a `tests/test_policy.py` or
      `tests/test_tools.py` case proving its tier and, if hard-blocked, that
      it resists `AUTONOMY_LEVEL=autonomous`
- [ ] `CHANGELOG.md` has an entry under `## [Unreleased]`
- [ ] `docs/tools.md` (and any other doc page the change touches) is updated
      in the same PR, not left stale
- [ ] If the change touches `policy.py`'s tier/confirm/hard-block behavior,
      the PR body says so explicitly — `segment-mcp` reused this design and
      won't see this diff

## What gets rejected

- Any write tool without a `confirm: bool = False` gate
- Promoting a `HIGH_WRITE` tool to run unattended under `AUTONOMY_LEVEL=assisted`
- Removing something from `HARD_BLOCK`, or a new destructive tool that
  doesn't go in it, without an explicit decision recorded in the PR body
- A cap or gate check that can raise instead of failing closed
- `INSTANTLY_API_KEY`, `MCP_AUTH_TOKEN`, or any secret accepted as a tool
  argument or CLI flag instead of an environment variable
- Deploying this, or wiring up Smithery/MCP-Registry publishing, without the
  operator explicitly reopening that decision
- A live API call in a test — the suite is fully mocked and must stay that way
