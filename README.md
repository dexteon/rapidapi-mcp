# rapidapi-mcp

One MCP server exposing **all ~128 of the maintainer's RapidAPI subscriptions** through the
single shared `x-rapidapi-key`. Discovery + execution in 4 tools — no per-API
server needed.

## Tools
- `rapidapi_categories()` — categories + counts.
- `rapidapi_search(query, category, limit)` — find subscribed APIs by keyword/category.
- `rapidapi_describe(slug)` — host, endpoints (method/path/purpose/key_params), quota, notes.
- `rapidapi_call(slug, path, method, params, json_body, host, force)` — proxy any endpoint, quota-guarded.
- `rapidapi_quota(slug, near_cap_only)` — estimated monthly usage/remaining per API.

Typical flow: `search` → `describe` → `call`.

## Quota guard
Each `rapidapi_call` checks the catalog `monthly_cap` + `dashboard_usage_pct` snapshot
plus a local per-slug counter (persisted, resets monthly). Hard-capped APIs are
**blocked before the request** once estimated usage hits the cap — override with
`force=true`. The snapshot is point-in-time (last harvest), so estimates are a floor,
not live truth. Counter file: `RAPIDAPI_USAGE_FILE` (default `~/.rapidapi_mcp_usage.json`).

## Run
```
pip install -r requirements.txt
set RAPIDAPI_KEY=<your-fresh-key>
python server.py
```

## Env
- `RAPIDAPI_KEY` — **required**. ⚠️ The previously shared key was flagged **compromised** —
  rotate at https://rapidapi.com/developer/security and set the fresh value here.
- `RAPIDAPI_CATALOG` — optional path to `rapidapi_catalog.json`
  (default `%USERPROFILE%/Desktop/rapidapi_catalog.json`). The catalog is the single
  source of truth (slug/host/endpoints/quota); this server reads it, never copies the key.

## Register in hermes
Stdio server — register by command, not a URL:
```
hermes mcp add rapidapi --command python --args D:/DexProjects/rapidapi-mcp/server.py
```
or in `%LOCALAPPDATA%/hermes/config.yaml` under `mcp_servers:`:
```yaml
  rapidapi:
    type: stdio
    command: python
    args: [ D:/DexProjects/rapidapi-mcp/server.py ]
    env:
      RAPIDAPI_KEY: ${RAPIDAPI_KEY}
    enabled: true
```
