"""Tool + policy tests: the write-action safety gate and autonomy guardrails.

Key guarantee under test: a write tool with confirm=False (in manual mode) returns
a PREVIEW and makes ZERO HTTP calls.
"""

from __future__ import annotations

import httpx
import pytest

from instantly_mcp import server
from instantly_mcp.client import InstantlyClient
from instantly_mcp.models import campaign_build_preview, normalize_leads, resolve_interest_status
from instantly_mcp.policy import Policy, PolicyConfig


class CallLog:
    def __init__(self):
        self.calls = []


def install_client(monkeypatch, handler):
    log = CallLog()

    def wrapped(request: httpx.Request) -> httpx.Response:
        log.calls.append((request.method, request.url.path))
        return handler(request)

    client = InstantlyClient(api_key="test-key",
                             transport=httpx.MockTransport(wrapped))
    monkeypatch.setattr(server, "_client", client)
    return log


def install_policy(monkeypatch, tmp_path, level="manual", **caps):
    cfg = PolicyConfig(level=level, audit_log_path=str(tmp_path / "audit.log"), **caps)
    monkeypatch.setattr(server, "_policy", Policy(cfg))


def boom_handler(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"HTTP must not be called: {request.method} {request.url}")


def ok_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"status": "success", "leads_uploaded": 1,
                                     "id": "new-id", "name": "X", "status_code": 2})


def preview_text(result) -> str:
    if isinstance(result, str):
        return result
    if isinstance(result, dict) and "preview" in result:
        return result["preview"]
    return repr(result)


# --- confirm=False previews and makes ZERO HTTP calls -----------------------
@pytest.mark.parametrize("coro_factory", [
    lambda: server.pause_campaign("camp-1"),
    lambda: server.launch_campaign("camp-1"),
    lambda: server.delete_lead("lead-1"),
    lambda: server.create_lead_list("My List"),
    lambda: server.add_to_blocklist(["bad@example.com"]),
    lambda: server.pause_account("mailbox@example.com"),
    lambda: server.update_account("mailbox@example.com", {"daily_limit": 40}),
    lambda: server.create_campaign("C", "Subj", "Body"),
    lambda: server.reply_to_email("email-1", "hi there"),
    lambda: server.delete_webhook("wh-1"),
])
async def test_write_tools_preview_without_http(monkeypatch, tmp_path, coro_factory):
    log = install_client(monkeypatch, boom_handler)
    install_policy(monkeypatch, tmp_path, level="manual")
    result = await coro_factory()
    assert "confirm=true" in preview_text(result).lower()
    assert log.calls == []  # zero HTTP calls


async def test_add_leads_preview_reports_valid_count(monkeypatch, tmp_path):
    log = install_client(monkeypatch, boom_handler)
    install_policy(monkeypatch, tmp_path, level="manual")
    result = await server.add_leads(
        leads=[{"email": "a@x.com"}, {"email": "a@x.com"}, {"email": "b@x.com"}],
        campaign_id="camp-1",
    )
    assert result["leads_valid"] == 2  # deduped a@x.com
    assert "confirm=true" in result["preview"].lower()
    assert log.calls == []


# --- confirm=True executes even in manual mode ------------------------------
async def test_confirm_true_executes(monkeypatch, tmp_path):
    log = install_client(monkeypatch, ok_handler)
    install_policy(monkeypatch, tmp_path, level="manual")
    result = await server.pause_campaign("camp-1", confirm=True)
    assert log.calls == [("POST", "/api/v2/campaigns/camp-1/pause")]
    assert isinstance(result, dict)


# --- autonomy levels --------------------------------------------------------
async def test_assisted_low_write_runs_autonomously(monkeypatch, tmp_path):
    log = install_client(monkeypatch, ok_handler)
    install_policy(monkeypatch, tmp_path, level="assisted")
    result = await server.add_leads(leads=[{"email": "a@x.com"}], campaign_id="camp-1")
    # LOW_WRITE executes without confirm under "assisted".
    assert result.get("executed") is True
    assert ("POST", "/api/v2/leads/add") in log.calls


async def test_assisted_high_write_still_previews(monkeypatch, tmp_path):
    log = install_client(monkeypatch, boom_handler)
    install_policy(monkeypatch, tmp_path, level="assisted")
    result = await server.pause_campaign("camp-1")  # HIGH_WRITE
    assert "confirm=true" in preview_text(result).lower()
    assert log.calls == []


async def test_autonomous_hard_block_still_previews(monkeypatch, tmp_path):
    log = install_client(monkeypatch, boom_handler)
    install_policy(monkeypatch, tmp_path, level="autonomous")
    # delete_lead is hard-blocked: never runs without confirm, even autonomous.
    result = await server.delete_lead("lead-1")
    assert "hard-blocked" in preview_text(result).lower()
    assert log.calls == []


async def test_autonomous_high_write_runs(monkeypatch, tmp_path):
    log = install_client(monkeypatch, ok_handler)
    install_policy(monkeypatch, tmp_path, level="autonomous")
    result = await server.pause_campaign("camp-1")  # HIGH_WRITE, not hard-blocked
    assert ("POST", "/api/v2/campaigns/camp-1/pause") in log.calls
    assert isinstance(result, dict)


# --- volume caps ------------------------------------------------------------
async def test_per_call_lead_cap_forces_preview(monkeypatch, tmp_path):
    log = install_client(monkeypatch, boom_handler)
    install_policy(monkeypatch, tmp_path, level="autonomous", max_leads_per_call=2)
    leads = [{"email": f"user{i}@x.com"} for i in range(5)]
    result = await server.add_leads(leads=leads, campaign_id="camp-1")
    assert "cap" in preview_text(result).lower()
    assert log.calls == []


async def test_allowlist_blocks_autonomous_off_target(monkeypatch, tmp_path):
    log = install_client(monkeypatch, boom_handler)
    install_policy(monkeypatch, tmp_path, level="autonomous",
                   allowlist=frozenset({"allowed-camp"}))
    result = await server.pause_campaign("other-camp")
    assert "allowlist" in preview_text(result).lower()
    assert log.calls == []


# --- audit log --------------------------------------------------------------
async def test_executed_write_is_audited(monkeypatch, tmp_path):
    install_client(monkeypatch, ok_handler)
    audit = tmp_path / "audit.log"
    install_policy(monkeypatch, tmp_path, level="manual")
    await server.pause_campaign("camp-1", confirm=True)
    assert audit.exists()
    content = audit.read_text()
    assert "pause_campaign" in content
    assert "confirmed" in content


# --- pure helpers -----------------------------------------------------------
def test_normalize_leads_dedupes_and_validates():
    clean, warnings = normalize_leads([
        {"email": "A@X.com"},
        {"email": "a@x.com"},          # dup after lowercasing
        {"first_name": "NoEmail"},      # allowed (list upload)
        {"email": "b@x.com", "bogus_field": 1},  # invalid -> warned
    ])
    emails = [c.get("email") for c in clean]
    assert "a@x.com" in emails
    assert emails.count("a@x.com") == 1
    assert any("duplicate" in w for w in warnings)
    assert any("bogus_field" in w or "skipped" in w for w in warnings)


def test_resolve_interest_status():
    assert resolve_interest_status("interested") == 1
    assert resolve_interest_status("meeting_booked") == 2
    assert resolve_interest_status(-4) == -4
    assert resolve_interest_status("Not Interested") == -1
    with pytest.raises(ValueError):
        resolve_interest_status("nonsense")
    with pytest.raises(ValueError):
        resolve_interest_status(99)


def test_campaign_build_preview_is_paused_and_zero_write():
    result = campaign_build_preview("Design partners", "Hi", "Body", sender_accounts=["a@example.com"], tags=["beta"], lead_list_id="list-1")
    assert result["creation_status"] == "paused"
    assert result["write_performed"] is False
    assert result["summary"]["variant_count"] == 1
