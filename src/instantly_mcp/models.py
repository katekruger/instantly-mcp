"""Pydantic input models + normalizers for the Instantly MCP tools.

Tool signatures accept plain ``dict`` / ``list[dict]`` (so FastMCP generates
simple JSON schemas), and these helpers validate and normalize that input before
it reaches the API.
"""

from __future__ import annotations

from typing import Any, Optional, Union

from pydantic import BaseModel, ConfigDict, field_validator

Scalar = Union[str, int, float, bool, None]


class LeadInput(BaseModel):
    """One lead in an ``add_leads`` / ``create_lead`` call.

    Mirrors the ``/leads/add`` body. ``email`` is optional at the schema level
    (lists allow name-only leads) but required for campaign uploads — that rule
    is enforced by the tool, not here.
    """

    model_config = ConfigDict(extra="forbid")

    email: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    company_name: Optional[str] = None
    job_title: Optional[str] = None
    phone: Optional[str] = None
    website: Optional[str] = None
    personalization: Optional[str] = None
    custom_variables: Optional[dict[str, Scalar]] = None

    @field_validator("email")
    @classmethod
    def _lower_email(cls, v: Optional[str]) -> Optional[str]:
        return v.strip().lower() if isinstance(v, str) and v.strip() else None


def normalize_leads(leads: list[dict]) -> tuple[list[dict], list[str]]:
    """Validate + dedupe a batch of leads by email (idempotency for re-runs).

    Returns ``(clean_leads, warnings)``. Leads without an email are kept as-is
    (valid for list uploads); duplicate emails within the batch are dropped.
    """
    clean: list[dict] = []
    seen_emails: set[str] = set()
    warnings: list[str] = []
    for i, raw in enumerate(leads):
        try:
            lead = LeadInput.model_validate(raw)
        except Exception as exc:  # noqa: BLE001 - surface as a warning, keep going
            warnings.append(f"lead[{i}] skipped: {exc}")
            continue
        if lead.email:
            if lead.email in seen_emails:
                warnings.append(f"lead[{i}] duplicate email '{lead.email}' dropped")
                continue
            seen_emails.add(lead.email)
        clean.append(lead.model_dump(exclude_none=True))
    return clean, warnings


def default_schedule(
    name: str = "Business Hours",
    timezone: str = "America/New_York",
    start: str = "09:00",
    end: str = "17:00",
) -> dict:
    """A ready-to-send ``campaign_schedule`` (Mon-Fri, business hours)."""
    return {
        "schedules": [
            {
                "name": name,
                "timing": {"from": start, "to": end},
                "days": {"0": False, "1": True, "2": True, "3": True,
                         "4": True, "5": True, "6": False},
                "timezone": timezone,
            }
        ]
    }


def simple_sequence(subject: str, body: str) -> list[dict]:
    """A single-step, single-variant email sequence."""
    return [
        {
            "steps": [
                {
                    "type": "email",
                    "delay": 0,
                    "variants": [{"subject": subject, "body": body}],
                }
            ]
        }
    ]


def campaign_build_preview(
    name: str,
    subject: str,
    body: str,
    *,
    timezone: str = "America/New_York",
    schedule: Optional[dict] = None,
    sequences: Optional[list[dict]] = None,
    sender_accounts: Optional[list[str]] = None,
    tags: Optional[list[str]] = None,
    lead_list_id: Optional[str] = None,
) -> dict[str, Any]:
    """Build a zero-I/O campaign plan before any Instantly write."""
    seqs = sequences or simple_sequence(subject, body)
    variant_count = sum(
        len(step.get("variants", []))
        for sequence in seqs
        for step in sequence.get("steps", [])
    )
    warnings = []
    if not sender_accounts:
        warnings.append("No sender allocation supplied.")
    if not lead_list_id:
        warnings.append("No lead list mapped.")
    return {
        "campaign": {
            "name": name,
            "campaign_schedule": schedule or default_schedule(timezone=timezone),
            "sequences": seqs,
            "sender_accounts": sender_accounts or [],
            "tags": tags or [],
            "lead_list_id": lead_list_id,
        },
        "summary": {"sequence_count": len(seqs), "variant_count": variant_count},
        "warnings": warnings,
        "creation_status": "paused",
        "write_performed": False,
        "execution_note": (
            "create_campaign writes only verified campaign fields. Sender, tag, and "
            "lead-list mappings remain explicit follow-ups until their endpoint "
            "contracts are configured."
        ),
    }


# Interest status enum (lt_interest_status), per the Instantly lead schema.
INTEREST_STATUS = {
    "out_of_office": 0,
    "interested": 1,
    "meeting_booked": 2,
    "meeting_completed": 3,
    "won": 4,
    "not_interested": -1,
    "wrong_person": -2,
    "lost": -3,
    "do_not_contact": -4,
}


def resolve_interest_status(status: Union[str, int]) -> int:
    """Map a friendly label (or raw int) to the numeric interest value."""
    if isinstance(status, int):
        if -4 <= status <= 4:
            return status
        raise ValueError(f"interest status {status} out of range (-4..4)")
    key = str(status).strip().lower().replace(" ", "_")
    if key not in INTEREST_STATUS:
        allowed = ", ".join(INTEREST_STATUS)
        raise ValueError(f"unknown interest status '{status}'. Allowed: {allowed}")
    return INTEREST_STATUS[key]
