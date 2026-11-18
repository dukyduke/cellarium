---
name: cellarium-wine-enrichment
description: Complete Cellarium wine sheets from cited sources only.
version: 0.1.0
author: Guillaume Chevillot (dukyduke), Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [cellarium, wine, mcp, enrichment, sources]
    related_skills: []
---

# Cellarium — wine sheet enrichment guide (for AI agents)

You are connected to a personal wine cellar through the Cellarium MCP server.
This guide governs every write you make to wine sheets and drinking windows.
Read it fully before calling `update_wine_info`, `set_drinking_window` or `create_wine`.

## Golden rules

1. **Never invent.** Write a value only when it is confirmed by a source you actually
   consulted in this session (producer website, appellation body, recognised guide,
   reputable merchant sheet, Wikipedia for appellation-level facts). No source, no write.
2. **Cite every write.** Pass the URLs (or precise references) you used in `sources`.
   The server rejects AI writes without sources. One source per claim is the minimum;
   prefer the producer's own page for cuvée-specific facts.
3. **Leave blank when unsure.** An empty field is better than a plausible guess.
   If sources disagree, write nothing for that field and report the disagreement.
4. **Never overwrite the owner's data.** Fields entered by the user or imported from their
   old tool are protected: the server skips them and reports `protected`. Do not try to
   work around this. Only pass `confirmed_by_user=true` when the owner explicitly asked you,
   in this conversation, to change that exact value.
5. **Match the exact wine.** Same producer, same cuvée, same colour and — for vintage-specific
   facts — same vintage. Facts about another cuvée, another colour or the appellation in
   general must not be presented as facts about this wine.
6. **Be transparent.** After writing, tell the owner what you filled, from which sources,
   and what you left empty and why.

## What you may complete

| Field | Level | Typical source |
|---|---|---|
| `region`, `country` | appellation | appellation body, Wikipedia |
| `producer` | cuvée | producer site, label |
| `grapes` | cuvée (or appellation rules if the cuvée sheet is silent — say so) | producer technical sheet |
| `description` | cuvée | producer / guide tasting notes, summarised in your own words, 2–4 sentences, no marketing superlatives |
| `serving_temp` | cuvée or style | producer sheet; otherwise leave empty |
| `food_pairing` | cuvée | producer sheet / guide |
| drinking window (`drink_from`, `peak_from`, `peak_to`, `drink_before`) | **vintage** | producer, guide or merchant with an explicit window for this vintage |

### Drinking window ("quand boire ce vin")

- Use `set_drinking_window(wine_id, year, ...)` with years (integers).
- Only use a window that a source states for this wine (ideally this vintage).
  A generic appellation rule of thumb ("Pomerol keeps 10–20 years") is **not** enough on
  its own. If it is the only thing you have, do not write it: propose it to the owner in
  the conversation and write it only if they confirm (`confirmed_by_user=true`).
- Keep `drink_from <= peak_from <= peak_to <= drink_before`. You may fill only some of them.
- Non-vintage wines: `year` = 0.

## What you must not touch

- Stock, purchases, prices, bottle locations: only on explicit request from the owner.
- `name`, `appellation`, `type`: identity fields. Suggest corrections, do not apply them
  unless the owner asks.
- `notes`: the owner's personal notes, never written by an AI.

## Recommended workflow

1. `list_wines_to_enrich` (or `get_wine` for a given wine) to see what is missing.
2. Research the exact wine. Collect sources.
3. `update_wine_info(wine_id, fields={...}, sources=[...])` with only confirmed fields.
4. `set_drinking_window(...)` per vintage when a source gives a window.
5. Read the response: `updated`, `skipped` (protected or unknown fields). Report to the owner.

Values written through MCP are marked "AI" with their sources in the web interface,
so the owner can review them.

Write descriptions in the owner's language (French by default unless told otherwise).
