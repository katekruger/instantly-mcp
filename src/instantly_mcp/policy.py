"""Autonomy policy + guardrails, enforced in code (not just prompt text).

Layered on top of the manual ``confirm`` gate:

* Risk tiers (READ / LOW_WRITE / HIGH_WRITE) assigned per tool.
* ``AUTONOMY_LEVEL`` (manual | assisted | autonomous) chooses what runs without
  a human confirming each call.
* Volume caps (per-call and rolling-24h) that force a preview when exceeded.
* An always-on hard-block list that can NEVER run without ``confirm`` regardless
  of level.
* Optional campaign allow/deny lists.
* An append-only ``audit.log`` of every executed write.

See the README for the tool-to-tier table.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

# --- Risk tiers -------------------------------------------------------------
READ = "READ"
LOW_WRITE = "LOW_WRITE"
HIGH_WRITE = "HIGH_WRITE"

# Tools that can NEVER execute without confirm=true, at any autonomy level.
HARD_BLOCK = frozenset({
    "delete_lead",
    "delete_leads_bulk",
    "delete_webhook",
    "pause_account",
    "remove_from_blocklist",
})


@dataclass
class Decision:
    execute: bool
    autonomous: bool
    preview: Optional[str] = None
    reason: Optional[str] = None


@dataclass
class PolicyConfig:
    level: str = "manual"
    max_leads_per_call: int = 1000
    max_leads_per_day: int = 5000
    max_emails_per_day: int = 50
    max_campaigns_per_call: int = 1
    allowlist: frozenset[str] = field(default_factory=frozenset)
    denylist: frozenset[str] = field(default_factory=frozenset)
    audit_log_path: str = "audit.log"

    @classmethod
    def from_env(cls) -> "PolicyConfig":
        def _int(name: str, default: int) -> int:
            try:
                return int(os.environ.get(name, default))
            except ValueError:
                return default

        def _set(name: str) -> frozenset[str]:
            raw = os.environ.get(name, "")
            return frozenset(x.strip() for x in raw.split(",") if x.strip())

        level = os.environ.get("AUTONOMY_LEVEL", "manual").strip().lower()
        if level not in ("manual", "assisted", "autonomous"):
            level = "manual"
        return cls(
            level=level,
            max_leads_per_call=_int("INSTANTLY_MAX_LEADS_PER_CALL", 1000),
            max_leads_per_day=_int("INSTANTLY_MAX_LEADS_PER_DAY", 5000),
            max_emails_per_day=_int("INSTANTLY_MAX_EMAILS_PER_DAY", 50),
            max_campaigns_per_call=_int("INSTANTLY_MAX_CAMPAIGNS_PER_CALL", 1),
            allowlist=_set("INSTANTLY_CAMPAIGN_ALLOWLIST"),
            denylist=_set("INSTANTLY_CAMPAIGN_DENYLIST"),
            audit_log_path=os.environ.get("INSTANTLY_AUDIT_LOG", "audit.log"),
        )


class Policy:
    """Decides whether a write executes, and records the ones that do."""

    def __init__(self, config: Optional[PolicyConfig] = None):
        self.config = config or PolicyConfig.from_env()

    # -- rolling 24h usage from the audit log --------------------------------
    def _usage_last_24h(self, metric: str) -> int:
        path = self.config.audit_log_path
        if not metric or not os.path.exists(path):
            return 0
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        total = 0
        try:
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    if rec.get("metric") != metric:
                        continue
                    try:
                        ts = datetime.fromisoformat(rec.get("ts", ""))
                    except ValueError:
                        continue
                    if ts >= cutoff:
                        total += int(rec.get("count", 0) or 0)
        except OSError:
            return 0
        return total

    def _cap_check(self, tier: str, volume: Optional[int], metric: Optional[str],
                   n_campaigns: int) -> Optional[str]:
        """Return a human reason if a volume cap is exceeded, else None."""
        cfg = self.config
        if n_campaigns > cfg.max_campaigns_per_call:
            return (f"affects {n_campaigns} campaigns > cap "
                    f"{cfg.max_campaigns_per_call} per call")
        if metric == "leads_added" and volume is not None:
            if volume > cfg.max_leads_per_call:
                return f"{volume} leads > cap {cfg.max_leads_per_call} per call"
            if self._usage_last_24h("leads_added") + volume > cfg.max_leads_per_day:
                return (f"would exceed {cfg.max_leads_per_day} leads/24h "
                        f"(already {self._usage_last_24h('leads_added')})")
        if metric == "emails_sent":
            used = self._usage_last_24h("emails_sent")
            if used + (volume or 1) > cfg.max_emails_per_day:
                return f"would exceed {cfg.max_emails_per_day} emails/24h (already {used})"
        return None

    def _scope_check(self, target_campaigns: Optional[list[str]]) -> Optional[str]:
        cfg = self.config
        if not target_campaigns:
            return None
        targets = [c for c in target_campaigns if c]
        if cfg.denylist and any(c in cfg.denylist for c in targets):
            return "target campaign is on the denylist"
        if cfg.allowlist and any(c not in cfg.allowlist for c in targets):
            return "target campaign is not on the autonomous allowlist"
        return None

    def evaluate(
        self,
        tool: str,
        tier: str,
        confirm: bool,
        *,
        preview_text: str,
        target_campaigns: Optional[list[str]] = None,
        volume: Optional[int] = None,
        metric: Optional[str] = None,
    ) -> Decision:
        """Decide whether ``tool`` may run now.

        ``confirm=True`` always executes (the manual escape hatch). Otherwise the
        autonomy level + tier decide, subject to hard-block, caps, and scope.
        """
        if tier == READ:
            return Decision(execute=True, autonomous=False)

        n_campaigns = len([c for c in (target_campaigns or []) if c])
        hard_blocked = tool in HARD_BLOCK
        cap_reason = self._cap_check(tier, volume, metric, n_campaigns)
        scope_reason = self._scope_check(target_campaigns)

        # Would the level+tier permit autonomous execution, ignoring guards?
        level = self.config.level
        tier_allows = (
            (level == "autonomous" and not hard_blocked)
            or (level == "assisted" and tier == LOW_WRITE)
        )
        autonomous_ok = tier_allows and not cap_reason and not scope_reason

        if confirm:
            return Decision(execute=True, autonomous=False, reason="confirmed")
        if autonomous_ok:
            return Decision(execute=True, autonomous=True, reason="autonomous")

        # Build the preview reason.
        if hard_blocked:
            why = "This action is hard-blocked and always requires confirm=true."
        elif cap_reason:
            why = f"Blocked by volume cap: {cap_reason}."
        elif scope_reason:
            why = f"Blocked by scope: {scope_reason}."
        elif level == "manual":
            why = "AUTONOMY_LEVEL=manual — every write needs confirm=true."
        elif tier == HIGH_WRITE:
            why = f"HIGH_WRITE action under AUTONOMY_LEVEL={level} needs confirm=true."
        else:
            why = "Needs confirm=true."
        preview = f"{preview_text} {why} Re-call with confirm=true to execute."
        return Decision(execute=False, autonomous=False, preview=preview, reason=why)

    # -- audit trail ---------------------------------------------------------
    def record(self, tool: str, args: dict, result: str, autonomous: bool,
               *, metric: Optional[str] = None, count: int = 0) -> None:
        """Append one executed write to the audit log (secrets already excluded)."""
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "tool": tool,
            "mode": "autonomous" if autonomous else "confirmed",
            "args": _redact(args),
            "result": result[:500],
        }
        if metric:
            rec["metric"] = metric
            rec["count"] = count
        try:
            with open(self.config.audit_log_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, default=str) + "\n")
        except OSError:
            pass  # never let audit I/O break a tool call


_SECRET_HINTS = ("key", "token", "secret", "password", "authorization")


def _redact(args: dict) -> dict:
    out = {}
    for k, v in (args or {}).items():
        if any(h in k.lower() for h in _SECRET_HINTS):
            out[k] = "***"
        elif isinstance(v, list) and len(v) > 10:
            out[k] = f"[{len(v)} items]"
        else:
            out[k] = v
    return out
