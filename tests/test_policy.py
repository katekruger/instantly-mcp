"""Direct tests of the Policy engine: the module the project's entire safety
claim rests on ("an agent cannot talk its way past a cap, because the cap is
an if statement" — README).

test_tools.py already proves the confirm gate and the basic autonomy tiers
end-to-end through the tool layer. This file covers what that coverage
missed: the rolling 24h volume caps actually reading the audit log, every
hard-blocked tool (not just one of them), the denylist, PolicyConfig.from_env
parsing real environment variables, and audit-log redaction. Each test
targets one specific claim made in README.md / docs/autonomy.md / SECURITY.md
so a broken guarantee fails a specific, named test rather than a generic one.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from instantly_mcp.policy import (
    HARD_BLOCK,
    HIGH_WRITE,
    LOW_WRITE,
    READ,
    Policy,
    PolicyConfig,
    _redact,
)


def cfg(tmp_path, **kw):
    kw.setdefault("audit_log_path", str(tmp_path / "audit.log"))
    return PolicyConfig(**kw)


def write_audit_record(tmp_path, *, metric, count, age_hours=1.0):
    """Append one audit record at a given age, bypassing Policy.record so the
    timestamp can be controlled precisely."""
    path = tmp_path / "audit.log"
    rec = {
        "ts": (datetime.now(timezone.utc) - timedelta(hours=age_hours)).isoformat(),
        "tool": "add_leads",
        "mode": "autonomous",
        "args": {},
        "result": "uploaded=1",
        "metric": metric,
        "count": count,
    }
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")


# ===========================================================================
# READ tier: never gated, no cap, no audit entry possible
# ===========================================================================
def test_read_tier_always_executes_and_is_never_autonomous_flagged():
    p = Policy(PolicyConfig())
    decision = p.evaluate("list_campaigns", READ, confirm=False, preview_text="n/a")
    assert decision.execute is True
    assert decision.autonomous is False  # a read was never "autonomously" permitted; it just runs


# ===========================================================================
# The confirm gate is the manual escape hatch — including through hard-block
# ===========================================================================
def test_confirm_true_bypasses_hard_block():
    """The hard-block list means "cannot run autonomously," not "cannot run."
    confirm=true is the operator's explicit say-so and must still work, even
    for delete_lead — otherwise the tool would be permanently unusable."""
    p = Policy(PolicyConfig(level="manual"))
    decision = p.evaluate("delete_lead", HIGH_WRITE, confirm=True, preview_text="Would DELETE.")
    assert decision.execute is True
    assert decision.autonomous is False
    assert decision.reason == "confirmed"


# ===========================================================================
# Hard-block list: every listed tool, not just one, resists autonomous mode
# ===========================================================================
@pytest.mark.parametrize("tool", sorted(HARD_BLOCK))
def test_every_hard_blocked_tool_resists_autonomous_mode(tmp_path, tool):
    p = Policy(cfg(tmp_path, level="autonomous"))
    decision = p.evaluate(tool, HIGH_WRITE, confirm=False, preview_text="Would ACT.")
    assert decision.execute is False
    assert "hard-blocked" in decision.preview.lower()


def test_hard_block_list_is_exactly_the_documented_set():
    """Pins HARD_BLOCK's membership so a future edit is a deliberate, reviewed
    diff here rather than a silent change to what "hard-blocked" means."""
    assert HARD_BLOCK == frozenset({
        "delete_lead", "delete_leads_bulk", "delete_webhook",
        "pause_account", "remove_from_blocklist",
    })


# ===========================================================================
# Autonomy tiers: assisted only ever grants LOW_WRITE, never HIGH_WRITE
# ===========================================================================
def test_assisted_never_grants_high_write_regardless_of_caps(tmp_path):
    """Even with every cap wide open, "assisted" must not let a HIGH_WRITE
    action skip confirm — only "autonomous" can. If this test starts failing,
    the tier boundary itself has been crossed, not just a cap."""
    p = Policy(cfg(tmp_path, level="assisted", max_campaigns_per_call=1000))
    decision = p.evaluate("pause_campaign", HIGH_WRITE, confirm=False,
                           preview_text="Would PAUSE.", target_campaigns=["camp-1"])
    assert decision.execute is False


def test_unrecognized_tier_under_assisted_fails_closed_to_a_confirm_prompt(tmp_path):
    """assisted's autonomous-execution rule checks `tier == LOW_WRITE`
    explicitly, so a tier value that is neither LOW_WRITE nor HIGH_WRITE (a
    typo when wiring a future tool, or a not-yet-invented tier) falls through
    every specific branch to the generic "needs confirm" preview rather than
    being silently allowed to run or raising. Fixes coverage on that
    catch-all branch and pins the fail-closed behavior it exists for."""
    p = Policy(cfg(tmp_path, level="assisted"))
    decision = p.evaluate("some_future_tool", "MEDIUM_WRITE", confirm=False,
                           preview_text="Would DO something.")
    assert decision.execute is False
    assert "needs confirm=true" in decision.preview.lower()


def test_unrecognized_autonomy_level_string_is_rejected_by_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTONOMY_LEVEL", "godmode")
    monkeypatch.setenv("INSTANTLY_AUDIT_LOG", str(tmp_path / "audit.log"))
    config = PolicyConfig.from_env()
    assert config.level == "manual"  # unrecognized input must fail closed, not open


@pytest.mark.parametrize("raw_level", ["AUTONOMOUS", " autonomous ", "Autonomous"])
def test_autonomy_level_from_env_is_case_and_whitespace_insensitive(
    monkeypatch, tmp_path, raw_level,
):
    monkeypatch.setenv("AUTONOMY_LEVEL", raw_level)
    monkeypatch.setenv("INSTANTLY_AUDIT_LOG", str(tmp_path / "audit.log"))
    assert PolicyConfig.from_env().level == "autonomous"


# ===========================================================================
# Volume caps: the rolling 24h window actually reads the audit log
# ===========================================================================
def test_leads_per_day_cap_blocks_once_prior_usage_plus_new_volume_exceeds_it(tmp_path):
    write_audit_record(tmp_path, metric="leads_added", count=4800, age_hours=2)
    p = Policy(cfg(tmp_path, level="autonomous", max_leads_per_day=5000, max_leads_per_call=1000))
    # 4800 already used today + 300 more would be 5100 > 5000.
    decision = p.evaluate("add_leads", LOW_WRITE, confirm=False, preview_text="Would ADD 300.",
                           volume=300, metric="leads_added")
    assert decision.execute is False
    assert "5000 leads/24h" in decision.preview


def test_leads_per_day_cap_allows_when_prior_usage_plus_new_volume_is_under_it(tmp_path):
    write_audit_record(tmp_path, metric="leads_added", count=100, age_hours=2)
    p = Policy(cfg(tmp_path, level="autonomous", max_leads_per_day=5000, max_leads_per_call=1000))
    decision = p.evaluate("add_leads", LOW_WRITE, confirm=False, preview_text="Would ADD 300.",
                           volume=300, metric="leads_added")
    assert decision.execute is True
    assert decision.autonomous is True


def test_usage_older_than_24h_does_not_count_toward_the_cap(tmp_path):
    """The window is rolling, not a calendar day: a record from 25 hours ago
    must not still be weighing on today's cap."""
    write_audit_record(tmp_path, metric="leads_added", count=4999, age_hours=25)
    p = Policy(cfg(tmp_path, level="autonomous", max_leads_per_day=5000, max_leads_per_call=1000))
    decision = p.evaluate("add_leads", LOW_WRITE, confirm=False, preview_text="Would ADD 300.",
                           volume=300, metric="leads_added")
    assert decision.execute is True


def test_usage_just_inside_the_24h_window_still_counts(tmp_path):
    """A record from 23h59m ago is still "today" and must count — the window
    is rolling from the current instant, not bucketed to a calendar day where
    an operator could time a burst to land just after a reset."""
    write_audit_record(tmp_path, metric="leads_added", count=4999, age_hours=23.99)
    p = Policy(cfg(tmp_path, level="autonomous", max_leads_per_day=5000, max_leads_per_call=1000))
    decision = p.evaluate("add_leads", LOW_WRITE, confirm=False, preview_text="Would ADD 300.",
                           volume=300, metric="leads_added")
    assert decision.execute is False


def test_usage_for_a_different_metric_does_not_contaminate_this_ones_total(tmp_path):
    """The audit log is one shared file for every metric. A record's `metric`
    field must actually be checked, not just its presence — otherwise a busy
    email day could wrongly throttle lead uploads, or vice versa."""
    write_audit_record(tmp_path, metric="emails_sent", count=4999, age_hours=1)
    p = Policy(cfg(tmp_path, level="autonomous", max_leads_per_day=5000, max_leads_per_call=1000))
    decision = p.evaluate("add_leads", LOW_WRITE, confirm=False, preview_text="Would ADD 300.",
                           volume=300, metric="leads_added")
    assert decision.execute is True  # the 4999 emails_sent must not count as leads_added usage


def test_unreadable_audit_log_fails_open_to_zero_usage_not_a_crash(tmp_path):
    """The audit log is meant to be a soft dependency: if it becomes
    unreadable (permissions, a corrupted mount, or here a directory sitting
    where a file is expected), a write must still be evaluable rather than
    raising and taking the whole tool call down with it."""
    unreadable = tmp_path / "audit.log"
    unreadable.mkdir()  # os.path.exists() is True; open(path, "r") raises IsADirectoryError
    p = Policy(cfg(tmp_path, level="autonomous", max_leads_per_day=5000, max_leads_per_call=1000))
    decision = p.evaluate("add_leads", LOW_WRITE, confirm=False, preview_text="Would ADD 300.",
                           volume=300, metric="leads_added")
    assert decision.execute is True


def test_malformed_audit_lines_are_skipped_not_fatal_and_not_counted(tmp_path):
    """A half-written line from a crash mid-append must not take down every
    subsequent cap check (denial of service) or silently inflate/deflate
    usage. It should simply be ignored."""
    path = tmp_path / "audit.log"
    path.write_text(
        "not json at all\n"
        '{"metric": "leads_added", "count": 999}\n'  # valid JSON, no "ts" -> skipped
        '{"metric": "leads_added", "ts": "not-a-timestamp", "count": 999}\n'
        "\n",  # blank line
    )
    p = Policy(cfg(tmp_path, level="autonomous", max_leads_per_day=5000, max_leads_per_call=1000))
    decision = p.evaluate("add_leads", LOW_WRITE, confirm=False, preview_text="Would ADD 300.",
                           volume=300, metric="leads_added")
    assert decision.execute is True  # none of the garbage counted toward usage


def test_emails_per_day_cap_blocks_high_write_even_under_autonomous(tmp_path):
    write_audit_record(tmp_path, metric="emails_sent", count=50, age_hours=1)
    p = Policy(cfg(tmp_path, level="autonomous", max_emails_per_day=50))
    decision = p.evaluate("reply_to_email", HIGH_WRITE, confirm=False,
                           preview_text="Would REPLY.", volume=1, metric="emails_sent")
    assert decision.execute is False
    assert "50 emails/24h" in decision.preview


def test_emails_per_day_cap_allows_the_boundary_email(tmp_path):
    """49 already sent + this 1 == 50, not > 50: the cap is `>`, so the 50th
    email of the day is still allowed. Kept as its own test from the blocking
    case above so a future `>` -> `>=` edit fails exactly one of the two."""
    write_audit_record(tmp_path, metric="emails_sent", count=49, age_hours=1)
    p = Policy(cfg(tmp_path, level="autonomous", max_emails_per_day=50))
    decision = p.evaluate("reply_to_email", HIGH_WRITE, confirm=False,
                           preview_text="Would REPLY.", volume=1, metric="emails_sent")
    assert decision.execute is True


def test_per_call_lead_cap_is_checked_independently_of_the_daily_cap(tmp_path):
    """A single oversized call must be rejected even with an empty audit log
    (zero prior usage) — the per-call cap is not derived from the daily one."""
    p = Policy(cfg(tmp_path, level="autonomous", max_leads_per_call=100, max_leads_per_day=100000))
    decision = p.evaluate("add_leads", LOW_WRITE, confirm=False, preview_text="Would ADD 500.",
                           volume=500, metric="leads_added")
    assert decision.execute is False
    assert "cap 100 per call" in decision.preview


def test_campaigns_per_call_cap_blocks_a_two_campaign_action_under_default_cap(tmp_path):
    """move_lead between two different campaigns targets both the source and
    destination (target_campaigns has 2 entries), so it exceeds the default
    max_campaigns_per_call=1 even under autonomous mode unless the operator
    has explicitly raised the cap. This is easy to miss when reading the
    tool's behavior in isolation, which is exactly why it needs a test."""
    p = Policy(cfg(tmp_path, level="autonomous"))  # default max_campaigns_per_call=1
    decision = p.evaluate("move_lead", HIGH_WRITE, confirm=False, preview_text="Would MOVE.",
                           target_campaigns=["camp-a", "camp-b"])
    assert decision.execute is False
    assert "2 campaigns" in decision.preview


def test_campaigns_per_call_cap_allows_a_single_campaign_action(tmp_path):
    p = Policy(cfg(tmp_path, level="autonomous"))
    decision = p.evaluate("pause_campaign", HIGH_WRITE, confirm=False, preview_text="Would PAUSE.",
                           target_campaigns=["camp-a"])
    assert decision.execute is True


# ===========================================================================
# Scope: allow/deny lists
# ===========================================================================
def test_denylist_blocks_a_named_campaign_even_under_autonomous(tmp_path):
    p = Policy(cfg(tmp_path, level="autonomous", denylist=frozenset({"do-not-touch"})))
    decision = p.evaluate("pause_campaign", HIGH_WRITE, confirm=False, preview_text="Would PAUSE.",
                           target_campaigns=["do-not-touch"])
    assert decision.execute is False
    assert "denylist" in decision.preview


def test_denylist_wins_over_allowlist_for_the_same_campaign(tmp_path):
    """If an operator misconfigures a campaign onto both lists, the safer
    interpretation must win. The code checks denylist first — pin that."""
    p = Policy(cfg(tmp_path, level="autonomous",
                   allowlist=frozenset({"camp-x"}), denylist=frozenset({"camp-x"})))
    decision = p.evaluate("pause_campaign", HIGH_WRITE, confirm=False, preview_text="Would PAUSE.",
                           target_campaigns=["camp-x"])
    assert decision.execute is False


def test_allowlist_permits_a_listed_campaign(tmp_path):
    p = Policy(cfg(tmp_path, level="autonomous", allowlist=frozenset({"camp-x"})))
    decision = p.evaluate("pause_campaign", HIGH_WRITE, confirm=False, preview_text="Would PAUSE.",
                           target_campaigns=["camp-x"])
    assert decision.execute is True


# ===========================================================================
# PolicyConfig.from_env: malformed env input must fail safe, not raise
# ===========================================================================
def test_non_integer_cap_env_var_falls_back_to_the_default(monkeypatch, tmp_path):
    monkeypatch.setenv("INSTANTLY_MAX_LEADS_PER_CALL", "not-a-number")
    monkeypatch.setenv("INSTANTLY_AUDIT_LOG", str(tmp_path / "audit.log"))
    config = PolicyConfig.from_env()
    assert config.max_leads_per_call == 1000  # the documented default, not a crash


def test_allowlist_env_var_is_comma_split_and_trimmed(monkeypatch, tmp_path):
    monkeypatch.setenv("INSTANTLY_CAMPAIGN_ALLOWLIST", " camp-a, camp-b ,,camp-c")
    monkeypatch.setenv("INSTANTLY_AUDIT_LOG", str(tmp_path / "audit.log"))
    config = PolicyConfig.from_env()
    assert config.allowlist == frozenset({"camp-a", "camp-b", "camp-c"})


# ===========================================================================
# Audit log: secrets are redacted before anything touches disk
# ===========================================================================
@pytest.mark.parametrize(
    "key", ["api_key", "INSTANTLY_API_KEY", "token", "Authorization", "password"],
)
def test_redact_masks_every_secret_like_key_case_insensitively(key):
    out = _redact({key: "sk-verysecretvalue123"})
    assert out[key] == "***"


def test_redact_leaves_ordinary_values_untouched():
    out = _redact({"campaign_id": "camp-1", "count": 5})
    assert out == {"campaign_id": "camp-1", "count": 5}


def test_redact_summarizes_long_lists_instead_of_writing_every_value():
    """A leads list is exactly the kind of argument that must never be written
    to disk verbatim — 11+ raw email addresses in a plaintext log file."""
    out = _redact({"leads": [f"user{i}@example.com" for i in range(15)]})
    assert out["leads"] == "[15 items]"


def test_redact_leaves_short_lists_intact():
    out = _redact({"tags": ["a", "b", "c"]})
    assert out["tags"] == ["a", "b", "c"]


def test_executed_write_audit_entry_has_secrets_redacted(tmp_path):
    """End-to-end through Policy.record: an arg dict containing a secret-named
    key must reach disk already masked, not rely on the caller remembering to
    scrub it first."""
    p = Policy(cfg(tmp_path, level="manual"))
    p.record("update_account", {"email": "a@b.com", "api_key": "sk-live-123"},
              "updated", autonomous=False)
    content = (tmp_path / "audit.log").read_text()
    assert "sk-live-123" not in content
    assert '"api_key": "***"' in content


def test_audit_write_failure_does_not_raise(tmp_path):
    """"never let audit I/O break a tool call" (policy.py) is itself a
    guarantee, not just a comment — record() must swallow an OSError rather
    than propagate it and fail an otherwise-successful write."""
    # A directory where record() expects a writable file: open() raises
    # IsADirectoryError, a subclass of OSError, with no monkeypatching needed.
    unwritable = tmp_path / "audit_dir"
    unwritable.mkdir()
    p = Policy(PolicyConfig(audit_log_path=str(unwritable)))
    p.record("pause_campaign", {"campaign_id": "camp-1"}, "paused",  # must not raise
             autonomous=True)
