# Tool reference

All 40 tools the server exposes, grouped by area. Reads are always allowed and
never need confirmation. Writes are gated — see
[Safety and autonomy](autonomy.md) for what the tiers mean.

| Tier | Meaning |
|---|---|
| **READ** | Always allowed. No confirmation, no cap. |
| **LOW_WRITE** | Reversible, small blast radius. Can run unattended at `assisted` and above. |
| **HIGH_WRITE** | Irreversible or large blast radius. Needs `confirm=true` unless `autonomous`. |
| **hard-blocked** | Never runs without `confirm=true`, at any autonomy level. |

Reads are always allowed. Writes are gated (see the autonomy tiers below).

## Analytics (read)
| Tool | What it does |
|---|---|
| `get_campaign_analytics` | Counts + rates for one campaign over a date range |
| `get_account_analytics` | Workspace daily sending analytics, aggregated |
| `get_campaign_steps_analytics` | Per-step / per-variant breakdown |
| `list_campaigns` | id, name, status, created (with status filter) |
| `get_campaign` | Full campaign config |

## Leads (read + write)
| Tool | Tier |
|---|---|
| `list_leads`, `get_lead`, `search_leads_by_email` | READ |
| `add_leads` | LOW_WRITE |
| `update_lead`, `set_lead_interest_status` | LOW_WRITE |
| `move_lead` | HIGH_WRITE |
| `delete_lead` | HIGH_WRITE · **hard-blocked** |

## Lead lists (read + write)
`list_lead_lists` (READ) · `create_lead_list` (LOW_WRITE)

## Campaign control (write)
`update_campaign`, `create_campaign`, `launch_campaign`, `pause_campaign` — all HIGH_WRITE.

`preview_campaign_build` is READ-only and performs zero HTTP calls. It renders the campaign,
variants, schedule, sender allocation, tags, and lead-list mapping before creation. Creation
still starts paused; launching remains a separate confirmed action.

## Emails / Unibox (read + write)
| Tool | Tier |
|---|---|
| `list_emails`, `get_email`, `count_unread` | READ |
| `mark_thread_read` | LOW_WRITE |
| `reply_to_email`, `forward_email` | HIGH_WRITE |

## Sender accounts / mailboxes (read + write)
| Tool | Tier |
|---|---|
| `list_accounts`, `get_account` | READ |
| `resume_account`, `update_account` | HIGH_WRITE |
| `pause_account` | HIGH_WRITE · **hard-blocked** |

## Blocklist (read + write)
| Tool | Tier |
|---|---|
| `list_blocklist` | READ |
| `add_to_blocklist` | LOW_WRITE |
| `remove_from_blocklist` | HIGH_WRITE · **hard-blocked** |

## Deliverability, workspace, webhooks
`verify_email` (spends a credit) · `get_workspace` (READ) ·
`list_webhooks`, `list_webhook_event_types` (READ) ·
`create_webhook` (HIGH_WRITE) · `delete_webhook` (HIGH_WRITE · **hard-blocked**).

---

---

Next: [Safety and autonomy](autonomy.md) · [Configuration](configuration.md) · [Hosting](hosting.md)
