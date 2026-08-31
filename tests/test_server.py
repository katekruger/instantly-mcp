"""``instantly-mcp --help`` / ``--version`` must not require configuration.

``main()`` used to ignore ``sys.argv`` entirely and run the fatal
``INSTANTLY_API_KEY`` check unconditionally, so ``--help`` died with a config
error instead of printing usage -- the first thing anyone types after
installing. Both flags must exit 0 with no ``INSTANTLY_API_KEY`` set and
without making any network call.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from instantly_mcp import server


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "instantly_mcp.server", *args],
        env={},  # deliberately empty -- no INSTANTLY_API_KEY, no PATH
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_help_exits_zero_with_no_configuration_and_names_the_tool():
    result = _run_cli("--help")
    assert result.returncode == 0
    assert "instantly-mcp" in result.stdout.lower()


def test_version_exits_zero_with_no_configuration():
    result = _run_cli("--version")
    assert result.returncode == 0
    assert result.stdout.strip()


def test_arg_parser_help_exits_zero_without_touching_the_environment(capsys):
    # In-process complement to the subprocess tests above: proves the parser
    # itself exits 0 on --help before anything in main() that would need
    # INSTANTLY_API_KEY runs.
    with pytest.raises(SystemExit) as exc_info:
        server._build_arg_parser().parse_args(["--help"])
    assert exc_info.value.code == 0
    assert "instantly-mcp" in capsys.readouterr().out.lower()


def test_arg_parser_version_exits_zero(capsys):
    with pytest.raises(SystemExit) as exc_info:
        server._build_arg_parser().parse_args(["--version"])
    assert exc_info.value.code == 0
    assert capsys.readouterr().out.strip()


def test_no_flags_still_requires_the_api_key():
    """The fix must not accidentally make the config check optional -- run
    with zero args (no --help/--version) and no INSTANTLY_API_KEY, it must
    still reach and enforce that check. Run via subprocess, not
    server.main() in-process: argparse reads real sys.argv by default, which
    in-process would be pytest's *own* CLI args, not an empty argv."""
    result = _run_cli()
    assert result.returncode == 1
    assert "INSTANTLY_API_KEY is not set" in result.stderr
