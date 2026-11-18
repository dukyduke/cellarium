---
name: cellarium-maintenance
description: Change the Cellarium wine cellar app safely.
version: 0.1.0
author: Guillaume Chevillot (dukyduke), Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [cellarium, fastapi, sqlite, mcp, vanilla-js, i18n]
    related_skills: []
---

# Cellarium maintenance

Procedure for adding features to or fixing the Cellarium repository ("vibe coding" by an AI agent) without
breaking the data model, permissions, translations or the mobile layout. Architecture and hard rules are in
`AGENTS.md`; read it first.

## When to Use

- The owner asks for a new feature, a UI change, a fix, a new language or a new MCP tool in Cellarium.
- Don't use for: completing wine sheets with data (that is an MCP client task governed by
  `cellarium/guides/wine-enrichment.md`), or server administration of a deployed instance.

## Prerequisites

- Python 3.11+, Node (only for `node --check`), a browser tool for visual checks.
- Dev install: `terminal(command="python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'")`.

## Procedure

1. **Locate the layer.** Business rule → `cellarium/cellar.py` (or `importers.py`, `auth.py`); HTTP shape →
   `cellarium/api.py`; AI tool → `cellarium/mcp_server.py`; screen → `cellarium/static/js/pages/<page>.js`.
   Done when you can name every file you will touch.
2. **Schema change?** Append a new SQL script to `MIGRATIONS` in `cellarium/db.py` (never edit an existing one),
   add the column to the relevant field tuples in `cellar.py` (`WINE_FIELDS`, `VINTAGE_FIELDS`, `LOT_FIELDS`),
   to `export_json` table list if it is a new table. Done when a fresh DB and an existing DB both migrate
   (tests create fresh DBs; check an existing one with `python -m cellarium list-users`).
3. **Write the test first** in `tests/` (domain in `test_cellar.py`, endpoints and roles in `test_api.py`, AI tools
   in `test_mcp.py`). Readers must get 403 on any new write endpoint. Done when the new test fails for the
   right reason.
4. **Implement** in the layer from step 1, keeping routes and tools thin. Writes: router `api.router` already
   requires `can_write` for non-GET methods; account-level actions go in `api.account`, user management in
   `api.owners`.
5. **UI strings**: every label via `t('section.key')`, added to `cellarium/static/i18n/fr.json` **and** `en.json`.
   Build DOM with `h()` from `core.js`; call the API with `api()` (adds the CSRF header).
6. **MCP**: new tool = function in `build_mcp` with `ctx: Context | None = None`, `with db(ctx, write=True)` for
   writes; descriptive data writes use `origin="ai"`, require `sources`, `protect_user=True`. Update the tool list in
   `tests/test_mcp.py::test_tools_listed`, in `README.md`, and the guide if behaviour changes.
7. **Verify**: run `terminal(command=".venv/bin/python -m pytest -q")` and
   `terminal(command="for f in cellarium/static/js/*.js cellarium/static/js/pages/*.js; do node --check $f; done")`;
   start `./launch.sh` (background) and check the changed screens at 1280 px and 390 px wide, including as a
   reader account. Done when tests are green and screenshots show no overflow or console error.
8. **Commit** with a descriptive message (French is fine), never including `data/`, `.env` or tokens.

## Pitfalls

- `h()` sets CSS custom properties through `style: {'--c': value}`; it uses `setProperty`, keep it that way.
- Route handlers in `main.js` receive `(match, query, el)`; forgetting to pass `el` renders nothing.
- `MCPServer.streamable_http_app()` creates a session manager that runs once: the app builds it in the lifespan.
- `Settings.from_env()` reads `.env` from the working directory; tests pass `env_file=None`.
- The enrichment guide exists twice: edit `cellarium/guides/wine-enrichment.md`, then copy it to
  `skills/cellarium-wine-enrichment/SKILL.md` (`tests/test_static.py` checks they match).
- Non-vintage wines are `year = NULL` in the DB, `0` in MCP tool arguments, `NM`/`NV` in the UI.
- Deleting a bottle (`DELETE /api/bottles/{id}`) is for data-entry errors only; consumption is `POST /out` and is logged.

## Verification

- `pytest -q` passes (domain, import, API roles/CSRF, MCP in-process and over HTTP, i18n completeness).
- The changed screen works in a browser on desktop and mobile, as owner and as reader.
- `git status` shows no `data/`, `.env` or secret files staged.
