# Cellarium

**Free (MIT)**, **self-hostable**, **mobile-friendly** wine cellar manager with **AI integration through MCP**.
Inspired by Vitiplace's cellar manager: plain, fast, effective.

*Version française : [README.md](README.md).*

## Features

- **Overview**: bottles per type (red, white, rosé, sparkling, sweet, cider — custom types allowed), purchase
  value, this year's drinking schedule, wines to drink first, storage fill.
- **My cellar**, four views:
  - sortable/filterable **list**: Appellation, Wine, Vintage, Type, Size, Price, Stock (cards on phones);
  - **drinking schedule**: keep, ready, at peak, declining (+ past due, not set);
  - graphical **plan** of your racks (rows × columns, Roman-numeral columns), bottles coloured by type,
    search highlighting, tap-to-move or drag-and-drop, swapping, **boxes** (loose storage) and unplaced bottles;
  - **history** of movements (in, out: drunk/given/lost, moves) with author and "put back".
- **Wine sheet**: name, appellation, region, country, producer, type, grapes, serving temperature,
  description, food pairing, personal notes, image (photo or optional **AI generation**), drinking window per
  vintage, purchases (date, vintage, size, quantity, price, comment), bottles and their locations.
- Configurable **storage**: racks (rows, columns) and boxes.
- **Import** Vitiplace's CSV export (or any CSV; French/English headers, delimiter and encoding detected) with
  preview and duplicate-safe re-import; CSV **export** and JSON backup/restore.
- **Accounts**: several **owners** (read/write) and **read-only** accounts to share the cellar. Login required.
- **MCP**: an AI assistant can browse the cellar, locate a bottle, log a bottle you drank… and **complete wine
  sheets** under a strict guide (no invention, sources required, user data protected).
- **i18n**: French (default) and English; add a language with one JSON file.

## Quick start

```bash
git clone https://github.com/dukyduke/cellarium.git && cd cellarium
./launch.sh      # creates .venv on first run, serves http://127.0.0.1:8770
```

On first start the console prints a **setup code**: open the UI, enter it and create the owner account
(or set `CELLARIUM_ADMIN_USER` / `CELLARIUM_ADMIN_PASSWORD`). Then describe your racks in
*Settings › Storage* and import your export in *Settings › Import / export*.

## Hosting

- Docker: `cp .env.example .env && docker compose up -d --build` (data in `./data`).
- systemd: see [`deploy/cellarium.service`](deploy/cellarium.service) and [`deploy/nginx.conf`](deploy/nginx.conf).
- Admin CLI: `python -m cellarium create-user|set-password|list-users|create-token|list-tokens`.

## AI integration (MCP)

Create a token in *Settings › API & MCP tokens*, then point your MCP client (Streamable HTTP) at
`https://your-host/mcp` with `Authorization: Bearer cel_…`. Read tokens cannot modify anything.
The enrichment guide ([`cellarium/guides/wine-enrichment.md`](cellarium/guides/wine-enrichment.md)) is served
by the MCP server and enforced server-side.

## For AI agents

See [`AGENTS.md`](AGENTS.md) and the skill [`skills/cellarium-maintenance/SKILL.md`](skills/cellarium-maintenance/SKILL.md).

## Development

`pip install -e '.[dev]' && pytest -q` — Python 3.11+, FastAPI, SQLite, build-free ES-module front end,
official MCP SDK. API docs at `/api/docs`.

## License

[MIT](LICENSE) © Guillaume Chevillot
