# Contributing

Issues and pull requests are welcome. This is a small project — no CLA, no
templates, no ceremony.

## Getting set up

```bash
git clone https://github.com/katekruger/instantly-mcp.git
cd instantly-mcp
uv sync                          # or: python3.12 -m venv .venv && pip install -e ".[dev]"
```

## Before you open a PR

```bash
pytest -q                                                    # all mocked; no network, no API key required
pytest -q --cov=src/instantly_mcp --cov-report=term-missing  # same, with coverage
ruff check src tests                                         # lint (line length 100, rules in pyproject.toml)
```

CI runs all three against Python 3.11, 3.12 and 3.13, and separately fails the
build if `policy.py` — the safety module — drops below 95% coverage. That
floor is scoped to `policy.py` alone; the rest of the project doesn't have one
yet (see the comment in `pyproject.toml`'s `[tool.coverage.report]`).

## What a good change looks like

- **Tests never hit the live API.** Mock the transport (see `tests/test_tools.py`
  for the `httpx.MockTransport` pattern). A test that spends a verification
  credit or sends mail is a bug.
- **New write tools declare a tier.** Pick `LOW_WRITE` or `HIGH_WRITE` in
  `policy.py`, take `confirm: bool = False`, and return `decision.preview`
  when the policy says no. Copy the shape of an existing tool.
- **A change to `policy.py` needs a test in `tests/test_policy.py`,** not just
  passing existing ones. That file exists because the safety model is the
  point of this project — see its module docstring for what it does and
  doesn't cover, and add to it rather than re-proving the same guarantee a
  different way.
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
