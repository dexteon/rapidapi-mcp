"""Quota guard for rapidapi-mcp.

Combines two signals:
- catalog `basic_plan.monthly_cap` + `dashboard_usage_pct` (a point-in-time
  snapshot from the last harvest — stale but a useful floor), and
- a local per-slug call counter persisted across restarts, reset each month.

estimate() = ceil(cap * snapshot_pct/100) + local_calls_this_month.
For hard_cap APIs, calls are blocked once estimate >= cap (override with force).
"""
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path

USAGE_FILE = Path(os.environ.get(
    "RAPIDAPI_USAGE_FILE", str(Path.home() / ".rapidapi_mcp_usage.json")))


def _month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _load() -> dict:
    try:
        return json.loads(USAGE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(d: dict) -> None:
    try:
        USAGE_FILE.write_text(json.dumps(d), encoding="utf-8")
    except Exception:
        pass


def local_count(slug: str) -> int:
    rec = _load().get(slug)
    if not rec or rec.get("month") != _month():
        return 0
    return int(rec.get("count", 0))


def record(slug: str) -> None:
    d = _load()
    m = _month()
    rec = d.get(slug)
    if not rec or rec.get("month") != m:
        rec = {"month": m, "count": 0}
    rec["count"] = int(rec.get("count", 0)) + 1
    d[slug] = rec
    _save(d)


def estimate(api: dict, slug: str) -> dict:
    """Return quota picture for one API.
    {cap, hard, snapshot_pct, snapshot_used, local_calls, est_used, remaining}
    cap/remaining are None when the catalog cap is unknown."""
    bp = api.get("basic_plan") or {}
    cap = bp.get("monthly_cap")
    hard = bool(bp.get("hard_cap"))
    pct = api.get("dashboard_usage_pct") or 0
    local = local_count(slug)
    if not isinstance(cap, (int, float)):
        return {"cap": None, "hard": hard, "snapshot_pct": pct,
                "snapshot_used": None, "local_calls": local,
                "est_used": None, "remaining": None}
    cap = int(cap)
    snapshot_used = math.ceil(cap * float(pct) / 100.0)
    est = snapshot_used + local
    return {"cap": cap, "hard": hard, "snapshot_pct": pct,
            "snapshot_used": snapshot_used, "local_calls": local,
            "est_used": est, "remaining": max(cap - est, 0)}


def check(api: dict, slug: str) -> tuple[bool, str]:
    """(allowed, message). Blocks only hard_cap APIs estimated at/over cap."""
    q = estimate(api, slug)
    if q["cap"] is None:
        return True, "cap unknown — proceeding (no guard)"
    if q["hard"] and q["est_used"] >= q["cap"]:
        return False, (f"hard cap reached: ~{q['est_used']}/{q['cap']} this month "
                       f"(snapshot {q['snapshot_pct']}% + {q['local_calls']} local). "
                       f"Pass force=true to override.")
    if q["remaining"] is not None and q["remaining"] <= max(1, q["cap"] // 10):
        return True, f"WARNING: ~{q['remaining']} of {q['cap']} left this month"
    return True, f"~{q['remaining']} of {q['cap']} left"
