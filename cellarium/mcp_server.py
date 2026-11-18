"""MCP server (Streamable HTTP, mounted at /mcp by app.py). Tools are thin wrappers over cellar.py.

AI writes go through the provenance rules of cellar._apply_fields:
  * origin='ai' + mandatory sources for descriptive data,
  * protect_user=True so values entered by the owner are never overwritten
    (unless the tool caller sets confirmed_by_user=True).
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Literal

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import __version__, auth, cellar
from .config import GUIDES_DIR, Settings
from .db import connect

GUIDE_URI = "cellarium://guides/wine-enrichment"
AI_WINE_FIELDS = ("producer", "region", "country", "grapes", "description", "serving_temp", "food_pairing")
IDENTITY_FIELDS = ("name", "appellation", "type")

INSTRUCTIONS = (
    "Cellarium: personal wine cellar (wines, vintages, bottles, racks and boxes, drinking schedule). "
    "Before writing any wine information call get_enrichment_guide and follow it strictly: write only facts "
    "confirmed by sources you consulted (pass them in `sources`), leave fields empty when unsure, never "
    "overwrite the owner's data. Before creating a wine, call get_naming_conventions and name it like the "
    "existing wines (typically 'Nom - Cuvée'). Comments written by an AI are automatically marked '[IA date]'. "
    "Stock changes (add/remove/move bottles) only on the owner's explicit request."
)


def guide_text() -> str:
    return (GUIDES_DIR / "wine-enrichment.md").read_text(encoding="utf-8")


def build_mcp(settings: Settings, local_principal: auth.Principal | None = None) -> MCPServer:
    """local_principal is used when there is no HTTP request (in-process clients, tests)."""
    mcp = MCPServer(name="cellarium", title="Cellarium wine cellar", version=__version__,
                    instructions=INSTRUCTIONS)

    def principal_of(ctx: Context | None) -> auth.Principal:
        headers = None
        if ctx is not None:
            try:
                headers = ctx.headers
            except ValueError:
                headers = None
        if headers is None:
            if local_principal is None:
                raise ToolError("authentication required")
            return local_principal
        conn = connect(settings.db_path)
        try:
            principal = auth.token_principal(conn, auth.bearer(headers.get("authorization")))
        finally:
            conn.close()
        if principal is None:
            raise ToolError("authentication required")
        return principal

    @contextmanager
    def db(ctx: Context | None, write: bool = False):
        principal = principal_of(ctx)
        if write and not principal.can_write:
            raise ToolError("read-only access: this token cannot modify the cellar "
                            "(needs an owner account and a 'write' token)")
        conn = connect(settings.db_path, actor=f"{principal.username} via MCP ({principal.token_name or 'local'})")
        try:
            yield conn
        except cellar.CellarError as exc:
            detail = f" ({exc.details})" if exc.details else ""
            raise ToolError(exc.message + detail) from None
        finally:
            conn.close()

    def _year(year: int | None) -> int | None:
        return None if not year else year

    # ------------------------------------------------------------ read

    @mcp.tool(description="Cellar overview: bottles per type, drinking phases, value, storages, bottles to drink first.")
    def cellar_summary(ctx: Context) -> dict:
        with db(ctx) as conn:
            return cellar.summary(conn)

    @mcp.tool(description="Search wine sheets by free text (name, producer, appellation, region, grapes).")
    def search_wines(query: str = "", type: str | None = None, in_stock_only: bool = False, limit: int = 30, ctx: Context | None = None) -> list[dict]:
        with db(ctx) as conn:
            return cellar.search_wines(conn, query or None, type, in_stock_only, min(limit, 200))

    @mcp.tool(description="Full wine sheet: fields with provenance (meta), vintages with drinking windows, "
                          "purchases (lots), bottles with ids and locations, recent history.")
    def get_wine(wine_id: int, ctx: Context | None = None) -> dict:
        with db(ctx) as conn:
            return cellar.get_wine(conn, wine_id)

    @mcp.tool(description="Where are the bottles matching a query? Returns bottle ids with rack/box location.")
    def find_bottles(query: str, ctx: Context | None = None) -> list[dict]:
        with db(ctx) as conn:
            return cellar.find_bottles(conn, query)

    @mcp.tool(description="Drinking schedule for the current year grouped by phase: keep, drink, peak, decline, unknown.")
    def drinking_schedule(phase: Literal["keep", "drink", "peak", "decline", "unknown"] | None = None, ctx: Context | None = None) -> dict:
        with db(ctx) as conn:
            data = cellar.schedule(conn)
        if phase:
            data["phases"] = {phase: data["phases"][phase]}
        return data

    @mcp.tool(description="Racks (rows x cols) and boxes with bottle counts and capacity.")
    def list_storages(ctx: Context) -> list[dict]:
        with db(ctx) as conn:
            return cellar.list_storages(conn)

    @mcp.tool(description="Movements history (in = purchases/imports, out = drunk/gifted/lost, move, restore).")
    def get_history(kind: Literal["in", "out", "move", "restore"] | None = None, year: int | None = None,
                    limit: int = 50, ctx: Context | None = None) -> list[dict]:
        with db(ctx) as conn:
            return cellar.history(conn, kind=kind, year=year, limit=min(limit, 500))

    @mcp.tool(description="Wines in stock missing descriptive fields or a drinking window — candidates for enrichment.")
    def list_wines_to_enrich(limit: int = 20, ctx: Context | None = None) -> list[dict]:
        with db(ctx) as conn:
            return cellar.incomplete_wines(conn, min(limit, 100))

    @mcp.tool(description="How wines are named in this cellar: observed name shapes (e.g. 'Nom - Cuvée', "
                          "'Nom (désignation)') with counts and examples, closest examples first (same appellation / "
                          "producer). Call before create_wine and deduce the naming rule from the existing names.")
    def get_naming_conventions(appellation: str | None = None, producer: str | None = None, limit: int = 40,
                               ctx: Context | None = None) -> dict:
        with db(ctx) as conn:
            return cellar.naming_conventions(conn, appellation, producer, min(limit, 200))

    @mcp.tool(description="Rules for completing wine sheets with AI. Read before any write.")
    def get_enrichment_guide(ctx: Context) -> str:
        return guide_text()

    @mcp.resource(GUIDE_URI, name="wine-enrichment-guide", title="Wine sheet enrichment guide",
                  description="Rules for AI completion of wine sheets (no invention, sources required).",
                  mime_type="text/markdown")
    def enrichment_guide_resource() -> str:
        return guide_text()

    @mcp.prompt(name="enrich_wine", title="Complete a wine sheet",
                description="Research and complete one wine sheet following the Cellarium enrichment guide.")
    def enrich_wine_prompt(wine_id: str, ctx: Context | None = None) -> str:
        with db(ctx) as conn:
            wine = cellar.get_wine(conn, int(wine_id))
        summary = {k: wine.get(k) for k in ("id", "name", "producer", "appellation", "region", "country", "type",
                                            "grapes", "description", "serving_temp", "food_pairing")}
        vintages = [{k: v[k] for k in ("year", "drink_from", "peak_from", "peak_to", "drink_before", "stock")}
                    for v in wine["vintages"]]
        return (f"{guide_text()}\n\n---\n\nComplete this wine sheet. Current data:\n{summary}\n"
                f"Vintages: {vintages}\nField provenance: {wine['meta']}\n\n"
                "Research the exact wine, then call update_wine_info and set_drinking_window with sources. "
                "Finish with a short report of what you filled, the sources, and what stayed empty.")

    # ------------------------------------------------------------ write: wine data

    @mcp.tool(description="Create a wine sheet. Use only for a wine the owner asked to add. First call "
                          "get_naming_conventions and build `name` the way existing wines are named (usually "
                          "'Nom - Cuvée', designation such as '(domaine de)' placed like in the examples). "
                          "Descriptive fields need `sources` unless provided_by_user=true (values dictated by the owner).")
    def create_wine(name: str, type: str, appellation: str | None = None, producer: str | None = None,
                    region: str | None = None, country: str | None = None, grapes: str | None = None,
                    description: str | None = None, serving_temp: str | None = None,
                    food_pairing: str | None = None, sources: list[str] | None = None,
                    provided_by_user: bool = False, ctx: Context | None = None) -> dict:
        data = {k: v for k, v in dict(name=name, type=type, appellation=appellation, producer=producer, region=region,
                                      country=country, grapes=grapes, description=description,
                                      serving_temp=serving_temp, food_pairing=food_pairing).items() if v}
        descriptive = [k for k in data if k in AI_WINE_FIELDS]
        if descriptive and not provided_by_user and not sources:
            raise ToolError(f"sources are required for {descriptive} (or set provided_by_user=true)")
        with db(ctx, write=True) as conn:
            existing = cellar.find_same_wine(conn, name, appellation, type)
            if existing:
                raise ToolError(f"wine already exists (id {existing}): use add_bottles on it instead of creating a duplicate")
            warnings = cellar.naming_warnings(conn, name)
            wine_id = cellar.create_wine(conn, data, origin="user" if provided_by_user else "ai", sources=sources)
            wine = cellar.get_wine(conn, wine_id)
        wine["naming"] = {"pattern": cellar.name_pattern(name)}
        if warnings:
            wine["naming"]["warnings"] = warnings + [
                "tell the owner; rename with update_wine_info(fields={'name': ...}, confirmed_by_user=true) if they agree"]
        return wine

    @mcp.tool(description="Add a comment to a wine sheet (tasting impression, advice, information found). The text "
                          "is appended to the notes, automatically prefixed with '[IA date]' to show it was written "
                          "by an AI; existing notes are never modified. Cite the source inside the comment when the "
                          "comment states facts.")
    def add_wine_comment(wine_id: int, comment: str, ctx: Context | None = None) -> dict:
        with db(ctx, write=True) as conn:
            return cellar.append_wine_comment(conn, wine_id, comment)

    @mcp.tool(description="Complete descriptive fields of a wine sheet (producer, region, country, grapes, "
                          "description, serving_temp, food_pairing). Requires sources. Fields entered by the owner "
                          "are protected and reported in `skipped`.")
    def update_wine_info(wine_id: int, fields: dict[str, str], sources: list[str],
                         confirmed_by_user: bool = False, ctx: Context | None = None) -> dict:
        allowed = AI_WINE_FIELDS + (IDENTITY_FIELDS if confirmed_by_user else ())
        bad = [k for k in fields if k not in allowed]
        if bad:
            raise ToolError(f"fields not writable by AI: {bad}. Allowed: {list(allowed)}")
        if not confirmed_by_user and not [s for s in sources if s and s.strip()]:
            raise ToolError("sources are required: write only confirmed information")
        with db(ctx, write=True) as conn:
            return cellar.update_wine(conn, wine_id, fields, origin="user" if confirmed_by_user else "ai",
                                      sources=sources, protect_user=not confirmed_by_user)

    @mcp.tool(description="Set the drinking window of one vintage (years). year=0 for non-vintage. Requires "
                          "sources stating this window; protected values entered by the owner are skipped.")
    def set_drinking_window(wine_id: int, year: int, sources: list[str], drink_from: int | None = None,
                            peak_from: int | None = None, peak_to: int | None = None,
                            drink_before: int | None = None, confirmed_by_user: bool = False, ctx: Context | None = None) -> dict:
        if not confirmed_by_user and not [s for s in sources if s and s.strip()]:
            raise ToolError("sources are required: write only a window stated by a source")
        data = {k: v for k, v in dict(drink_from=drink_from, peak_from=peak_from, peak_to=peak_to,
                                      drink_before=drink_before).items() if v is not None}
        if not data:
            raise ToolError("give at least one of drink_from, peak_from, peak_to, drink_before")
        with db(ctx, write=True) as conn:
            row = conn.execute("SELECT id FROM vintages WHERE wine_id = ? AND year IS ?", (wine_id, _year(year))).fetchone()
            if not row:
                raise ToolError(f"wine {wine_id} has no vintage {year or 'NV'} in the cellar")
            return cellar.update_vintage(conn, row["id"], data, origin="user" if confirmed_by_user else "ai",
                                         sources=sources, protect_user=not confirmed_by_user)

    # ------------------------------------------------------------ write: stock (owner's explicit request only)

    @mcp.tool(description="Add bottles to the cellar (a purchase). Only on the owner's explicit request. "
                          "storage_id: a rack fills the first free slots, a box holds them loosely, none = unplaced. "
                          "`comment` is stored with an '[IA date]' marker.")
    def add_bottles(wine_id: int, quantity: int, year: int = 0, volume_cl: float = 75,
                    unit_price: float | None = None, purchase_date: str | None = None,
                    supplier: str | None = None, comment: str | None = None,
                    storage_id: int | None = None, ctx: Context | None = None) -> dict:
        with db(ctx, write=True) as conn:
            return cellar.add_bottles(conn, wine_id, year=_year(year), quantity=quantity, volume_cl=volume_cl,
                                      unit_price=unit_price, purchase_date=purchase_date, supplier=supplier,
                                      comment=cellar.mark_ai_comment(comment), storage_id=storage_id)

    @mcp.tool(description="Take bottles out of the cellar (drunk, gifted, lost). Give bottle_ids, or wine_id + year "
                          "+ count (loose bottles are taken first). Only on the owner's explicit request. `note` is stored "
                          "with an '[IA date]' marker.")
    def remove_bottles(bottle_ids: list[int] | None = None, wine_id: int | None = None, year: int = 0,
                       count: int = 1, status: Literal["drunk", "gifted", "lost"] = "drunk",
                       note: str | None = None, date: str | None = None, ctx: Context | None = None) -> list[dict]:
        with db(ctx, write=True) as conn:
            if not bottle_ids:
                if not wine_id:
                    raise ToolError("give bottle_ids or wine_id")
                bottle_ids = [b["id"] for b in cellar.pick_bottles(conn, wine_id, _year(year), count)]
                if len(bottle_ids) < count:
                    raise ToolError(f"only {len(bottle_ids)} bottle(s) of this vintage in the cellar")
            note = cellar.mark_ai_comment(note)
            return [cellar.remove_bottle(conn, bid, status, date, note) for bid in bottle_ids]

    @mcp.tool(description="Move a bottle: storage_id + row/col for a rack (1-based), storage_id alone for a box or "
                          "the first free slot, storage_id null to mark it unplaced.")
    def move_bottle(bottle_id: int, storage_id: int | None = None, row: int | None = None,
                    col: int | None = None, ctx: Context | None = None) -> dict:
        with db(ctx, write=True) as conn:
            return cellar.move_bottle(conn, bottle_id, storage_id, row, col)

    return mcp


