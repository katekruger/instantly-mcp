# Safety and autonomy

The server assumes an LLM will call it unsupervised at some point, so the
guardrails are enforced in code rather than asked for in a prompt. Two layers:
a per-call `confirm` gate, and an autonomy policy that decides which calls may
skip it.

## Write-action safety (the `confirm` gate)

Every create/update/launch/pause/move/delete tool takes `confirm: bool = False`.

- With `confirm=false` (default) and no autonomous permission, the tool **does not
  call the API**. It returns a plain-language **preview** of exactly what it would
  do — e.g. `"Would PAUSE campaign camp-1. … Re-call with confirm=true to execute."`
  Previews are **network-free**, so they're instant and safe.
- With `confirm=true`, it executes and returns the result.
- Destructive tools say **DESTRUCTIVE** in the preview.

## Autonomy policy

The `confirm` gate is the manual model. On top of it, an **autonomy policy** lets
routine work run without a human confirming each call, while hard-blocking risky
actions. Configure it with env vars (see [Configuration](configuration.md)).

**Risk tiers** (assigned per tool):
- **READ** — always allowed, no confirm, no cap.
- **LOW_WRITE** — reversible, low blast radius (`add_leads`, `set_lead_interest_status`, `update_lead`, `mark_thread_read`, `add_to_blocklist`, `create_lead_list`).
- **HIGH_WRITE** — high blast radius or irreversible (`launch_campaign`, `pause_campaign`, `create_campaign`, `update_campaign`, `move_lead`, `delete_lead`, `reply_to_email`, `forward_email`, `pause_account`, `resume_account`, `update_account`, `remove_from_blocklist`, `create_webhook`, `delete_webhook`).

**`AUTONOMY_LEVEL`** (operator chooses):

| Level | LOW_WRITE | HIGH_WRITE |
|---|---|---|
| `manual` (default) | needs `confirm=true` | needs `confirm=true` |
| `assisted` | runs autonomously within caps | needs `confirm=true` |
| `autonomous` | runs within caps | runs within caps, **except the hard-block list** |

**Hard-block list** (never runs without `confirm=true`, at any level):
`delete_lead`, `delete_webhook`, `pause_account`, `remove_from_blocklist`, and bulk deletes.

**Volume caps** (env-configurable, enforced in code — exceeding one forces a
`confirm` preview even in `autonomous` mode):
`INSTANTLY_MAX_LEADS_PER_CALL` (1000), `INSTANTLY_MAX_LEADS_PER_DAY` (5000),
`INSTANTLY_MAX_EMAILS_PER_DAY` (50), `INSTANTLY_MAX_CAMPAIGNS_PER_CALL` (1).
Rolling-24h usage is computed from the audit log.

**Allow/deny lists:** `INSTANTLY_CAMPAIGN_ALLOWLIST` / `INSTANTLY_CAMPAIGN_DENYLIST`
(comma-separated campaign UUIDs) restrict which campaigns autonomous actions may touch.

**Audit log:** every executed write is appended to `audit.log` (timestamp, tool,
args minus secrets, result, and whether it ran autonomously or was confirmed).
This is your paper trail for anything the agent did unattended. It may contain
lead emails, so it's gitignored.

**Idempotency:** `add_leads` dedupes by email within a call and honors
`skip_if_in_workspace`, so re-running a scheduled job doesn't double-load leads.

---

## Running independently

Two different meanings of "independent":

1. **Acting without you confirming each step** — solved by the autonomy policy.
   Set `AUTONOMY_LEVEL=assisted` (or `autonomous`) with caps and an allowlist, and
   the agent does routine work (pull analytics, add enriched leads, triage the
   Unibox, blocklist bad addresses) on its own while irreversible actions stay
   gated and everything is logged.

2. **Running when you're not driving / your machine is off** — local stdio has a
   hard ceiling here (no public URL, so it can't receive webhooks; it only exists
   while your machine + client run). Two ways to close the gap:
   - **Scheduled polling (do this first, works with local):** use your client's
     scheduled tasks to run a prompt on a cadence — e.g. every morning: *"pull
     yesterday's Instantly analytics, list new Unibox replies, and flag anything
     that needs me."*
   - **Hosted + webhooks (true always-on):** deploy this same codebase to a small
     always-on box and point Instantly webhooks at it (see [Hosting](hosting.md)).

---

Next: [Hosting](hosting.md) · [Configuration](configuration.md) · [Tool reference](tools.md)
