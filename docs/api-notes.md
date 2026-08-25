# Implementation notes

Endpoint paths and payload shapes were verified against the live v2 reference at
<https://developer.instantly.ai/>. Where the real API differed from what a naive
reading of the docs would suggest, the difference is handled in code, marked with
a `# NOTE:` comment at the call site, and listed below.

## Caveats

- The API key stays in env / headers only — never committed. `.env` and `audit.log`
  are gitignored; only `.env.example` is committed.
- Autonomy caps and the hard-block list are enforced in code, so a prompt can't
  talk the server past them.
- If Instantly changes their v2 API, patch `client.py` and the affected tool.
- Webhooks and some features require higher Instantly plan tiers — the server
  degrades gracefully with a clear error if the key's plan/scope can't reach an
  endpoint.

## Where the live docs differed from a naive spec (handled in code)

- **`add_leads`** → `POST /leads/add` (campaign_id/list_id at top level; rich
  response with `leads_uploaded`, `skipped_count`, etc.). `skip_if_in_workspace` exists.
- **`list_leads`** → `POST /leads/list` (not GET); campaign filter field is
  `campaign`, envelope is `{items, next_starting_after}`.
- **Campaign analytics** → `GET /campaigns/analytics` returns an **array**; fields
  are `emails_sent_count`, `open_count_unique`, `reply_count_unique`,
  `link_click_count_unique`, `bounced_count`, `unsubscribed_count`,
  `total_opportunities` (mapped to "opportunities"; there's no literal
  "interested" count).
- **launch/pause** → `POST /campaigns/{id}/activate` and `/pause`.
- **`set_lead_interest_status`** → `POST /leads/update-interest-status`, keyed by
  **`lead_email`** (not lead id) with `interest_value`.
- **`move_lead`** → `POST /leads/move` is **async** (returns a BackgroundJob) and
  needs the source campaign/list as well as the destination.
- **Blocklist** → paths are `/block-lists-entries` with `{"bl_values": [...]}`.
- **Emails** → reply/forward require `eaccount` + `subject` + a `body` object; the
  tools auto-derive these from the original email when omitted. `mark_thread_read`
  is `POST /emails/threads/{thread_id}/mark-as-read`. `count_unread` →
  `/emails/unread/count`.
- **Accounts** → keyed by **email** in the path; analytics is
  `GET /accounts/analytics/daily`.
- **Webhooks** → `POST /webhooks` takes a **singular** `event_type`, so
  `create_webhook` creates one webhook per requested type.
- **Workspace** → `GET /workspaces/current`.
- **Inbox-placement test create/get** were omitted — the public reference didn't
  document a clean create/get pair; the inbox-placement *analytics* endpoints exist
  and can be added later if needed.
```

---

Next: [Tool reference](tools.md) · [Configuration](configuration.md)
