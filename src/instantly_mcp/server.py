"""FastMCP app for the Instantly.ai v2 API.

Tool docstrings are the descriptions the MCP client shows the model. Every
write/destructive tool takes ``confirm: bool = False`` and, when not confirmed
(and not permitted autonomously by the policy), returns a plain-language PREVIEW
string and makes ZERO HTTP calls. See ``policy.py`` for the autonomy model and
the README for the tool-to-tier table.

# NOTE: Endpoint paths/shapes below were verified against the live v2 reference
# at https://developer.instantly.ai/ . Places where the original build spec
# differed from the real API are called out with `# NOTE:` comments.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Optional

from . import formatting as fmt
from .auth import build_server
from .client import InstantlyClient
from .models import (
    campaign_build_preview,
    default_schedule,
    normalize_leads,
    resolve_interest_status,
    simple_sequence,
)
from .policy import HIGH_WRITE, LOW_WRITE, Policy

# Built from $TRANSPORT: bare over stdio, bearer-token resource server over
# HTTP. Refuses to start an HTTP transport with no token -- see auth.py.
mcp = build_server()


_LOGIN_PAGE = """<!doctype html>
<meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Connect Instantly MCP</title>
<style>
 body{{font:16px/1.5 system-ui,sans-serif;background:#111;color:#eee;
      display:grid;place-items:center;min-height:100vh;margin:0}}
 form{{background:#1c1c1c;padding:2rem;border-radius:12px;width:min(92vw,26rem)}}
 h1{{font-size:1.1rem;margin:0 0 .25rem}} p{{color:#999;font-size:.85rem;margin:0 0 1.25rem}}
 input{{width:100%;padding:.7rem;font-size:1rem;border-radius:8px;border:1px solid #333;
        background:#111;color:#eee;box-sizing:border-box}}
 button{{width:100%;margin-top:.75rem;padding:.7rem;font-size:1rem;border:0;
         border-radius:8px;background:#e5633a;color:#fff;cursor:pointer}}
 .err{{color:#ff8a7a;font-size:.85rem;margin-top:.75rem}}
</style>
<form method=post>
  <h1>Connect Instantly MCP</h1>
  <p>Paste the access token for this server to authorize the connector.</p>
  <input type=password name=passphrase autofocus autocomplete=current-password
         placeholder="Access token">
  <button type=submit>Authorize</button>
  {error}
</form>
"""


@mcp.custom_route("/login", methods=["GET", "POST"])
async def login(request):
    """Passphrase gate standing in front of the OAuth authorization code.

    Dynamic client registration is open by design, so this page is the only
    thing separating an anonymous visitor from a token that can send mail from
    the operator's domain. Every failure -- wrong passphrase, unknown request,
    expired request -- renders the identical message, so the page cannot be used
    to enumerate valid request ids.
    """
    from starlette.responses import HTMLResponse, RedirectResponse

    provider = getattr(mcp, "_instantly_oauth", None)
    if provider is None:  # stdio build has no OAuth server
        return HTMLResponse("Not Found", status_code=404)

    request_id = request.query_params.get("rq", "")

    if request.method == "GET":
        if not provider.pending_exists(request_id):
            return HTMLResponse(
                _LOGIN_PAGE.format(
                    error='<p class=err>This link has expired. Reconnect from Claude.</p>'
                ),
                status_code=400,
            )
        return HTMLResponse(_LOGIN_PAGE.format(error=""))

    form = await request.form()
    redirect_to, reason = provider.complete_login(request_id, form.get("passphrase", ""))
    if redirect_to is None:
        # An expired link and a wrong token need different actions from the
        # operator, so say which it was rather than blaming the credential.
        message = {
            "expired": "This link has expired. Reconnect from Claude to get a new one.",
            "locked": "Too many attempts. Wait a few minutes and try again.",
        }.get(reason, "Incorrect token.")
        return HTMLResponse(
            _LOGIN_PAGE.format(error=f'<p class=err>{message}</p>'),
            status_code=410 if reason == "expired" else 401,
        )
    return RedirectResponse(redirect_to, status_code=302)


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(request):  # noqa: ARG001 - signature fixed by starlette
    """Unauthenticated liveness probe for the hosting platform.

    Deliberately separate from ``/mcp``: that route answers 401 without a bearer
    token (correctly), which a platform health check would read as a permanently
    failing service. Returns a bare literal -- no workspace, plan, config or
    version detail -- so an anonymous prober learns only that something is up.
    """
    from starlette.responses import JSONResponse

    return JSONResponse({"status": "ok"})

_client: Optional[InstantlyClient] = None
_policy: Optional[Policy] = None


def get_client() -> InstantlyClient:
    global _client
    if _client is None:
        _client = InstantlyClient.from_env()
    return _client


def get_policy() -> Policy:
    global _policy
    if _policy is None:
        _policy = Policy()
    return _policy


# ===========================================================================
# Analytics (read-only)
# ===========================================================================
@mcp.tool()
async def get_campaign_analytics(
    campaign_id: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Any:
    """Analytics for ONE campaign over an optional date range (YYYY-MM-DD).

    Returns sent/opens/replies/clicks/bounces/unsubscribes/opportunities counts
    plus computed open/reply/click/bounce rates.
    """
    data = await get_client()._request(
        "GET", "/campaigns/analytics",
        params={"id": campaign_id, "start_date": start_date, "end_date": end_date},
    )
    summary = fmt.summarize_analytics_list(data)
    if isinstance(summary, list):
        return summary[0] if len(summary) == 1 else summary
    return summary


@mcp.tool()
async def get_account_analytics(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Any:
    """Workspace-level daily sending analytics, aggregated across accounts.

    # NOTE: backed by GET /accounts/analytics/daily (there is no single
    # "account analytics overview" endpoint in v2). Returns per-day rows plus
    # a computed totals+rates roll-up.
    """
    data = await get_client()._request(
        "GET", "/accounts/analytics/daily",
        params={"start_date": start_date, "end_date": end_date},
    )
    return fmt.summarize_account_analytics(data)


@mcp.tool()
async def get_campaign_steps_analytics(campaign_id: str) -> Any:
    """Per-sequence-step (and variant) analytics for one campaign."""
    data = await get_client()._request(
        "GET", "/campaigns/analytics/steps",
        params={"campaign_id": campaign_id},
    )
    return fmt.summarize_step_analytics(data)


@mcp.tool()
async def list_campaigns(status: Optional[int] = None, limit: int = 50) -> Any:
    """List campaigns (id, name, status, created).

    Optional `status` filter (int): 0=draft, 1=active, 2=paused, 3=completed,
    4=running_subsequences, -1=accounts_unhealthy, -2=bounce_protect,
    -99=account_suspended.
    """
    items, has_more = await get_client().paginate(
        "/campaigns", method="GET",
        params={"status": status}, limit=limit,
    )
    return fmt.summarize_campaigns(items, has_more)


@mcp.tool()
async def get_campaign(campaign_id: str) -> Any:
    """Get one campaign's full configuration (schedule, sequences, status)."""
    return await get_client()._request("GET", f"/campaigns/{campaign_id}")


# ===========================================================================
# Leads (read + write)
# ===========================================================================
@mcp.tool()
async def list_leads(
    campaign_id: Optional[str] = None,
    list_id: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 50,
) -> Any:
    """List leads, optionally filtered by campaign, list, or search text.

    # NOTE: v2 lists leads via POST /leads/list (not GET); the campaign filter
    # field is `campaign`, not `campaign_id`.
    """
    body = {"campaign": campaign_id, "list_id": list_id, "search": search}
    body = {k: v for k, v in body.items() if v is not None}
    items, has_more = await get_client().paginate(
        "/leads/list", method="POST", json=body, limit=limit,
    )
    return fmt.summarize_leads(items, has_more)


@mcp.tool()
async def get_lead(lead_id: str) -> Any:
    """Get one lead by id."""
    data = await get_client()._request("GET", f"/leads/{lead_id}")
    return fmt.summarize_lead(data) if isinstance(data, dict) else data


@mcp.tool()
async def search_leads_by_email(email: str) -> Any:
    """Find leads matching an email (across campaigns/lists) — who is this and
    which campaign(s) are they in."""
    items, has_more = await get_client().paginate(
        "/leads/list", method="POST", json={"search": email}, limit=50,
    )
    return fmt.summarize_leads(items, has_more)


@mcp.tool()
async def add_leads(
    leads: list[dict],
    campaign_id: Optional[str] = None,
    list_id: Optional[str] = None,
    skip_if_in_workspace: bool = True,
    confirm: bool = False,
) -> Any:
    """Add leads in bulk to a campaign OR a list (provide exactly one target).

    `leads` is a list of objects: at least `email` (required for campaigns),
    plus optional first_name, last_name, company_name, job_title, phone, website,
    personalization, and custom_variables (a dict). Duplicate emails within the
    batch are deduped; `skip_if_in_workspace` avoids re-loading existing contacts
    (safe for scheduled re-runs). LOW_WRITE.
    """
    if bool(campaign_id) == bool(list_id):
        return "Provide exactly one of campaign_id or list_id (not both, not neither)."
    clean, warnings = normalize_leads(leads)
    if not clean:
        return {"error": "no valid leads after normalization", "warnings": warnings}

    target = f"campaign {campaign_id}" if campaign_id else f"list {list_id}"
    preview = (f"Would ADD {len(clean)} lead(s) to {target} "
               f"(skip_if_in_workspace={skip_if_in_workspace}).")
    p = get_policy()
    decision = p.evaluate(
        "add_leads", LOW_WRITE, confirm, preview_text=preview,
        target_campaigns=[campaign_id] if campaign_id else None,
        volume=len(clean), metric="leads_added",
    )
    if not decision.execute:
        return {"preview": decision.preview, "leads_valid": len(clean),
                "warnings": warnings}

    body: dict[str, Any] = {"leads": clean, "skip_if_in_workspace": skip_if_in_workspace}
    if campaign_id:
        body["campaign_id"] = campaign_id
    else:
        body["list_id"] = list_id
    result = await get_client()._request("POST", "/leads/add", json=body)
    uploaded = result.get("leads_uploaded", len(clean)) if isinstance(result, dict) else len(clean)
    p.record("add_leads", {"target": target, "count": len(clean)},
             f"uploaded={uploaded}", decision.autonomous,
             metric="leads_added", count=uploaded)
    out = {"executed": True, "result": result}
    if warnings:
        out["warnings"] = warnings
    return out


@mcp.tool()
async def update_lead(lead_id: str, fields: dict, confirm: bool = False) -> Any:
    """Update editable fields on a lead (first_name, last_name, company_name,
    job_title, phone, website, personalization, custom_variables, ...). LOW_WRITE."""
    if not fields:
        return "Provide at least one field to update."
    preview = f"Would UPDATE lead {lead_id}: fields {list(fields)}."
    p = get_policy()
    decision = p.evaluate("update_lead", LOW_WRITE, confirm, preview_text=preview)
    if not decision.execute:
        return decision.preview
    result = await get_client()._request("PATCH", f"/leads/{lead_id}", json=fields)
    p.record("update_lead", {"lead_id": lead_id, "fields": list(fields)},
             "updated", decision.autonomous)
    return fmt.summarize_lead(result) if isinstance(result, dict) else result


@mcp.tool()
async def set_lead_interest_status(
    lead_email: str,
    status: str,
    campaign_id: Optional[str] = None,
    confirm: bool = False,
) -> Any:
    """Set a lead's interest status.

    `status` is a label — one of: interested, meeting_booked, meeting_completed,
    won, out_of_office, not_interested, wrong_person, lost, do_not_contact — or a
    raw int (-4..4). LOW_WRITE.

    # NOTE: v2 keys this endpoint (POST /leads/update-interest-status) by
    # `lead_email`, not lead id, so this tool takes the email.
    """
    try:
        value = resolve_interest_status(status)
    except ValueError as exc:
        return str(exc)
    preview = f"Would SET interest status of {lead_email} to '{status}' ({value})."
    p = get_policy()
    decision = p.evaluate(
        "set_lead_interest_status", LOW_WRITE, confirm, preview_text=preview,
        target_campaigns=[campaign_id] if campaign_id else None,
    )
    if not decision.execute:
        return decision.preview
    body = {"lead_email": lead_email, "interest_value": value}
    if campaign_id:
        body["campaign_id"] = campaign_id
    result = await get_client()._request("POST", "/leads/update-interest-status", json=body)
    p.record("set_lead_interest_status",
             {"lead_email": lead_email, "value": value}, "set", decision.autonomous)
    return result if result is not None else {"ok": True}


@mcp.tool()
async def move_lead(
    lead_id: str,
    to_campaign_id: Optional[str] = None,
    to_list_id: Optional[str] = None,
    from_campaign_id: Optional[str] = None,
    from_list_id: Optional[str] = None,
    copy: bool = False,
    confirm: bool = False,
) -> Any:
    """Move (or copy) one lead to another campaign or list. HIGH_WRITE.

    # NOTE: v2 POST /leads/move is asynchronous and returns a BackgroundJob;
    # it needs the SOURCE (from_campaign_id or from_list_id) as well as the
    # destination. Poll the returned job id if you need completion status.
    """
    if bool(to_campaign_id) == bool(to_list_id):
        return "Provide exactly one destination: to_campaign_id or to_list_id."
    if not (from_campaign_id or from_list_id):
        return "Provide the source: from_campaign_id or from_list_id."
    dest = to_campaign_id or to_list_id
    verb = "COPY" if copy else "MOVE"
    preview = f"Would {verb} lead {lead_id} to {dest}."
    p = get_policy()
    decision = p.evaluate(
        "move_lead", HIGH_WRITE, confirm, preview_text=preview,
        target_campaigns=[c for c in (to_campaign_id, from_campaign_id) if c],
    )
    if not decision.execute:
        return decision.preview
    body: dict[str, Any] = {"ids": [lead_id], "copy_leads": copy}
    if from_campaign_id:
        body["campaign"] = from_campaign_id
    if from_list_id:
        body["list_id"] = from_list_id
    if to_campaign_id:
        body["to_campaign_id"] = to_campaign_id
    if to_list_id:
        body["to_list_id"] = to_list_id
    result = await get_client()._request("POST", "/leads/move", json=body)
    p.record("move_lead", {"lead_id": lead_id, "dest": dest}, "job created",
             decision.autonomous)
    return {"executed": True, "background_job": result}


@mcp.tool()
async def delete_lead(lead_id: str, confirm: bool = False) -> Any:
    """DESTRUCTIVE: permanently delete a lead. HIGH_WRITE + hard-blocked —
    always requires confirm=true, even in autonomous mode."""
    preview = f"Would DESTRUCTIVELY DELETE lead {lead_id}."
    p = get_policy()
    decision = p.evaluate("delete_lead", HIGH_WRITE, confirm, preview_text=preview)
    if not decision.execute:
        return decision.preview
    result = await get_client()._request("DELETE", f"/leads/{lead_id}")
    p.record("delete_lead", {"lead_id": lead_id}, "deleted", decision.autonomous)
    return {"deleted": True, "lead": result}


# ===========================================================================
# Lead lists (read + write)
# ===========================================================================
@mcp.tool()
async def list_lead_lists(limit: int = 50) -> Any:
    """List lead lists (id, name)."""
    items, has_more = await get_client().paginate("/lead-lists", method="GET", limit=limit)
    simplified = [{"id": x.get("id"), "name": x.get("name")} for x in items]
    out: dict[str, Any] = {"count": len(simplified), "lists": simplified}
    note = fmt.truncation_note(len(simplified), has_more)
    if note:
        out["note"] = note
    return out


@mcp.tool()
async def create_lead_list(name: str, confirm: bool = False) -> Any:
    """Create a new lead list. LOW_WRITE."""
    preview = f"Would CREATE lead list '{name}'."
    p = get_policy()
    decision = p.evaluate("create_lead_list", LOW_WRITE, confirm, preview_text=preview)
    if not decision.execute:
        return decision.preview
    result = await get_client()._request("POST", "/lead-lists", json={"name": name})
    p.record("create_lead_list", {"name": name}, "created", decision.autonomous)
    return result


# ===========================================================================
# Campaign control (higher-stakes writes)
# ===========================================================================
@mcp.tool()
async def preview_campaign_build(
    name: str,
    subject: str,
    body: str,
    timezone: str = "America/New_York",
    schedule: Optional[dict] = None,
    sequences: Optional[list[dict]] = None,
    sender_accounts: Optional[list[str]] = None,
    tags: Optional[list[str]] = None,
    lead_list_id: Optional[str] = None,
) -> Any:
    """Preview campaign, variants, schedule, senders, tags, and lead-list mapping.

    Makes ZERO HTTP calls."""
    return campaign_build_preview(
        name, subject, body, timezone=timezone, schedule=schedule,
        sequences=sequences, sender_accounts=sender_accounts, tags=tags,
        lead_list_id=lead_list_id,
    )


@mcp.tool()
async def create_campaign(
    name: str,
    subject: str,
    body: str,
    timezone: str = "America/New_York",
    schedule: Optional[dict] = None,
    sequences: Optional[list[dict]] = None,
    confirm: bool = False,
) -> Any:
    """Create a campaign. HIGH_WRITE. Created PAUSED — launch separately.

    Convenience: pass `name`, `subject`, `body` and a sensible Mon-Fri business-
    hours schedule + single-step sequence are generated for you. For full control,
    pass `schedule` (a campaign_schedule object with a `schedules` array) and/or
    `sequences` (list of {steps:[{type,delay,variants:[{subject,body}]}]}).
    """
    campaign_schedule = schedule or default_schedule(timezone=timezone)
    seqs = sequences or simple_sequence(subject, body)
    preview = f"Would CREATE campaign '{name}' (starts paused)."
    p = get_policy()
    decision = p.evaluate("create_campaign", HIGH_WRITE, confirm, preview_text=preview)
    if not decision.execute:
        return decision.preview
    payload = {"name": name, "campaign_schedule": campaign_schedule, "sequences": seqs}
    result = await get_client()._request("POST", "/campaigns", json=payload)
    p.record("create_campaign", {"name": name}, "created", decision.autonomous)
    return fmt.summarize_campaign(result) if isinstance(result, dict) else result


@mcp.tool()
async def update_campaign(campaign_id: str, fields: dict, confirm: bool = False) -> Any:
    """Patch campaign settings (name, campaign_schedule, sequences, ...). HIGH_WRITE."""
    if not fields:
        return "Provide at least one field to update."
    preview = f"Would UPDATE campaign {campaign_id}: fields {list(fields)}."
    p = get_policy()
    decision = p.evaluate("update_campaign", HIGH_WRITE, confirm, preview_text=preview,
                          target_campaigns=[campaign_id])
    if not decision.execute:
        return decision.preview
    result = await get_client()._request("PATCH", f"/campaigns/{campaign_id}", json=fields)
    p.record("update_campaign", {"campaign_id": campaign_id, "fields": list(fields)},
             "updated", decision.autonomous)
    return fmt.summarize_campaign(result) if isinstance(result, dict) else result


@mcp.tool()
async def launch_campaign(campaign_id: str, confirm: bool = False) -> Any:
    """Activate/resume a campaign so it starts sending. HIGH_WRITE.

    # NOTE: v2 path is POST /campaigns/{id}/activate.
    """
    preview = f"Would LAUNCH (activate) campaign {campaign_id} — it will start sending."
    p = get_policy()
    decision = p.evaluate("launch_campaign", HIGH_WRITE, confirm, preview_text=preview,
                          target_campaigns=[campaign_id])
    if not decision.execute:
        return decision.preview
    result = await get_client()._request("POST", f"/campaigns/{campaign_id}/activate")
    p.record("launch_campaign", {"campaign_id": campaign_id}, "activated",
             decision.autonomous)
    return fmt.summarize_campaign(result) if isinstance(result, dict) else {"ok": True}


@mcp.tool()
async def pause_campaign(campaign_id: str, confirm: bool = False) -> Any:
    """Pause/stop a campaign. HIGH_WRITE."""
    preview = f"Would PAUSE campaign {campaign_id}."
    p = get_policy()
    decision = p.evaluate("pause_campaign", HIGH_WRITE, confirm, preview_text=preview,
                          target_campaigns=[campaign_id])
    if not decision.execute:
        return decision.preview
    result = await get_client()._request("POST", f"/campaigns/{campaign_id}/pause")
    p.record("pause_campaign", {"campaign_id": campaign_id}, "paused",
             decision.autonomous)
    return fmt.summarize_campaign(result) if isinstance(result, dict) else {"ok": True}


# ===========================================================================
# Emails / Unibox (read + write)
# ===========================================================================
@mcp.tool()
async def list_emails(
    campaign_id: Optional[str] = None,
    lead_email: Optional[str] = None,
    eaccount: Optional[str] = None,
    unread_only: bool = False,
    limit: int = 50,
) -> Any:
    """List Unibox emails (sends, replies, manual). Filter by campaign, lead
    email, sending account, or unread. # NOTE: this endpoint is rate-limited to
    20 req/min by Instantly."""
    params = {
        "campaign_id": campaign_id,
        "lead": lead_email,
        "eaccount": eaccount,
        "is_unread": True if unread_only else None,
    }
    items, has_more = await get_client().paginate(
        "/emails", method="GET", params=params, limit=limit,
    )
    return fmt.summarize_emails(items, has_more)


@mcp.tool()
async def get_email(email_id: str) -> Any:
    """Get one email's full subject/body/thread."""
    data = await get_client()._request("GET", f"/emails/{email_id}")
    return fmt.summarize_email(data, full=True) if isinstance(data, dict) else data


@mcp.tool()
async def count_unread() -> Any:
    """Count unread Unibox messages."""
    data = await get_client()._request("GET", "/emails/unread/count")
    return data if isinstance(data, dict) else {"count": data}


async def _fetch_email_raw(email_id: str) -> dict:
    data = await get_client()._request("GET", f"/emails/{email_id}")
    return data if isinstance(data, dict) else {}


@mcp.tool()
async def reply_to_email(
    email_id: str,
    body: str,
    eaccount: Optional[str] = None,
    subject: Optional[str] = None,
    html: bool = False,
    confirm: bool = False,
) -> Any:
    """Reply to an inbound Unibox email. HIGH_WRITE (sends mail from your account).

    If `eaccount` (the sending account) or `subject` are omitted, they're derived
    from the original email ("Re: <subject>") on execute. Set `html=true` to send
    `body` as HTML instead of plain text.

    # NOTE: v2 POST /emails/reply requires reply_to_uuid, eaccount, subject, and
    # a body object ({html|text}).
    """
    preview = f"Would REPLY to email {email_id} (a message will be sent)."
    p = get_policy()
    decision = p.evaluate("reply_to_email", HIGH_WRITE, confirm, preview_text=preview,
                          volume=1, metric="emails_sent")
    if not decision.execute:
        return decision.preview

    if not eaccount or not subject:
        original = await _fetch_email_raw(email_id)
        eaccount = eaccount or original.get("eaccount") or original.get("from_address_email")
        orig_subject = original.get("subject") or ""
        subject = subject or (orig_subject if orig_subject.lower().startswith("re:")
                              else f"Re: {orig_subject}".strip())
    payload = {
        "reply_to_uuid": email_id,
        "eaccount": eaccount,
        "subject": subject,
        "body": {"html": body} if html else {"text": body},
    }
    result = await get_client()._request("POST", "/emails/reply", json=payload)
    p.record("reply_to_email", {"email_id": email_id, "eaccount": eaccount},
             "sent", decision.autonomous, metric="emails_sent", count=1)
    return {"sent": True, "result": result}


@mcp.tool()
async def forward_email(
    email_id: str,
    to: str,
    eaccount: Optional[str] = None,
    subject: Optional[str] = None,
    body: Optional[str] = None,
    confirm: bool = False,
) -> Any:
    """Forward a Unibox email to `to` (comma-separated addresses). HIGH_WRITE.

    If `body` is omitted the original message is included. `eaccount`/`subject`
    default from the original on execute.

    # NOTE: v2 POST /emails/forward requires reply_to_uuid, to_address_email_list,
    # eaccount, subject, and either a body or include_original_body=true.
    """
    preview = f"Would FORWARD email {email_id} to {to}."
    p = get_policy()
    decision = p.evaluate("forward_email", HIGH_WRITE, confirm, preview_text=preview,
                          volume=1, metric="emails_sent")
    if not decision.execute:
        return decision.preview

    if not eaccount or not subject:
        original = await _fetch_email_raw(email_id)
        eaccount = eaccount or original.get("eaccount") or original.get("from_address_email")
        orig_subject = original.get("subject") or ""
        subject = subject or f"Fwd: {orig_subject}".strip()
    payload: dict[str, Any] = {
        "reply_to_uuid": email_id,
        "to_address_email_list": to,
        "eaccount": eaccount,
        "subject": subject,
    }
    if body:
        payload["body"] = {"text": body}
    else:
        payload["include_original_body"] = True
    result = await get_client()._request("POST", "/emails/forward", json=payload)
    p.record("forward_email", {"email_id": email_id, "to": to}, "sent",
             decision.autonomous, metric="emails_sent", count=1)
    return {"sent": True, "result": result}


@mcp.tool()
async def mark_thread_read(thread_id: str, confirm: bool = False) -> Any:
    """Mark all emails in a thread as read. LOW_WRITE (low risk)."""
    preview = f"Would MARK thread {thread_id} as read."
    p = get_policy()
    decision = p.evaluate("mark_thread_read", LOW_WRITE, confirm, preview_text=preview)
    if not decision.execute:
        return decision.preview
    result = await get_client()._request(
        "POST", f"/emails/threads/{thread_id}/mark-as-read")
    p.record("mark_thread_read", {"thread_id": thread_id}, "marked read",
             decision.autonomous)
    return result if result is not None else {"ok": True}


# ===========================================================================
# Sender accounts / mailboxes (read + write)
# ===========================================================================
@mcp.tool()
async def list_accounts(search: Optional[str] = None, limit: int = 50) -> Any:
    """List sender email accounts with status, warmup status, and daily limit."""
    items, has_more = await get_client().paginate(
        "/accounts", method="GET", params={"search": search}, limit=limit,
    )
    return fmt.summarize_accounts(items, has_more)


@mcp.tool()
async def get_account(email: str) -> Any:
    """Get status/settings for one sending account (by email)."""
    data = await get_client()._request("GET", f"/accounts/{email}")
    return fmt.summarize_account(data) if isinstance(data, dict) else data


@mcp.tool()
async def pause_account(email: str, confirm: bool = False) -> Any:
    """Pause a sending account (stops it sending). HIGH_WRITE + hard-blocked —
    always requires confirm=true. Deliverability control."""
    preview = f"Would PAUSE sending account {email}."
    p = get_policy()
    decision = p.evaluate("pause_account", HIGH_WRITE, confirm, preview_text=preview)
    if not decision.execute:
        return decision.preview
    result = await get_client()._request("POST", f"/accounts/{email}/pause")
    p.record("pause_account", {"email": email}, "paused", decision.autonomous)
    return fmt.summarize_account(result) if isinstance(result, dict) else {"ok": True}


@mcp.tool()
async def resume_account(email: str, confirm: bool = False) -> Any:
    """Resume a paused sending account. HIGH_WRITE."""
    preview = f"Would RESUME sending account {email}."
    p = get_policy()
    decision = p.evaluate("resume_account", HIGH_WRITE, confirm, preview_text=preview)
    if not decision.execute:
        return decision.preview
    result = await get_client()._request("POST", f"/accounts/{email}/resume")
    p.record("resume_account", {"email": email}, "resumed", decision.autonomous)
    return fmt.summarize_account(result) if isinstance(result, dict) else {"ok": True}


@mcp.tool()
async def update_account(email: str, fields: dict, confirm: bool = False) -> Any:
    """Update account settings, e.g. {"daily_limit": 40}, sending_gap, signature,
    or a nested "warmup" object. HIGH_WRITE (affects deliverability)."""
    if not fields:
        return "Provide at least one field to update (e.g. daily_limit)."
    preview = f"Would UPDATE account {email}: fields {list(fields)}."
    p = get_policy()
    decision = p.evaluate("update_account", HIGH_WRITE, confirm, preview_text=preview)
    if not decision.execute:
        return decision.preview
    result = await get_client()._request("PATCH", f"/accounts/{email}", json=fields)
    p.record("update_account", {"email": email, "fields": list(fields)},
             "updated", decision.autonomous)
    return fmt.summarize_account(result) if isinstance(result, dict) else result


# ===========================================================================
# Blocklist (read + write)
# ===========================================================================
@mcp.tool()
async def list_blocklist(limit: int = 50) -> Any:
    """List blocked contacts/domains.

    # NOTE: v2 path is /block-lists-entries.
    """
    items, has_more = await get_client().paginate(
        "/block-lists-entries", method="GET", limit=limit)
    return fmt.summarize_blocklist(items, has_more)


@mcp.tool()
async def add_to_blocklist(entries: list[str], confirm: bool = False) -> Any:
    """Add emails/domains to the blocklist (plain strings). LOW_WRITE.

    # NOTE: v2 POST /block-lists-entries/bulk-create with body {"bl_values": [...]}.
    """
    values = [e.strip() for e in entries if e and e.strip()]
    if not values:
        return "Provide at least one email or domain."
    preview = f"Would ADD {len(values)} entr(y/ies) to the blocklist."
    p = get_policy()
    decision = p.evaluate("add_to_blocklist", LOW_WRITE, confirm, preview_text=preview)
    if not decision.execute:
        return decision.preview
    result = await get_client()._request(
        "POST", "/block-lists-entries/bulk-create", json={"bl_values": values})
    p.record("add_to_blocklist", {"count": len(values)}, "added", decision.autonomous)
    return result


@mcp.tool()
async def remove_from_blocklist(entries: list[str], confirm: bool = False) -> Any:
    """Remove emails/domains from the blocklist. HIGH_WRITE + hard-blocked —
    always requires confirm=true.

    # NOTE: v2 POST /block-lists-entries/bulk-delete with {"bl_values": [...]}
    # (bulk-delete body shape not fully documented in the public reference —
    # verify against your workspace on first real call).
    """
    values = [e.strip() for e in entries if e and e.strip()]
    if not values:
        return "Provide at least one email or domain."
    preview = f"Would REMOVE {len(values)} entr(y/ies) from the blocklist."
    p = get_policy()
    decision = p.evaluate("remove_from_blocklist", HIGH_WRITE, confirm,
                          preview_text=preview)
    if not decision.execute:
        return decision.preview
    result = await get_client()._request(
        "POST", "/block-lists-entries/bulk-delete", json={"bl_values": values})
    p.record("remove_from_blocklist", {"count": len(values)}, "removed",
             decision.autonomous)
    return result


# ===========================================================================
# Deliverability / verification
# ===========================================================================
@mcp.tool()
async def verify_email(email: str) -> Any:
    """Verify a single email address (valid / invalid / catch-all).

    # NOTE: POST /email-verification consumes a verification credit. It returns
    # "pending" if it takes >10s — call again to poll the stored result.
    """
    return await get_client()._request(
        "POST", "/email-verification", json={"email": email})


# ===========================================================================
# Workspace (read)
# ===========================================================================
@mcp.tool()
async def get_workspace() -> Any:
    """Current workspace info and plan ids (useful to see what the key can reach)."""
    return await get_client()._request("GET", "/workspaces/current")


# ===========================================================================
# Webhooks (read + write) — for the hosted/reactive path
# ===========================================================================
@mcp.tool()
async def list_webhooks() -> Any:
    """List configured webhooks."""
    items, has_more = await get_client().paginate("/webhooks", method="GET", limit=100)
    return {"count": len(items), "webhooks": items,
            "note": fmt.truncation_note(len(items), has_more)}


@mcp.tool()
async def list_webhook_event_types() -> Any:
    """List the webhook event types this workspace supports (e.g. reply_received,
    lead_meeting_booked, email_sent/opened/clicked/replied/bounced/unsubscribed)."""
    return await get_client()._request("GET", "/webhooks/event-types")


@mcp.tool()
async def create_webhook(
    url: str,
    event_types: list[str],
    name: Optional[str] = None,
    campaign_id: Optional[str] = None,
    confirm: bool = False,
) -> Any:
    """Create webhook(s) pointing at `url` for the given event types. HIGH_WRITE.

    Receiving webhooks needs a publicly reachable URL (the hosted deployment, not
    local stdio) and an Instantly plan tier that includes webhooks.

    # NOTE: v2 POST /webhooks takes a SINGULAR `event_type` per webhook, so this
    # tool creates one webhook per requested event type and returns them all.
    """
    types = [t.strip() for t in event_types if t and t.strip()]
    if not types:
        return "Provide at least one event type (see list_webhook_event_types)."
    preview = f"Would CREATE {len(types)} webhook(s) -> {url} for {types}."
    p = get_policy()
    decision = p.evaluate("create_webhook", HIGH_WRITE, confirm, preview_text=preview,
                          target_campaigns=[campaign_id] if campaign_id else None)
    if not decision.execute:
        return decision.preview
    created = []
    for et in types:
        payload: dict[str, Any] = {"target_hook_url": url, "event_type": et}
        if name:
            payload["name"] = name
        if campaign_id:
            payload["campaign"] = campaign_id
        created.append(await get_client()._request("POST", "/webhooks", json=payload))
    p.record("create_webhook", {"url": url, "event_types": types},
             f"created {len(created)}", decision.autonomous)
    return {"created": len(created), "webhooks": created}


@mcp.tool()
async def delete_webhook(webhook_id: str, confirm: bool = False) -> Any:
    """Delete a webhook. HIGH_WRITE + hard-blocked — always requires confirm=true."""
    preview = f"Would DELETE webhook {webhook_id}."
    p = get_policy()
    decision = p.evaluate("delete_webhook", HIGH_WRITE, confirm, preview_text=preview)
    if not decision.execute:
        return decision.preview
    result = await get_client()._request("DELETE", f"/webhooks/{webhook_id}")
    p.record("delete_webhook", {"webhook_id": webhook_id}, "deleted",
             decision.autonomous)
    return {"deleted": True, "result": result}


# ===========================================================================
# Entry point
# ===========================================================================
def main() -> None:
    """Console entry point. Fails fast if INSTANTLY_API_KEY is unset. Selects the
    transport from $TRANSPORT (default stdio) so switching to hosted HTTP/SSE is a
    config change, not a rewrite."""
    if not os.environ.get("INSTANTLY_API_KEY"):
        print(
            "ERROR: INSTANTLY_API_KEY is not set.\n"
            "Get a v2 key in Instantly -> Settings -> Integrations -> API, then:\n"
            "  export INSTANTLY_API_KEY=your_key_here",
            file=sys.stderr,
        )
        sys.exit(1)

    transport = os.environ.get("TRANSPORT", "stdio").strip().lower()
    if transport == "stdio":
        mcp.run()
    else:
        # FastMCP reads HOST/PORT from its own settings/env for HTTP transports.
        mcp.run(transport=transport)


if __name__ == "__main__":
    main()
