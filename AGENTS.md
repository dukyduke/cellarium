# AGENTS.md — working on Cellarium

Instructions for AI coding agents (and humans) changing this repository. Read this before editing.
A step-by-step procedure lives in `skills/cellarium-maintenance/SKILL.md`.

## What this is

Self-hosted wine cellar manager. One Python process serves the web UI, a REST API (`/api`) and an MCP
server (`/mcp`). Storage is a single SQLite file plus an images folder (`CELLARIUM_DATA_DIR`).

## Layout

| Path | Role |
|---|---|
| `cellarium/db.py` | SQLite connection + **append-only** migrations (`MIGRATIONS` list, `PRAGMA user_version`). |
| `cellarium/cellar.py` | **All business rules**: wines, vintages, lots (purchases), bottles, storages, movements, phases, summary, plan, history, provenance (`field_meta`). |
| `cellarium/importers.py` | CSV/XLSX import (Vitiplace + generic headers), CSV export. |
| `cellarium/auth.py` | Accounts (`owner` / `reader`), sessions, API tokens (`read` / `write` scope), password hashing (scrypt). |
| `cellarium/api.py` | FastAPI routers: `public`, `account` (any logged-in user), `owners` (account management), `router` (cellar; writes need `can_write`). Thin. |
| `cellarium/mcp_server.py` | MCP tools/prompt/resource (official `mcp` SDK v2, `MCPServer`). Thin; enforces AI write rules. |
| `cellarium/guides/wine-enrichment.md` | Rules (SKILL.md format) served to AI clients for completing wine sheets; mirrored in `skills/cellarium-wine-enrichment/SKILL.md` (a test keeps both identical). |
| `cellarium/app.py` | App factory; `McpGateway` routes `/mcp` (bearer token) vs FastAPI. |
| `cellarium/static/` | Front end: `index.html`, `style.css`, `js/core.js` (api, i18n `t()`, `h()` DOM builder, dialogs), `js/main.js` (router), `js/pages/*.js`, `i18n/{fr,en}.json`. No build step, no framework. |
| `tests/` | pytest: domain, import, API (roles, CSRF, tokens), MCP (in-process + real HTTP), static i18n/import checks. |
| `deploy/` | systemd unit, nginx example. `Dockerfile`, `docker-compose.yml` at the root. |

## Domain model (cheat sheet)

- `wines` (the sheet) → `vintages` (year NULL = non-vintage; drinking window `drink_from ≤ peak_from ≤ peak_to ≤ drink_before`)
  → `lots` (a purchase: date, volume_cl, unit_price, quantity, comment) → `bottles` (one row per physical bottle,
  `status` in_cellar/drunk/gifted/lost, location = `storage_id` + `row`/`col` for racks, `storage_id` only for boxes, NULL = unplaced).
- `movements`: history (`in`, `out`, `move`, `restore`) with `actor` (from `conn.actor`).
- `field_meta`: provenance of descriptive fields (`user` / `import` / `ai` + sources). `protect_user=True` prevents
  AI from overwriting `user`/`import` values.
- Phases (`cellar.phase_for`): keep → drink → peak → decline, `unknown` without dates; `overdue` after `drink_before`.

## Rules

1. Business logic goes in `cellar.py` (or `importers.py` / `auth.py`), never in routes, MCP tools or JS.
2. Schema change = **new** entry appended to `db.MIGRATIONS`. Never edit a released migration.
3. Every user-visible string goes through `t('key')`; add the key to **both** `i18n/fr.json` and `i18n/en.json`
   (French is the default language). `tests/test_static.py` fails otherwise.
4. Authorisation: reads need a login; writes need `principal.can_write` (owner + write scope). Cookie-session
   writes must send `X-Cellarium: 1` (CSRF guard; `core.js` `api()` does it).
5. MCP writes to descriptive data must keep the provenance rules: `origin='ai'`, non-empty `sources`,
   `protect_user=True` unless the owner explicitly confirmed. Update `guides/wine-enrichment.md` when tools change.
6. Front end: build DOM with `h()` (never `innerHTML` with data), keep it working at 390 px wide, no new
   dependencies or build tools.
7. Never commit `data/`, `.env`, tokens or passwords.

## Checks before committing

```bash
.venv/bin/python -m pytest -q
for f in cellarium/static/js/*.js cellarium/static/js/pages/*.js; do node --check "$f"; done
```

Then run `./launch.sh` and check the changed screens in a browser, desktop **and** mobile width (390 px).
