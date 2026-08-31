"""Shape raw Instantly API responses into compact, LLM-friendly summaries.

Every list tool caps output and appends a truncation note; analytics tools
compute rates (open/reply/click/bounce) from the raw counts.
"""

from __future__ import annotations

from typing import Any, Optional

CAMPAIGN_STATUS = {
    0: "draft", 1: "active", 2: "paused", 3: "completed",
    4: "running_subsequences", -1: "accounts_unhealthy",
    -2: "bounce_protect", -99: "account_suspended",
}
ACCOUNT_STATUS = {1: "active", 2: "paused", 3: "maintenance",
                  -1: "error", -2: "error", -3: "error"}
WARMUP_STATUS = {0: "paused", 1: "active", -1: "banned", -2: "issue", -3: "issue"}


def _rate(numerator: Any, denominator: Any) -> Optional[str]:
    try:
        n, d = float(numerator or 0), float(denominator or 0)
    except (TypeError, ValueError):
        return None
    if d <= 0:
        return None
    return f"{round(100 * n / d, 1)}%"


def truncation_note(shown: int, has_more: bool, hint: str = "add a filter") -> Optional[str]:
    if has_more:
        return f"showing first {shown}; more available — raise `limit` or {hint}."
    return None


def campaign_status_label(status: Any) -> str:
    try:
        return CAMPAIGN_STATUS.get(int(status), str(status))
    except (TypeError, ValueError):
        return str(status)


def summarize_campaign(c: dict) -> dict:
    return {
        "id": c.get("id"),
        "name": c.get("name"),
        "status": campaign_status_label(c.get("status")),
        "status_code": c.get("status"),
        "timestamp_created": c.get("timestamp_created"),
    }


def summarize_campaigns(items: list[dict], has_more: bool) -> dict:
    out: dict[str, Any] = {
        "count": len(items),
        "campaigns": [summarize_campaign(c) for c in items],
    }
    note = truncation_note(len(items), has_more)
    if note:
        out["note"] = note
    return out


def summarize_campaign_analytics(row: dict) -> dict:
    """One analytics row -> counts + computed rates."""
    sent = row.get("emails_sent_count", 0)
    return {
        "campaign_id": row.get("campaign_id") or row.get("id"),
        "campaign_name": row.get("campaign_name") or row.get("name"),
        "sent": sent,
        "opens_unique": row.get("open_count_unique"),
        "replies_unique": row.get("reply_count_unique"),
        "clicks_unique": row.get("link_click_count_unique"),
        "bounced": row.get("bounced_count"),
        "unsubscribed": row.get("unsubscribed_count"),
        "opportunities": row.get("total_opportunities"),
        "rates": {
            "open_rate": _rate(row.get("open_count_unique"), sent),
            "reply_rate": _rate(row.get("reply_count_unique"), sent),
            "click_rate": _rate(row.get("link_click_count_unique"), sent),
            "bounce_rate": _rate(row.get("bounced_count"), sent),
        },
    }


def summarize_analytics_list(rows: Any) -> Any:
    if isinstance(rows, list):
        return [summarize_campaign_analytics(r) for r in rows]
    if isinstance(rows, dict):
        return summarize_campaign_analytics(rows)
    return rows


def summarize_step_analytics(rows: Any) -> Any:
    """Per-step rows -> counts + rates keyed by step/variant."""
    def one(r: dict) -> dict:
        sent = r.get("sent", 0)
        return {
            "step": r.get("step"),
            "variant": r.get("variant"),
            "sent": sent,
            "opened_unique": r.get("unique_opened"),
            "replies_unique": r.get("unique_replies"),
            "clicks_unique": r.get("unique_clicks"),
            "rates": {
                "open_rate": _rate(r.get("unique_opened"), sent),
                "reply_rate": _rate(r.get("unique_replies"), sent),
                "click_rate": _rate(r.get("unique_clicks"), sent),
            },
        }
    return [one(r) for r in rows] if isinstance(rows, list) else rows


def summarize_account_analytics(rows: Any) -> Any:
    """Daily account analytics -> aggregate totals across the returned rows."""
    if not isinstance(rows, list):
        return rows
    agg = {"sent": 0, "opened": 0, "unique_opened": 0, "replies": 0,
           "unique_replies": 0, "clicks": 0, "unique_clicks": 0, "bounced": 0}
    for r in rows:
        for k in agg:
            agg[k] += r.get(k, 0) or 0
    return {
        "days": len(rows),
        "totals": agg,
        "rates": {
            "open_rate": _rate(agg["unique_opened"], agg["sent"]),
            "reply_rate": _rate(agg["unique_replies"], agg["sent"]),
            "bounce_rate": _rate(agg["bounced"], agg["sent"]),
        },
        "daily": rows,
    }


def summarize_lead(lead: dict) -> dict:
    return {
        "id": lead.get("id"),
        "email": lead.get("email"),
        "first_name": lead.get("first_name"),
        "last_name": lead.get("last_name"),
        "company_name": lead.get("company_name"),
        "campaign": lead.get("campaign"),
        "list_id": lead.get("list_id"),
        "interest_status": lead.get("lt_interest_status"),
        "status": lead.get("status"),
    }


def summarize_leads(items: list[dict], has_more: bool) -> dict:
    out: dict[str, Any] = {
        "count": len(items),
        "leads": [summarize_lead(x) for x in items],
    }
    note = truncation_note(len(items), has_more)
    if note:
        out["note"] = note
    return out


def summarize_account(a: dict[str, Any]) -> dict:
    status = a.get("status")
    warmup_status = a.get("warmup_status")
    return {
        "email": a.get("email"),
        # ACCOUNT_STATUS/WARMUP_STATUS are keyed by int; a status the API
        # sends as something else (or omits) passes through unmapped rather
        # than being coerced or dropped.
        "status": ACCOUNT_STATUS.get(status, status) if isinstance(status, int) else status,
        "status_code": status,
        "warmup_status": (
            WARMUP_STATUS.get(warmup_status, warmup_status)
            if isinstance(warmup_status, int) else warmup_status
        ),
        "daily_limit": a.get("daily_limit"),
        "provider_code": a.get("provider_code"),
    }


def summarize_accounts(items: list[dict], has_more: bool) -> dict:
    out: dict[str, Any] = {
        "count": len(items),
        "accounts": [summarize_account(a) for a in items],
    }
    note = truncation_note(len(items), has_more, hint="add a search")
    if note:
        out["note"] = note
    return out


def summarize_email(e: dict, *, full: bool = False) -> dict:
    body = e.get("body") or {}
    text = body.get("text") if isinstance(body, dict) else None
    out = {
        "id": e.get("id"),
        "thread_id": e.get("thread_id"),
        "subject": e.get("subject"),
        "from": e.get("from_address_email") or e.get("eaccount"),
        "to": e.get("to_address_email_list"),
        "is_unread": e.get("is_unread"),
        "campaign_id": e.get("campaign_id"),
        "timestamp": e.get("timestamp_created") or e.get("timestamp_email"),
    }
    if full:
        out["body_text"] = text
        out["body_html"] = body.get("html") if isinstance(body, dict) else None
    elif text:
        out["preview"] = text[:200]
    return out


def summarize_emails(items: list[dict], has_more: bool) -> dict:
    out: dict[str, Any] = {
        "count": len(items),
        "emails": [summarize_email(x) for x in items],
    }
    note = truncation_note(len(items), has_more)
    if note:
        out["note"] = note
    return out


def summarize_blocklist(items: list[dict], has_more: bool) -> dict:
    entries = [
        {"id": x.get("id"), "value": x.get("bl_value") or x.get("value")}
        for x in items
    ]
    out: dict[str, Any] = {"count": len(entries), "entries": entries}
    note = truncation_note(len(entries), has_more)
    if note:
        out["note"] = note
    return out
