"""Cellar domain logic. Every function takes an open sqlite3 connection.

This module is the single source of truth for business rules; the REST API
(api.py) and the MCP server (mcp_server.py) are thin layers over it.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
from typing import Any, Iterable

from .db import transaction

WINE_FIELDS = (
    "name", "producer", "appellation", "region", "country", "type",
    "grapes", "description", "serving_temp", "food_pairing", "notes",
)
VINTAGE_FIELDS = ("drink_from", "peak_from", "peak_to", "drink_before", "notes")
LOT_FIELDS = ("purchase_date", "volume_cl", "unit_price", "supplier", "comment")
OUT_STATUSES = ("drunk", "gifted", "lost")
PHASES = ("keep", "drink", "peak", "decline", "unknown")
ORIGINS = ("user", "import", "ai")
AI_COMMENT_RE = re.compile(r"^\[IA(?: [0-9-]{10})?\]")


class CellarError(Exception):
    status = 400

    def __init__(self, message: str, **details: Any):
        super().__init__(message)
        self.message = message
        self.details = details


class NotFound(CellarError):
    status = 404


class Conflict(CellarError):
    status = 409


class Invalid(CellarError):
    status = 400


# ---------------------------------------------------------------- helpers

def today() -> str:
    return dt.date.today().isoformat()


def current_year() -> int:
    return dt.date.today().year


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _int_or_none(value: Any, field: str) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise Invalid(f"{field} must be an integer", field=field) from None


def _float_or_none(value: Any, field: str) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        raise Invalid(f"{field} must be a number", field=field) from None


def _date_or_none(value: Any, field: str = "date") -> str | None:
    text = _clean_text(value)
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y", "%Y/%m/%d", "%d.%m.%Y"):
        try:
            return dt.datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    raise Invalid(f"{field}: unrecognised date '{text}' (expected YYYY-MM-DD)", field=field)


def _year_or_none(value: Any) -> int | None:
    """Vintage year; 0 / '' / 'NV' mean non-vintage (None)."""
    if value is None:
        return None
    text = str(value).strip().upper()
    if text in {"", "0", "NV", "SA", "S.A.", "NM"}:
        return None
    year = _int_or_none(text, "year")
    if year is not None and not (1800 <= year <= 2200):
        raise Invalid(f"year {year} out of range", field="year")
    return year


def roman(n: int) -> str:
    out = ""
    for value, sym in ((1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
                       (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")):
        while n >= value:
            out += sym
            n -= value
    return out


def wine_label(appellation: str | None, name: str, year: int | None) -> str:
    parts = [p for p in (appellation, name) if p]
    label = " — ".join(parts)
    return f"{label} {year}" if year else f"{label} (NV)"


def location_dict(row: sqlite3.Row | dict) -> dict | None:
    if row["storage_id"] is None:
        return None
    loc = {
        "storage_id": row["storage_id"],
        "storage_name": row["storage_name"],
        "kind": row["storage_kind"],
        "row": row["row"],
        "col": row["col"],
    }
    loc["label"] = location_label(loc)
    return loc


def location_label(loc: dict | None) -> str | None:
    """'Principale 2-IV' for a rack slot, the box name for a box, None when unplaced."""
    if not loc:
        return None
    if loc.get("kind") == "rack" and loc.get("row"):
        return f"{loc['storage_name']} {loc['row']}-{roman(loc['col'])}"
    return str(loc["storage_name"])


def _record_movement(conn, kind: str, *, wine_id=None, bottle_id=None, count=1, label=None, year=None,
                     from_loc=None, to_loc=None, note=None, reason=None, date=None) -> None:
    conn.execute(
        """INSERT INTO movements (date, kind, reason, wine_id, bottle_id, count, label, year, from_loc, to_loc, note, actor)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (date or today(), kind, reason, wine_id, bottle_id, count, label, year, from_loc, to_loc, note,
         getattr(conn, "actor", None)),
    )


def mark_ai_comment(text: Any, date: str | None = None) -> str | None:
    """Prefix a comment written by an AI with '[IA YYYY-MM-DD] ' (shown as an "AI" badge in the UI)."""
    text = _clean_text(text)
    if not text:
        return None
    if AI_COMMENT_RE.match(text):
        return text
    return f"[IA {date or today()}] {text}"


# ---------------------------------------------------------------- types

def list_types(conn) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM wine_types ORDER BY sort, code")]


def _check_type(conn, code: str | None) -> str:
    if not code:
        raise Invalid("type is required", field="type")
    row = conn.execute("SELECT code FROM wine_types WHERE code = ?", (code,)).fetchone()
    if not row:
        codes = [r["code"] for r in conn.execute("SELECT code FROM wine_types")]
        raise Invalid(f"unknown type '{code}' (allowed: {', '.join(codes)})", field="type")
    return code


def add_type(conn, code: str, label_fr: str, label_en: str, color: str = "#999999", sort: int = 100) -> dict:
    code = (code or "").strip().lower()
    if not re.fullmatch(r"[a-z][a-z0-9_-]{1,30}", code):
        raise Invalid("type code must be lowercase letters/digits", field="code")
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", color or ""):
        raise Invalid("color must be #rrggbb", field="color")
    try:
        conn.execute("INSERT INTO wine_types (code, label_fr, label_en, color, sort) VALUES (?, ?, ?, ?, ?)",
                     (code, label_fr.strip(), label_en.strip(), color, sort))
    except sqlite3.IntegrityError:
        raise Conflict(f"type '{code}' already exists") from None
    return dict(conn.execute("SELECT * FROM wine_types WHERE code = ?", (code,)).fetchone())


def delete_type(conn, code: str) -> None:
    row = conn.execute("SELECT builtin FROM wine_types WHERE code = ?", (code,)).fetchone()
    if not row:
        raise NotFound("type not found")
    if row["builtin"]:
        raise Conflict("built-in types cannot be deleted")
    if conn.execute("SELECT 1 FROM wines WHERE type = ? LIMIT 1", (code,)).fetchone():
        raise Conflict("type is used by wines")
    conn.execute("DELETE FROM wine_types WHERE code = ?", (code,))


# ---------------------------------------------------------------- provenance

def _meta(conn, entity: str, entity_id: int) -> dict[str, dict]:
    out = {}
    for r in conn.execute("SELECT field, origin, sources, updated_at FROM field_meta WHERE entity = ? AND entity_id = ?",
                          (entity, entity_id)):
        out[r["field"]] = {
            "origin": r["origin"],
            "sources": json.loads(r["sources"]) if r["sources"] else [],
            "updated_at": r["updated_at"],
        }
    return out


def _set_meta(conn, entity: str, entity_id: int, field: str, origin: str, sources: list[str] | None, empty: bool):
    if empty:
        conn.execute("DELETE FROM field_meta WHERE entity = ? AND entity_id = ? AND field = ?", (entity, entity_id, field))
        return
    conn.execute(
        """INSERT INTO field_meta (entity, entity_id, field, origin, sources, updated_at)
           VALUES (?, ?, ?, ?, ?, datetime('now'))
           ON CONFLICT(entity, entity_id, field) DO UPDATE SET
             origin = excluded.origin, sources = excluded.sources, updated_at = excluded.updated_at""",
        (entity, entity_id, field, origin, json.dumps(sources) if sources else None),
    )


def _apply_fields(conn, entity: str, table: str, entity_id: int, current: dict, data: dict, allowed: Iterable[str],
                  origin: str, sources: list[str] | None, protect_user: bool, normalise) -> dict:
    """Shared update routine with provenance and user-data protection.

    protect_user=True (AI callers) refuses to overwrite a non-empty value whose
    origin is 'user' or 'import'.
    """
    if origin not in ORIGINS:
        raise Invalid(f"origin must be one of {ORIGINS}")
    meta = _meta(conn, entity, entity_id)
    updated, skipped, sets, params = [], [], [], []
    for field, raw in data.items():
        if field not in allowed:
            skipped.append({"field": field, "reason": "unknown field"})
            continue
        value = normalise(field, raw)
        old = current.get(field)
        if value == old:
            continue
        if protect_user and old not in (None, "") and meta.get(field, {}).get("origin", "user") in ("user", "import"):
            skipped.append({"field": field, "reason": "protected: value entered by the user", "current": old})
            continue
        sets.append(f"{field} = ?")
        params.append(value)
        updated.append(field)
        _set_meta(conn, entity, entity_id, field, origin, sources, value in (None, ""))
    if sets:
        if table == "wines":
            sets.append("updated_at = datetime('now')")
        conn.execute(f"UPDATE {table} SET {', '.join(sets)} WHERE id = ?", (*params, entity_id))
    return {"updated": updated, "skipped": skipped}


# ---------------------------------------------------------------- wines

def _wine_row(conn, wine_id: int) -> dict:
    row = conn.execute("SELECT * FROM wines WHERE id = ?", (wine_id,)).fetchone()
    if not row:
        raise NotFound(f"wine {wine_id} not found")
    return dict(row)


def _normalise_wine_field(conn):
    def normalise(field, raw):
        value = _clean_text(raw)
        if field == "type":
            return _check_type(conn, value)
        if field == "name" and not value:
            raise Invalid("name is required", field="name")
        return value
    return normalise


def create_wine(conn, data: dict, origin: str = "user", sources: list[str] | None = None) -> int:
    name = _clean_text(data.get("name"))
    if not name:
        raise Invalid("name is required", field="name")
    wine_type = _check_type(conn, _clean_text(data.get("type")))
    with transaction(conn):
        cur = conn.execute("INSERT INTO wines (name, type) VALUES (?, ?)", (name, wine_type))
        wine_id = cur.lastrowid
        for f in ("name", "type"):
            _set_meta(conn, "wine", wine_id, f, origin, sources, False)
        rest = {k: v for k, v in data.items() if k in WINE_FIELDS and k not in ("name", "type")}
        _apply_fields(conn, "wine", "wines", wine_id, {"name": name, "type": wine_type}, rest, WINE_FIELDS,
                      origin, sources, False, _normalise_wine_field(conn))
    return wine_id


def update_wine(conn, wine_id: int, data: dict, origin: str = "user", sources: list[str] | None = None,
                protect_user: bool = False) -> dict:
    with transaction(conn):
        current = _wine_row(conn, wine_id)
        return _apply_fields(conn, "wine", "wines", wine_id, current, data, WINE_FIELDS, origin, sources,
                             protect_user, _normalise_wine_field(conn))


def delete_wine(conn, wine_id: int) -> None:
    with transaction(conn):
        _wine_row(conn, wine_id)
        conn.execute("DELETE FROM field_meta WHERE entity = 'wine' AND entity_id = ?", (wine_id,))
        conn.execute("""DELETE FROM field_meta WHERE entity = 'vintage'
                        AND entity_id IN (SELECT id FROM vintages WHERE wine_id = ?)""", (wine_id,))
        conn.execute("DELETE FROM wines WHERE id = ?", (wine_id,))


def set_wine_image(conn, wine_id: int, filename: str | None, origin: str | None) -> str | None:
    with transaction(conn):
        old = _wine_row(conn, wine_id)["image"]
        conn.execute("UPDATE wines SET image = ?, image_origin = ?, updated_at = datetime('now') WHERE id = ?",
                     (filename, origin if filename else None, wine_id))
    return old


_BOTTLE_SELECT = """
    SELECT b.id AS bottle_id, b.status, b.row, b.col, b.storage_id, b.out_date, b.out_note, b.rating,
           s.name AS storage_name, s.kind AS storage_kind,
           l.id AS lot_id, l.volume_cl, l.unit_price, l.purchase_date,
           v.id AS vintage_id, v.year, w.id AS wine_id, w.name, w.appellation, w.type
      FROM bottles b
      JOIN lots l ON l.id = b.lot_id
      JOIN vintages v ON v.id = l.vintage_id
      JOIN wines w ON w.id = v.wine_id
      LEFT JOIN storages s ON s.id = b.storage_id
"""


def _bottle_dict(r) -> dict:
    return {
        "id": r["bottle_id"],
        "status": r["status"],
        "wine_id": r["wine_id"],
        "vintage_id": r["vintage_id"],
        "lot_id": r["lot_id"],
        "name": r["name"],
        "appellation": r["appellation"],
        "type": r["type"],
        "year": r["year"],
        "volume_cl": r["volume_cl"],
        "unit_price": r["unit_price"],
        "location": location_dict(r),
        "out_date": r["out_date"],
        "out_note": r["out_note"],
    }


def get_wine(conn, wine_id: int, year_now: int | None = None) -> dict:
    wine = _wine_row(conn, wine_id)
    year_now = year_now or current_year()
    wine["meta"] = _meta(conn, "wine", wine_id)
    vintages = []
    for v in conn.execute("""SELECT v.*, (SELECT count(*) FROM bottles b JOIN lots l ON l.id = b.lot_id
                                     WHERE l.vintage_id = v.id AND b.status = 'in_cellar') AS stock
                               FROM vintages v WHERE v.wine_id = ? ORDER BY ifnull(v.year, 0) DESC""", (wine_id,)):
        vd = dict(v)
        vd["phase"] = phase_for(vd, year_now)
        vd["meta"] = _meta(conn, "vintage", vd["id"])
        vintages.append(vd)
    lots = []
    for lot in conn.execute("""SELECT l.*, v.year,
                                      (SELECT count(*) FROM bottles b WHERE b.lot_id = l.id AND b.status = 'in_cellar') AS in_cellar
                                 FROM lots l JOIN vintages v ON v.id = l.vintage_id
                                WHERE v.wine_id = ?
                                ORDER BY ifnull(l.purchase_date, l.created_at) DESC, l.id DESC""", (wine_id,)):
        lots.append(dict(lot))
    bottles = [_bottle_dict(r) for r in conn.execute(
        _BOTTLE_SELECT + " WHERE w.id = ? ORDER BY b.status != 'in_cellar', ifnull(v.year, 0), s.sort, b.row, b.col, b.id",
        (wine_id,))]
    history = [dict(r) for r in conn.execute(
        "SELECT * FROM movements WHERE wine_id = ? ORDER BY date DESC, id DESC LIMIT 100", (wine_id,))]
    wine.update(
        vintages=vintages,
        lots=lots,
        bottles=bottles,
        history=history,
        stock=sum(1 for b in bottles if b["status"] == "in_cellar"),
    )
    return wine


def search_wines(conn, q: str | None = None, wine_type: str | None = None, in_stock: bool = False,
                 limit: int = 50) -> list[dict]:
    sql = """SELECT w.id, w.name, w.producer, w.appellation, w.region, w.country, w.type, w.image,
                    (SELECT count(*) FROM bottles b JOIN lots l ON l.id = b.lot_id JOIN vintages v ON v.id = l.vintage_id
                      WHERE v.wine_id = w.id AND b.status = 'in_cellar') AS stock,
                    (SELECT group_concat(ifnull(year, 'NV'), ',') FROM vintages WHERE wine_id = w.id) AS years
               FROM wines w WHERE 1 = 1"""
    params: list[Any] = []
    if q:
        for word in q.split():
            sql += " AND (w.name || ' ' || ifnull(w.producer,'') || ' ' || ifnull(w.appellation,'') || ' ' || ifnull(w.region,'') || ' ' || ifnull(w.grapes,'')) LIKE ?"
            params.append(f"%{word}%")
    if wine_type:
        sql += " AND w.type = ?"
        params.append(wine_type)
    if in_stock:
        sql = f"SELECT * FROM ({sql}) WHERE stock > 0"
    sql += " ORDER BY appellation COLLATE NOCASE, name COLLATE NOCASE LIMIT ?"
    params.append(int(limit))
    return [dict(r) for r in conn.execute(sql, params)]


def incomplete_wines(conn, limit: int = 20) -> list[dict]:
    """Wines in stock that miss descriptive data or a drinking window (for AI enrichment)."""
    rows = []
    for r in conn.execute(
        """SELECT w.id, w.name, w.appellation, w.type, w.region, w.description, w.grapes, w.producer
             FROM wines w
            WHERE EXISTS (SELECT 1 FROM vintages v JOIN lots l ON l.vintage_id = v.id JOIN bottles b ON b.lot_id = l.id
                           WHERE v.wine_id = w.id AND b.status = 'in_cellar')
            ORDER BY w.appellation, w.name"""):
        missing = [f for f in ("region", "description", "grapes", "producer") if not r[f]]
        no_window = [v["year"] for v in conn.execute(
            """SELECT year FROM vintages WHERE wine_id = ? AND drink_from IS NULL AND peak_from IS NULL
                                              AND drink_before IS NULL""", (r["id"],))]
        if missing or no_window:
            rows.append({"id": r["id"], "name": r["name"], "appellation": r["appellation"], "type": r["type"],
                         "missing_fields": missing, "vintages_without_window": no_window})
        if len(rows) >= limit:
            break
    return rows


def append_wine_comment(conn, wine_id: int, comment: str) -> dict:
    """Append an AI comment to the wine notes; existing text is never modified."""
    marked = mark_ai_comment(comment)
    if not marked:
        raise Invalid("comment is empty", field="comment")
    with transaction(conn):
        current = _wine_row(conn, wine_id)["notes"]
        notes = f"{current.rstrip()}\n\n{marked}" if current else marked
        conn.execute("UPDATE wines SET notes = ?, updated_at = datetime('now') WHERE id = ?", (notes, wine_id))
        if not current:
            _set_meta(conn, "wine", wine_id, "notes", "ai", None, False)
    return {"wine_id": wine_id, "added": marked}


_DESIGNATION_RE = re.compile(r"\(([^)]*)\)")


def name_pattern(name: str) -> str:
    """Shape of a wine name: 'Nom', 'Nom - Cuvée', 'Nom (désignation)', 'Nom - Cuvée (désignation)'…"""
    has_sep = " - " in name
    paren = _DESIGNATION_RE.search(name)
    if has_sep and paren:
        return "Nom (désignation) - Cuvée" if paren.start() < name.index(" - ") else "Nom - Cuvée (désignation)"
    if has_sep:
        return "Nom - Cuvée"
    if paren:
        return "Nom (désignation)"
    return "Nom"


def naming_conventions(conn, appellation: str | None = None, producer: str | None = None, limit: int = 40) -> dict:
    """Existing wine names and their observed shapes, so an AI can name a new wine consistently."""
    rows = [dict(r) for r in conn.execute("SELECT id, name, appellation, type, producer FROM wines ORDER BY id DESC")]
    patterns: dict[str, list[str]] = {}
    designations: dict[str, int] = {}
    for r in rows:
        patterns.setdefault(name_pattern(r["name"]), []).append(r["name"])
        for d in _DESIGNATION_RE.findall(r["name"]):
            key = d.strip().lower()
            designations[key] = designations.get(key, 0) + 1

    def score(r):
        s = 0
        if appellation and (r["appellation"] or "").lower() == appellation.lower():
            s += 2
        if producer and any(w.lower() in r["name"].lower() for w in producer.split() if len(w) > 3):
            s += 3
        return -s

    examples = sorted(rows, key=score)[:limit]
    return {
        "total": len(rows),
        "patterns": [{"pattern": p, "count": len(v), "examples": v[:5]}
                     for p, v in sorted(patterns.items(), key=lambda kv: -len(kv[1]))],
        "designations_in_parentheses": dict(sorted(designations.items(), key=lambda kv: -kv[1])[:20]),
        "examples": [{"id": r["id"], "name": r["name"], "appellation": r["appellation"], "type": r["type"]}
                     for r in examples],
    }


_LEADING_DESIGNATION_RE = re.compile(r"^(domaine|château|chateau|clos|mas|maison|cave|caves|vignobles?)\b", re.I)


def naming_warnings(conn, name: str) -> list[str]:
    """Soft checks of a new name against the shapes used in this cellar (never blocking)."""
    conv = naming_conventions(conn, limit=0)
    total = conv["total"]
    if total < 3:
        return []
    counts = {p["pattern"]: p["count"] for p in conv["patterns"]}
    warnings = []
    plain_share = counts.get("Nom", 0) / total
    if name_pattern(name) == "Nom" and plain_share < 0.2:
        common = conv["patterns"][0]
        warnings.append(f"only {counts.get('Nom', 0)}/{total} existing names have neither ' - ' nor a designation in "
                        f"parentheses; usual shape is '{common['pattern']}' (e.g. {common['examples'][:3]})")
    lead = _LEADING_DESIGNATION_RE.match(name.strip())
    if lead and conv["designations_in_parentheses"]:
        all_names = [r["name"] for r in conn.execute("SELECT name FROM wines")]
        leading_existing = sum(1 for n in all_names if _LEADING_DESIGNATION_RE.match(n))
        if leading_existing == 0:
            warnings.append(f"'{lead.group(1)}' is written at the start of the name, but this cellar puts designations "
                            f"in parentheses after the name, e.g. {list(conv['designations_in_parentheses'])[:3]}")
    return warnings


def find_same_wine(conn, name: str, appellation: str | None, wine_type: str) -> int | None:
    row = conn.execute(
        """SELECT id FROM wines WHERE lower(name) = lower(?) AND lower(ifnull(appellation, '')) = lower(?)
             AND type = ?""", (name.strip(), (appellation or "").strip(), wine_type)).fetchone()
    return row["id"] if row else None


# ---------------------------------------------------------------- vintages & drinking windows

def get_or_create_vintage(conn, wine_id: int, year: Any) -> int:
    year = _year_or_none(year)
    row = conn.execute("SELECT id FROM vintages WHERE wine_id = ? AND year IS ?", (wine_id, year)).fetchone()
    if row:
        return row["id"]
    _wine_row(conn, wine_id)
    return conn.execute("INSERT INTO vintages (wine_id, year) VALUES (?, ?)", (wine_id, year)).lastrowid


def update_vintage(conn, vintage_id: int, data: dict, origin: str = "user", sources: list[str] | None = None,
                   protect_user: bool = False) -> dict:
    with transaction(conn):
        row = conn.execute("SELECT * FROM vintages WHERE id = ?", (vintage_id,)).fetchone()
        if not row:
            raise NotFound(f"vintage {vintage_id} not found")
        current = dict(row)

        def normalise(field, raw):
            if field == "notes":
                return _clean_text(raw)
            value = _int_or_none(raw, field)
            if value is not None and not (1800 <= value <= 2300):
                raise Invalid(f"{field} out of range", field=field)
            return value

        merged = {**current, **{k: normalise(k, v) for k, v in data.items() if k in VINTAGE_FIELDS}}
        seq = [merged[f] for f in ("drink_from", "peak_from", "peak_to", "drink_before") if merged[f] is not None]
        if seq != sorted(seq):
            raise Invalid("expected drink_from <= peak_from <= peak_to <= drink_before")
        if merged["year"] and merged["drink_from"] and merged["drink_from"] < merged["year"]:
            raise Invalid("drink_from is before the vintage year")
        return _apply_fields(conn, "vintage", "vintages", vintage_id, current, data, VINTAGE_FIELDS,
                             origin, sources, protect_user, normalise)


def phase_for(v: dict, year: int) -> str:
    """Drinking phase of a vintage for a given calendar year.

    keep    : before drink_from
    drink   : ready, before the peak
    peak    : within [peak_from, peak_to]
    decline : after the peak (or after drink_before)
    unknown : no window at all
    """
    start, p1, p2, end = v.get("drink_from"), v.get("peak_from"), v.get("peak_to"), v.get("drink_before")
    if p1 is None and p2 is not None:
        p1 = p2
    if p2 is None and p1 is not None:
        p2 = p1
    if start is None and p1 is None and end is None:
        return "unknown"
    if start is not None and year < start:
        return "keep"
    if p1 is not None:
        if year < p1:
            # ready (drink_from reached) but not yet at its peak; without drink_from, wait for the peak
            return "drink" if start is not None else "keep"
        if year <= p2:
            return "peak"
        return "decline"
    if end is not None and year > end:
        return "decline"
    return "drink"


def is_overdue(v: dict, year: int) -> bool:
    return v.get("drink_before") is not None and year > v["drink_before"]


# ---------------------------------------------------------------- storages

def _storage(conn, storage_id: int) -> dict:
    row = conn.execute("SELECT * FROM storages WHERE id = ?", (storage_id,)).fetchone()
    if not row:
        raise NotFound(f"storage {storage_id} not found")
    return dict(row)


def list_storages(conn) -> list[dict]:
    out = []
    for r in conn.execute("""SELECT s.*, (SELECT count(*) FROM bottles b WHERE b.storage_id = s.id AND b.status = 'in_cellar') AS count
                               FROM storages s ORDER BY s.sort, s.id"""):
        d = dict(r)
        d["capacity"] = d["rows"] * d["cols"] if d["kind"] == "rack" else None
        out.append(d)
    return out


def _validate_storage(data: dict, kind: str) -> tuple[int | None, int | None]:
    if kind not in ("rack", "box"):
        raise Invalid("kind must be 'rack' or 'box'", field="kind")
    if kind == "box":
        return None, None
    rows = _int_or_none(data.get("rows"), "rows")
    cols = _int_or_none(data.get("cols"), "cols")
    if not rows or not cols or not (1 <= rows <= 100 and 1 <= cols <= 100):
        raise Invalid("a rack needs rows and cols between 1 and 100")
    return rows, cols


def create_storage(conn, data: dict) -> int:
    name = _clean_text(data.get("name"))
    if not name:
        raise Invalid("name is required", field="name")
    kind = data.get("kind") or "rack"
    rows, cols = _validate_storage(data, kind)
    sort = _int_or_none(data.get("sort"), "sort")
    if sort is None:
        sort = (conn.execute("SELECT max(sort) FROM storages").fetchone()[0] or 0) + 10
    return conn.execute("INSERT INTO storages (name, kind, rows, cols, sort, notes) VALUES (?, ?, ?, ?, ?, ?)",
                        (name, kind, rows, cols, sort, _clean_text(data.get("notes")))).lastrowid


def update_storage(conn, storage_id: int, data: dict, unplace_overflow: bool = False) -> dict:
    with transaction(conn):
        cur = _storage(conn, storage_id)
        name = _clean_text(data.get("name", cur["name"]))
        if not name:
            raise Invalid("name is required", field="name")
        kind = data.get("kind", cur["kind"])
        rows, cols = _validate_storage({"rows": data.get("rows", cur["rows"]), "cols": data.get("cols", cur["cols"])}, kind)
        moved = []
        if kind == "rack":
            overflow = conn.execute(
                """SELECT b.id FROM bottles b WHERE b.storage_id = ? AND b.status = 'in_cellar'
                     AND (b.row IS NULL OR b.row > ? OR b.col > ?)""", (storage_id, rows, cols)).fetchall()
            if overflow:
                if not unplace_overflow:
                    raise Conflict("bottles are outside the new grid", bottles=[r["id"] for r in overflow])
                for r in overflow:
                    move_bottle(conn, r["id"], None)
                    moved.append(r["id"])
        elif cur["kind"] == "rack":
            conn.execute("UPDATE bottles SET row = NULL, col = NULL WHERE storage_id = ?", (storage_id,))
        sort = _int_or_none(data.get("sort", cur["sort"]), "sort")
        conn.execute("UPDATE storages SET name = ?, kind = ?, rows = ?, cols = ?, sort = ?, notes = ? WHERE id = ?",
                     (name, kind, rows, cols, sort, _clean_text(data.get("notes", cur["notes"])), storage_id))
    return {"unplaced": moved}


def delete_storage(conn, storage_id: int) -> dict:
    with transaction(conn):
        _storage(conn, storage_id)
        ids = [r["id"] for r in conn.execute(
            "SELECT id FROM bottles WHERE storage_id = ? AND status = 'in_cellar'", (storage_id,))]
        for bottle_id in ids:
            move_bottle(conn, bottle_id, None)
        conn.execute("UPDATE bottles SET storage_id = NULL, row = NULL, col = NULL WHERE storage_id = ?", (storage_id,))
        conn.execute("DELETE FROM storages WHERE id = ?", (storage_id,))
    return {"unplaced": ids}


def _free_slots(conn, storage: dict) -> list[tuple[int, int]]:
    taken = {(r["row"], r["col"]) for r in conn.execute(
        "SELECT row, col FROM bottles WHERE storage_id = ? AND status = 'in_cellar' AND row IS NOT NULL", (storage["id"],))}
    return [(r, c) for r in range(1, storage["rows"] + 1) for c in range(1, storage["cols"] + 1) if (r, c) not in taken]


# ---------------------------------------------------------------- bottles

def _bottle(conn, bottle_id: int) -> dict:
    row = conn.execute(_BOTTLE_SELECT + " WHERE b.id = ?", (bottle_id,)).fetchone()
    if not row:
        raise NotFound(f"bottle {bottle_id} not found")
    return _bottle_dict(row)


def get_bottle(conn, bottle_id: int) -> dict:
    return _bottle(conn, bottle_id)


def add_bottles(conn, wine_id: int, *, year: Any = None, quantity: Any = 1, volume_cl: Any = 75, unit_price: Any = None,
                purchase_date: Any = None, supplier: Any = None, comment: Any = None, storage_id: Any = None,
                positions: list[dict] | None = None, reason: str = "purchase") -> dict:
    quantity = _int_or_none(quantity, "quantity") or 0
    if not (0 < quantity <= 500):
        raise Invalid("quantity must be between 1 and 500", field="quantity")
    volume = _float_or_none(volume_cl, "volume_cl") or 75
    price = _float_or_none(unit_price, "unit_price")
    pdate = _date_or_none(purchase_date, "purchase_date")
    storage_id = _int_or_none(storage_id, "storage_id")
    with transaction(conn):
        wine = _wine_row(conn, wine_id)
        vintage_id = get_or_create_vintage(conn, wine_id, year)
        vyear = conn.execute("SELECT year FROM vintages WHERE id = ?", (vintage_id,)).fetchone()["year"]
        lot_id = conn.execute(
            """INSERT INTO lots (vintage_id, purchase_date, volume_cl, unit_price, quantity, supplier, comment)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (vintage_id, pdate, volume, price, quantity, _clean_text(supplier), _clean_text(comment))).lastrowid
        slots: list[tuple[int | None, int | None, int | None]] = []
        storage = _storage(conn, storage_id) if storage_id else None
        if storage and storage["kind"] == "rack":
            free = _free_slots(conn, storage)
            if positions:
                wanted = [(int(p["row"]), int(p["col"])) for p in positions]
                for pos in wanted:
                    if pos not in free:
                        raise Conflict(f"slot {pos[0]}-{roman(pos[1])} is not free in {storage['name']}")
                if len(set(wanted)) != len(wanted):
                    raise Invalid("duplicate positions")
                free = wanted + [s for s in free if s not in wanted]
            for i in range(quantity):
                slots.append((storage_id, *free[i]) if i < len(free) else (None, None, None))
        elif storage:
            slots = [(storage_id, None, None)] * quantity
        else:
            slots = [(None, None, None)] * quantity
        bottle_ids = []
        for sid, r, c in slots:
            bottle_ids.append(conn.execute("INSERT INTO bottles (lot_id, storage_id, row, col) VALUES (?, ?, ?, ?)",
                                           (lot_id, sid, r, c)).lastrowid)
        placed = sum(1 for s in slots if s[0])
        _record_movement(conn, "in", wine_id=wine_id, count=quantity, reason=reason, year=vyear,
                         label=wine_label(wine["appellation"], wine["name"], vyear),
                         to_loc=storage["name"] if storage else None, note=_clean_text(comment), date=pdate)
    return {"lot_id": lot_id, "vintage_id": vintage_id, "bottle_ids": bottle_ids, "placed": placed,
            "unplaced": quantity - placed}


def update_lot(conn, lot_id: int, data: dict) -> None:
    sets, params = [], []
    for field in LOT_FIELDS:
        if field not in data:
            continue
        raw = data[field]
        if field == "purchase_date":
            value = _date_or_none(raw, field)
        elif field in ("volume_cl", "unit_price"):
            value = _float_or_none(raw, field)
            if field == "volume_cl" and not value:
                raise Invalid("volume_cl is required", field=field)
        else:
            value = _clean_text(raw)
        sets.append(f"{field} = ?")
        params.append(value)
    if "year" in data:
        row = conn.execute("SELECT v.wine_id FROM lots l JOIN vintages v ON v.id = l.vintage_id WHERE l.id = ?",
                           (lot_id,)).fetchone()
        if not row:
            raise NotFound("lot not found")
        sets.append("vintage_id = ?")
        params.append(get_or_create_vintage(conn, row["wine_id"], data["year"]))
    if not sets:
        return
    with transaction(conn):
        if conn.execute(f"UPDATE lots SET {', '.join(sets)} WHERE id = ?", (*params, lot_id)).rowcount == 0:
            raise NotFound("lot not found")


def delete_lot(conn, lot_id: int) -> None:
    """Correction tool: removes a purchase and its bottles without history."""
    if conn.execute("DELETE FROM lots WHERE id = ?", (lot_id,)).rowcount == 0:
        raise NotFound("lot not found")


def delete_bottle(conn, bottle_id: int) -> None:
    """Correction tool: hard-delete one bottle (typo, double entry). Use remove_bottle to log consumption."""
    with transaction(conn):
        b = _bottle(conn, bottle_id)
        conn.execute("DELETE FROM bottles WHERE id = ?", (bottle_id,))
        conn.execute("UPDATE lots SET quantity = max(quantity - 1, 0) WHERE id = ?", (b["lot_id"],))
        if conn.execute("SELECT quantity FROM lots WHERE id = ?", (b["lot_id"],)).fetchone()["quantity"] == 0:
            conn.execute("DELETE FROM lots WHERE id = ?", (b["lot_id"],))


def remove_bottle(conn, bottle_id: int, status: str = "drunk", date: Any = None, note: Any = None,
                  rating: Any = None) -> dict:
    if status not in OUT_STATUSES:
        raise Invalid(f"status must be one of {OUT_STATUSES}", field="status")
    out_date = _date_or_none(date) or today()
    rating = _int_or_none(rating, "rating")
    if rating is not None and not 0 <= rating <= 5:
        raise Invalid("rating must be 0-5", field="rating")
    with transaction(conn):
        b = _bottle(conn, bottle_id)
        if b["status"] != "in_cellar":
            raise Conflict("bottle is not in the cellar")
        conn.execute("UPDATE bottles SET status = ?, out_date = ?, out_note = ?, rating = ? WHERE id = ?",
                     (status, out_date, _clean_text(note), rating, bottle_id))
        _record_movement(conn, "out", reason=status, wine_id=b["wine_id"], bottle_id=bottle_id, year=b["year"],
                         label=wine_label(b["appellation"], b["name"], b["year"]),
                         from_loc=location_label(b["location"]), note=_clean_text(note), date=out_date)
    return _bottle(conn, bottle_id)


def restore_bottle(conn, bottle_id: int) -> dict:
    with transaction(conn):
        b = _bottle(conn, bottle_id)
        if b["status"] == "in_cellar":
            raise Conflict("bottle is already in the cellar")
        loc = b["location"]
        if loc and loc["row"] and conn.execute(
                "SELECT 1 FROM bottles WHERE storage_id = ? AND row = ? AND col = ? AND status = 'in_cellar'",
                (loc["storage_id"], loc["row"], loc["col"])).fetchone():
            conn.execute("UPDATE bottles SET storage_id = NULL, row = NULL, col = NULL WHERE id = ?", (bottle_id,))
        conn.execute("UPDATE bottles SET status = 'in_cellar', out_date = NULL, out_note = NULL WHERE id = ?",
                     (bottle_id,))
        _record_movement(conn, "restore", wine_id=b["wine_id"], bottle_id=bottle_id, year=b["year"],
                         label=wine_label(b["appellation"], b["name"], b["year"]))
    return _bottle(conn, bottle_id)


def move_bottle(conn, bottle_id: int, storage_id: Any, row: Any = None, col: Any = None, swap: bool = False) -> dict:
    storage_id = _int_or_none(storage_id, "storage_id")
    row, col = _int_or_none(row, "row"), _int_or_none(col, "col")
    with transaction(conn):
        b = _bottle(conn, bottle_id)
        if b["status"] != "in_cellar":
            raise Conflict("bottle is not in the cellar")
        target_label = None
        swapped = None
        if storage_id:
            storage = _storage(conn, storage_id)
            if storage["kind"] == "rack":
                if row is None or col is None:
                    free = _free_slots(conn, storage)
                    if not free:
                        raise Conflict(f"{storage['name']} is full")
                    row, col = free[0]
                if not (1 <= row <= storage["rows"] and 1 <= col <= storage["cols"]):
                    raise Invalid("position outside the rack")
                other = conn.execute("""SELECT id FROM bottles WHERE storage_id = ? AND row = ? AND col = ?
                                         AND status = 'in_cellar' AND id != ?""",
                                     (storage_id, row, col, bottle_id)).fetchone()
                if other:
                    if not swap:
                        raise Conflict(f"slot {row}-{roman(col)} is occupied", bottle_id=other["id"])
                    old = b["location"]
                    conn.execute("UPDATE bottles SET storage_id = NULL, row = NULL, col = NULL WHERE id = ?", (bottle_id,))
                    conn.execute("UPDATE bottles SET storage_id = ?, row = ?, col = ? WHERE id = ?",
                                 (old["storage_id"] if old else None, old["row"] if old else None,
                                  old["col"] if old else None, other["id"]))
                    swapped = other["id"]
            else:
                row = col = None
            target_label = location_label({"storage_name": storage["name"], "kind": storage["kind"], "row": row, "col": col})
        else:
            row = col = None
        conn.execute("UPDATE bottles SET storage_id = ?, row = ?, col = ? WHERE id = ?", (storage_id, row, col, bottle_id))
        _record_movement(conn, "move", wine_id=b["wine_id"], bottle_id=bottle_id, year=b["year"],
                         label=wine_label(b["appellation"], b["name"], b["year"]),
                         from_loc=location_label(b["location"]), to_loc=target_label)
    result = _bottle(conn, bottle_id)
    result["swapped_with"] = swapped
    return result


def pick_bottles(conn, wine_id: int, year: Any = None, count: int = 1) -> list[dict]:
    """Bottles of a wine/vintage still in the cellar, loose ones (unplaced, boxes) first."""
    y = _year_or_none(year)
    rows = conn.execute(_BOTTLE_SELECT + """ WHERE w.id = ? AND v.year IS ? AND b.status = 'in_cellar'
                         ORDER BY b.row IS NOT NULL, b.storage_id IS NOT NULL, b.id LIMIT ?""",
                        (wine_id, y, int(count))).fetchall()
    return [_bottle_dict(r) for r in rows]


# ---------------------------------------------------------------- views

def cellar_rows(conn, year_now: int | None = None, include_empty: bool = False) -> list[dict]:
    """One row per wine + vintage + bottle volume (the 'Liste de cave')."""
    year_now = year_now or current_year()
    sql = """
        SELECT w.id AS wine_id, w.name, w.appellation, w.region, w.producer, w.type, w.image,
               v.id AS vintage_id, v.year, v.drink_from, v.peak_from, v.peak_to, v.drink_before,
               l.volume_cl,
               sum(CASE WHEN b.status = 'in_cellar' THEN 1 ELSE 0 END) AS stock,
               avg(CASE WHEN b.status = 'in_cellar' THEN l.unit_price END) AS price,
               max(l.unit_price) AS last_price
          FROM wines w
          JOIN vintages v ON v.wine_id = w.id
          JOIN lots l ON l.vintage_id = v.id
          JOIN bottles b ON b.lot_id = l.id
         GROUP BY v.id, l.volume_cl
    """
    rows = []
    for r in conn.execute(sql):
        d = dict(r)
        if not include_empty and not d["stock"]:
            continue
        if d["price"] is None:
            d["price"] = d.pop("last_price")
        else:
            d.pop("last_price")
        d["phase"] = phase_for(d, year_now)
        d["overdue"] = is_overdue(d, year_now)
        rows.append(d)
    rows.sort(key=lambda d: ((d["appellation"] or "").lower(), d["name"].lower(), d["year"] or 0))
    return rows


def schedule(conn, year_now: int | None = None) -> dict:
    year_now = year_now or current_year()
    groups: dict[str, list] = {p: [] for p in PHASES}
    seen = set()
    for r in cellar_rows(conn, year_now):
        key = r["vintage_id"]
        if key in seen:
            for item in groups[r["phase"]]:
                if item["vintage_id"] == key:
                    item["stock"] += r["stock"]
            continue
        seen.add(key)
        groups[r["phase"]].append({k: r[k] for k in (
            "wine_id", "vintage_id", "name", "appellation", "type", "year", "drink_from", "peak_from",
            "peak_to", "drink_before", "stock", "overdue")})
    return {"year": year_now, "phases": groups}


def summary(conn, year_now: int | None = None) -> dict:
    year_now = year_now or current_year()
    by_type = {t["code"]: 0 for t in list_types(conn)}
    value = 0.0
    rows = cellar_rows(conn, year_now)
    by_phase = {p: 0 for p in PHASES}
    overdue = 0
    for r in rows:
        by_type[r["type"]] = by_type.get(r["type"], 0) + r["stock"]
        by_phase[r["phase"]] += r["stock"]
        if r["overdue"]:
            overdue += r["stock"]
    for r in conn.execute("""SELECT sum(l.unit_price) AS v FROM bottles b JOIN lots l ON l.id = b.lot_id
                              WHERE b.status = 'in_cellar'"""):
        value = r["v"] or 0.0
    unplaced = conn.execute("SELECT count(*) FROM bottles WHERE status = 'in_cellar' AND storage_id IS NULL").fetchone()[0]
    priority = [r for r in rows if r["phase"] in ("decline", "peak")]
    priority.sort(key=lambda r: (not r["overdue"], r["phase"] != "decline", r["drink_before"] or 9999))
    return {
        "year": year_now,
        "total": sum(by_type.values()),
        "references": len({r["wine_id"] for r in rows}),
        "by_type": by_type,
        "by_phase": by_phase,
        "overdue": overdue,
        "value": round(value, 2),
        "unplaced": unplaced,
        "storages": list_storages(conn),
        "priority": priority[:8],
    }


def plan(conn) -> dict:
    storages = list_storages(conn)
    by_storage: dict[int, list] = {s["id"]: [] for s in storages}
    unplaced = []
    for r in conn.execute(_BOTTLE_SELECT + " WHERE b.status = 'in_cellar' ORDER BY w.appellation, w.name, v.year"):
        d = _bottle_dict(r)
        if r["storage_id"] is None or r["storage_id"] not in by_storage:
            unplaced.append(d)
        else:
            by_storage[r["storage_id"]].append(d)
    for s in storages:
        s["bottles"] = by_storage[s["id"]]
    return {"storages": storages, "unplaced": unplaced}


def history(conn, kind: str | None = None, year: int | None = None, wine_id: int | None = None,
            limit: int = 500) -> list[dict]:
    sql = """SELECT m.*, w.type FROM movements m LEFT JOIN wines w ON w.id = m.wine_id WHERE 1 = 1"""
    params: list[Any] = []
    if kind:
        sql += " AND m.kind = ?"
        params.append(kind)
    if year:
        sql += " AND substr(m.date, 1, 4) = ?"
        params.append(str(year))
    if wine_id:
        sql += " AND m.wine_id = ?"
        params.append(wine_id)
    sql += " ORDER BY m.date DESC, m.id DESC LIMIT ?"
    params.append(int(limit))
    return [dict(r) for r in conn.execute(sql, params)]


def find_bottles(conn, q: str) -> list[dict]:
    """Where are the bottles matching a free-text query?"""
    wines = search_wines(conn, q, in_stock=True, limit=20)
    out = []
    for w in wines:
        for r in conn.execute(_BOTTLE_SELECT + " WHERE w.id = ? AND b.status = 'in_cellar' ORDER BY v.year, s.sort",
                              (w["id"],)):
            out.append(_bottle_dict(r))
    return out


# ---------------------------------------------------------------- export

def export_json(conn, images_dir=None) -> dict:
    """Full backup. With images_dir, wine images are embedded (base64) so the file is self-contained."""
    tables = ("wine_types", "wines", "vintages", "field_meta", "storages", "lots", "bottles", "movements")
    out = {
        "format": "cellarium",
        "version": conn.execute("PRAGMA user_version").fetchone()[0],
        "exported_at": dt.datetime.now().isoformat(timespec="seconds"),
        "data": {t: [dict(r) for r in conn.execute(f"SELECT * FROM {t}")] for t in tables},
    }
    if images_dir is not None:
        import base64
        from pathlib import Path

        images = {}
        for row in out["data"]["wines"]:
            name = row.get("image")
            path = Path(images_dir) / name if name else None
            if path and path.is_file():
                images[name] = base64.b64encode(path.read_bytes()).decode()
        out["images"] = images
    return out


def import_json(conn, payload: dict, images_dir=None) -> dict:
    """Restore a full export (replaces the cellar content; keeps accounts and tokens)."""
    if payload.get("format") != "cellarium":
        raise Invalid("not a cellarium export")
    data = payload.get("data") or {}
    order = ("wine_types", "wines", "vintages", "field_meta", "storages", "lots", "bottles", "movements")
    with transaction(conn):
        for t in reversed(order):
            conn.execute(f"DELETE FROM {t}")
        for t in order:
            for row in data.get(t, []):
                cols = list(row)
                conn.execute(f"INSERT INTO {t} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                             [row[c] for c in cols])
    restored_images = 0
    if images_dir is not None:
        import base64
        from pathlib import Path

        Path(images_dir).mkdir(parents=True, exist_ok=True)  # fresh instance: no image uploaded yet
        for name, b64 in (payload.get("images") or {}).items():
            if not re.fullmatch(r"[A-Za-z0-9_.-]+\.(jpg|png|webp|gif)", name):
                continue
            (Path(images_dir) / name).write_bytes(base64.b64decode(b64))
            restored_images += 1
    counts = {t: len(data.get(t, [])) for t in order}
    counts["images"] = restored_images
    return counts
