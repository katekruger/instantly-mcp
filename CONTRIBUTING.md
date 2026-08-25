# Contributing

Issues and pull requests are welcome. This is a small project — no CLA, no
templates, no ceremony.

## Getting set up

```bash
git clone https://github.com/katekruger/instantlymcp.git
cd instantlymcp
uv sync                          # or: python3.12 -m venv .venv && pip install -e ".[dev]"
```

## Before you open a PR

```bash
pytest -q                 # all mocked; no network, no API key required
ruff check src tests      # lint (line length 100, rules configured in pyproject.toml)
```

CI runs both against Python 3.11, 3.12 and 3.13.

## What a good change looks like

- **Tests never hit the live API.** Mock the transport (see `tests/test_tools.py`
  for the `httpx.MockTransport` pattern). A test that spends a verification
  credit or sends mail is a bug.
- **New write tools declare a tier.** Pick `LOW_WRITE` or `HIGH_WRITE` in
  `policy.py`, take `confirm: bool = False`, and return `decision.preview`
  when the policy says no. Copy the shape of an existing tool.
- **Previews make zero HTTP calls.** The preview path must be safe to run
  against a production workspace.
- **Anything irreversible goes in `HARD_BLOCK`.** If getting it wrong would cost
  someone their sending reputation or their data, it should not be reachable
  autonomously.
- **Document env vars in three places** when you add one: `.env.example`, the
  table in `docs/configuration.md`, and wherever it changes behavior.
- **Tool docstrings are the model's documentation.** They become the description
  the MCP client shows Claude, so write them for the caller, and state the tier.
- **Record API surprises.** If the live Instantly v2 API differs from what the
  docs imply, leave a `# NOTE:` at the call site and add a line to
  `docs/api-notes.md`.

## Reporting a security issue

Don't open a public issue — see [SECURITY.md](SECURITY.md).
