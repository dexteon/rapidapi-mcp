"""RapidAPI MCP — one server exposing ALL of your RapidAPI subscriptions.

RapidAPI shares a single x-rapidapi-key across the whole app, so a single proxy
tool can reach any of the ~128 subscribed APIs. Discovery (search/describe) +
execution (call) cover the entire catalog with 4 tools.

Run: python server.py   (stdio MCP server)

Env:
  RAPIDAPI_KEY      — required. Your RapidAPI app key. (ROTATE the old shared key —
                      it was flagged compromised; set the fresh one here.)
  RAPIDAPI_CATALOG  — optional path to rapidapi_catalog.json
                      (default: %USERPROFILE%/Desktop/rapidapi_catalog.json)
"""
import json
import os
import re
from pathlib import Path
from typing import Any, Optional

import httpx
from mcp.server.fastmcp import FastMCP

import quota

mcp = FastMCP("rapidapi-mcp", instructions="one server exposing all of your RapidAPI subscriptions")

DEFAULT_CATALOG = Path(os.path.expanduser("~")) / "Desktop" / "rapidapi_catalog.json"
CATALOG_PATH = Path(os.environ.get("RAPIDAPI_CATALOG", str(DEFAULT_CATALOG)))


def _load() -> dict:
    with open(CATALOG_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("apis", data)


try:
    APIS: dict = _load()
except Exception as exc:  # surfaced via tools rather than crashing import
    APIS = {}
    _LOAD_ERROR = str(exc)
else:
    _LOAD_ERROR = None


def _key() -> str | None:
    return os.environ.get("RAPIDAPI_KEY") or None


# The x-rapidapi-key header must only ever go to a RapidAPI gateway host.
# A host is accepted only if it is a bare DNS name ending in .p.rapidapi.com
# (no scheme, port, path, userinfo, query or fragment characters).
_RAPIDAPI_HOST_RE = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+p\.rapidapi\.com$")


def is_allowed_host(host: Any) -> bool:
    """True only for a bare hostname under .p.rapidapi.com."""
    if not isinstance(host, str) or not host or len(host) > 253:
        return False
    return bool(_RAPIDAPI_HOST_RE.fullmatch(host.lower()))


def _summary(slug: str, a: dict) -> dict:
    bp = a.get("basic_plan") or {}
    return {
        "slug": slug,
        "name": a.get("name"),
        "category": a.get("category"),
        "host": a.get("host"),
        "search_for": a.get("search_for"),
        "monthly_cap": bp.get("monthly_cap"),
        "usage_pct": a.get("dashboard_usage_pct"),
    }


@mcp.tool()
def rapidapi_categories() -> dict:
    """List RapidAPI categories with how many subscribed APIs each holds."""
    if _LOAD_ERROR:
        return {"error": f"catalog not loaded: {_LOAD_ERROR}", "path": str(CATALOG_PATH)}
    counts: dict[str, int] = {}
    for a in APIS.values():
        counts[a.get("category", "other")] = counts.get(a.get("category", "other"), 0) + 1
    return {"total_apis": len(APIS), "categories": dict(sorted(counts.items()))}


@mcp.tool()
def rapidapi_search(query: str = "", category: str = "", limit: int = 25) -> list[dict]:
    """Find subscribed APIs by keyword and/or category.

    query matches name/slug/vendor/search_for/notes (case-insensitive).
    Returns lightweight summaries; use rapidapi_describe(slug) for endpoints.
    """
    if _LOAD_ERROR:
        return [{"error": f"catalog not loaded: {_LOAD_ERROR}", "path": str(CATALOG_PATH)}]
    q = query.lower().strip()
    cat = category.lower().strip()
    out = []
    for slug, a in APIS.items():
        if cat and a.get("category", "").lower() != cat:
            continue
        if q:
            blob = " ".join(str(a.get(k, "")) for k in
                            ("name", "slug", "vendor", "search_for", "notes", "returns")).lower()
            if q not in blob and q not in slug.lower():
                continue
        out.append(_summary(slug, a))
    return out[:limit]


@mcp.tool()
def rapidapi_describe(slug: str) -> dict:
    """Full detail for one subscribed API: host, endpoints (method/path/purpose/
    key_params), quota, notes. Use before rapidapi_call."""
    if _LOAD_ERROR:
        return {"error": f"catalog not loaded: {_LOAD_ERROR}", "path": str(CATALOG_PATH)}
    a = APIS.get(slug)
    if not a:
        hits = [s for s in APIS if slug.lower() in s.lower()]
        return {"error": f"unknown slug '{slug}'", "did_you_mean": hits[:8]}
    return {
        "slug": slug, "name": a.get("name"), "vendor": a.get("vendor"),
        "category": a.get("category"), "host": a.get("host"),
        "rapidapi_url": a.get("rapidapi_url"),
        "basic_plan": a.get("basic_plan"), "usage_pct": a.get("dashboard_usage_pct"),
        "search_for": a.get("search_for"), "returns": a.get("returns"),
        "notes": a.get("notes"), "endpoints": a.get("endpoints", []),
    }


@mcp.tool()
def rapidapi_call(slug: str = "", path: str = "/", method: str = "GET",
                  params: Optional[dict] = None, json_body: Optional[dict] = None,
                  host: str = "", max_chars: int = 12000, force: bool = False) -> dict:
    """Call any subscribed RapidAPI endpoint through the shared key.

    Provide slug (resolves host from catalog) OR an explicit host. path is the
    endpoint path (e.g. '/active-ats-7d'). params = query string; json_body =
    POST body. Response is truncated to max_chars.

    Quota-guarded: hard-capped APIs are blocked once estimated usage hits the
    monthly cap (override with force=true). Successful calls are counted locally.
    """
    key = _key()
    if not key:
        return {"error": "RAPIDAPI_KEY not set in env. Rotate the compromised key "
                         "at rapidapi.com/developer/security and set the fresh value."}
    resolved_host = host
    a = APIS.get(slug)
    if not resolved_host:
        if not a:
            return {"error": f"unknown slug '{slug}' and no host given"}
        resolved_host = a.get("host")
    if not resolved_host:
        return {"error": "could not resolve host"}
    if not is_allowed_host(resolved_host):
        return {"error": "host rejected: only *.p.rapidapi.com hosts may receive "
                         "the RapidAPI key", "host": resolved_host}
    if not path.startswith("/"):
        path = "/" + path

    # Quota guard (only when we know the slug → can look up its cap)
    quota_note = None
    if a:
        allowed, quota_note = quota.check(a, slug)
        if not allowed and not force:
            return {"error": "quota guard blocked call", "detail": quota_note,
                    "quota": quota.estimate(a, slug)}

    url = f"https://{resolved_host}{path}"
    if httpx.URL(url).host != resolved_host.lower():
        return {"error": "host rejected: built URL does not point at the RapidAPI host",
                "host": resolved_host}
    headers = {"x-rapidapi-host": resolved_host, "x-rapidapi-key": key}
    try:
        with httpx.Client(timeout=40, follow_redirects=False) as c:
            r = c.request(method.upper(), url, params=params or None,
                          json=json_body or None, headers=headers)
    except httpx.HTTPError as exc:
        return {"error": f"request failed: {exc}", "url": url}

    body: Any
    ctype = r.headers.get("content-type", "")
    if "json" in ctype:
        try:
            body = r.json()
        except Exception:
            body = r.text
    else:
        body = r.text
    # Count successful, authenticated calls against local quota (2xx/4xx hit the
    # plan; skip 401/403 auth failures which usually don't consume quota).
    if a and r.status_code not in (401, 403):
        quota.record(slug)
        quota_note = (quota.check(a, slug)[1])

    text = body if isinstance(body, str) else json.dumps(body)
    truncated = len(text) > max_chars
    return {
        "status": r.status_code, "host": resolved_host, "url": url,
        "truncated": truncated, "quota": quota_note,
        "body": (text[:max_chars] if isinstance(body, str) else body)
        if not truncated else text[:max_chars],
    }


@mcp.tool()
def rapidapi_quota(slug: str = "", near_cap_only: bool = False) -> Any:
    """Estimated monthly quota per API (catalog snapshot + local calls this month).

    One slug → that API's picture. No slug → all APIs with a known cap
    (set near_cap_only=true to show only those <=20% remaining)."""
    if _LOAD_ERROR:
        return {"error": f"catalog not loaded: {_LOAD_ERROR}"}
    if slug:
        a = APIS.get(slug)
        if not a:
            return {"error": f"unknown slug '{slug}'"}
        return {"slug": slug, **quota.estimate(a, slug)}
    out = []
    for s, a in APIS.items():
        q = quota.estimate(a, s)
        if q["cap"] is None:
            continue
        if near_cap_only and q["remaining"] > max(1, q["cap"] // 5):
            continue
        out.append({"slug": s, "name": a.get("name"), "hard": q["hard"],
                    "cap": q["cap"], "est_used": q["est_used"],
                    "remaining": q["remaining"]})
    out.sort(key=lambda x: (x["remaining"] if x["remaining"] is not None else 9e9))
    return out


if __name__ == "__main__":
    mcp.run()
