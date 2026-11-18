"""CSV import (Vitiplace export and generic spreadsheets).

Flow: parse_csv() -> preview rows with per-row errors -> commit_rows().
The Vitiplace "liste de cave" export has the header:
    Appellation,Nom,Millésimé,Robe,Quantité
Other columns are recognised by name (French or English, accents ignored).
"""

from __future__ import annotations

import csv
import io
import re
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from typing import Any

from . import cellar
from .db import transaction

COLUMN_ALIASES = {
    "appellation": ["appellation", "aoc", "aop"],
    "name": ["nom", "name", "vin", "wine", "nom du vin", "cuvee"],
    "year": ["millesime", "millesimee", "millesimé", "vintage", "annee", "year"],
    "type": ["robe", "type", "couleur", "color", "colour"],
    "quantity": ["quantite", "quantity", "qte", "qty", "stock", "nombre", "nb"],
    "volume_cl": ["volume", "vol", "volume cl", "contenance", "size", "format"],
    "unit_price": ["prix", "price", "prix unitaire", "p u", "pu", "unit price", "prix d achat"],
    "purchase_date": ["date", "date d achat", "purchase date", "achat"],
    "region": ["region"],
    "country": ["pays", "country"],
    "producer": ["producteur", "producer", "domaine", "chateau"],
    "grapes": ["cepages", "cepage", "grapes", "varietal", "variete"],
    "supplier": ["fournisseur", "supplier", "caviste"],
    "comment": ["commentaire", "comment", "comments", "note", "notes"],
    "location": ["emplacement", "location", "rangement"],
}

TYPE_ALIASES = {
    "red": ["rouge", "red", "vin rouge", "tinto", "rosso"],
    "white": ["blanc", "white", "vin blanc", "blanco", "bianco"],
    "rose": ["rose", "vin rose", "rosado", "rosato"],
    "sparkling": ["effervescent", "sparkling", "petillant", "mousseux", "champagne", "cremant", "vin mousseux"],
    "sweet": ["liquoreux", "moelleux", "doux", "sweet", "vin doux", "vin doux naturel", "dessert"],
    "cider": ["cidre", "cider", "poire", "poire cidre"],
}


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9]+", " ", text.lower())
    return text.strip()


_COLUMN_LOOKUP = {_norm(alias): key for key, aliases in COLUMN_ALIASES.items() for alias in aliases}


def map_type(value: str | None, extra_types: list[dict] | None = None) -> str | None:
    n = _norm(value or "")
    if not n:
        return None
    for code, aliases in TYPE_ALIASES.items():
        if n in {_norm(a) for a in aliases}:
            return code
    for t in extra_types or []:
        if n in {_norm(t["code"]), _norm(t["label_fr"]), _norm(t["label_en"])}:
            return t["code"]
    return None


def parse_volume(value: Any) -> float | None:
    text = str(value or "").strip().lower().replace(",", ".")
    if not text:
        return None
    m = re.match(r"([\d.]+)\s*(cl|l|ml)?", text)
    if not m:
        return None
    number = float(m.group(1))
    unit = m.group(2)
    if unit == "l" or (unit is None and number < 10):
        return round(number * 100, 1)
    if unit == "ml":
        return round(number / 10, 1)
    return number


def decode(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def read_xlsx(raw: bytes) -> list[list[str]]:
    """First worksheet of an .xlsx file as rows of strings (stdlib only: zip + XML)."""
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    try:
        book = zipfile.ZipFile(io.BytesIO(raw))
        shared = []
        if "xl/sharedStrings.xml" in book.namelist():
            for si in ET.fromstring(book.read("xl/sharedStrings.xml")).findall("m:si", ns):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{ns['m']}}}t")))
        sheets = sorted(n for n in book.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n))
        sheet = ET.fromstring(book.read(sheets[0]))
    except (zipfile.BadZipFile, KeyError, IndexError, ET.ParseError) as exc:
        raise cellar.Invalid(f"unreadable xlsx file: {exc}") from None
    rows = []
    for row in sheet.iter(f"{{{ns['m']}}}row"):
        cells: dict[int, str] = {}
        for c in row.findall("m:c", ns):
            ref = re.match(r"([A-Z]+)", c.get("r", "A"))
            col = 0
            for ch in ref.group(1) if ref else "A":
                col = col * 26 + (ord(ch) - 64)
            kind, v = c.get("t"), c.find("m:v", ns)
            if kind == "s" and v is not None:
                value = shared[int(v.text)]
            elif kind == "inlineStr":
                value = "".join(t.text or "" for t in c.iter(f"{{{ns['m']}}}t"))
            else:
                value = v.text if v is not None and v.text else ""
                if re.fullmatch(r"-?\d+\.0", value):
                    value = value[:-2]
            cells[col - 1] = value
        if cells:
            rows.append([cells.get(i, "") for i in range(max(cells) + 1)])
    return rows


def parse_file(raw: bytes, filename: str = "", extra_types: list[dict] | None = None) -> dict:
    if filename.lower().endswith(".xlsx") or raw[:2] == b"PK":
        return parse_rows(read_xlsx(raw), extra_types)
    return parse_csv(raw, extra_types)


def parse_csv(raw: bytes | str, extra_types: list[dict] | None = None) -> dict:
    text = decode(raw) if isinstance(raw, bytes) else raw.lstrip("\ufeff")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    return parse_rows(list(csv.reader(io.StringIO(text), dialect)), extra_types)


def parse_rows(rows: list[list[str]], extra_types: list[dict] | None = None) -> dict:
    if not rows:
        return {"format": "empty", "columns": {}, "rows": [], "unknown_columns": []}
    header = [h.lstrip("\ufeff") for h in rows[0]]
    mapping: dict[int, str] = {}
    unknown = []
    for i, h in enumerate(header):
        key = _COLUMN_LOOKUP.get(_norm(h))
        if key and key not in mapping.values():
            mapping[i] = key
        else:
            unknown.append(h)
    fmt = "vitiplace" if [_norm(h) for h in header[:5]] == ["appellation", "nom", "millesime", "robe", "quantite"] else "csv"
    out = []
    for line_no, cells in enumerate(rows[1:], start=2):
        if not any(c.strip() for c in cells):
            continue
        record = {key: (cells[i].strip() if i < len(cells) else "") for i, key in mapping.items()}
        out.append(_validate_row(record, line_no, extra_types))
    missing = [k for k in ("name", "type") if k not in mapping.values()]
    return {"format": fmt, "columns": {header[i]: k for i, k in mapping.items()}, "unknown_columns": unknown,
            "missing_columns": missing, "rows": out}


def _validate_row(record: dict, line_no: int, extra_types) -> dict:
    errors, warnings = [], []
    row: dict[str, Any] = {"line": line_no, "source": record}
    row["name"] = (record.get("name") or "").strip()
    if not row["name"]:
        errors.append("missing name")
    row["appellation"] = (record.get("appellation") or "").strip() or None
    row["type"] = map_type(record.get("type"), extra_types)
    if not row["type"]:
        errors.append(f"unknown type '{record.get('type', '')}'")
    try:
        row["year"] = cellar._year_or_none(record.get("year"))
    except cellar.CellarError as exc:
        row["year"] = None
        errors.append(exc.message)
    qty_text = (record.get("quantity") or "").strip()
    try:
        row["quantity"] = int(float(qty_text.replace(",", "."))) if qty_text else 1
        if row["quantity"] < 0:
            raise ValueError
    except ValueError:
        row["quantity"] = 0
        errors.append(f"invalid quantity '{qty_text}'")
    row["volume_cl"] = parse_volume(record.get("volume_cl")) or 75
    price_text = (record.get("unit_price") or "").replace("€", "").strip()
    try:
        row["unit_price"] = float(price_text.replace(",", ".")) if price_text else None
    except ValueError:
        row["unit_price"] = None
        warnings.append(f"ignored price '{price_text}'")
    try:
        row["purchase_date"] = cellar._date_or_none(record.get("purchase_date"))
    except cellar.CellarError:
        row["purchase_date"] = None
        warnings.append(f"ignored date '{record.get('purchase_date')}'")
    for key in ("region", "country", "producer", "grapes", "supplier", "comment", "location"):
        row[key] = (record.get(key) or "").strip() or None
    if row["quantity"] == 0 and not errors:
        warnings.append("quantity 0: wine sheet created without bottles")
    row["errors"], row["warnings"] = errors, warnings
    return row


def _as_record(row: dict) -> dict:
    """Rows coming back from the client are always re-validated (they may have been edited)."""
    record = {}
    for key in COLUMN_ALIASES:
        value = row.get(key)
        record[key] = "" if value is None else str(value)
    return record


def _find_wine(conn, row: dict) -> int | None:
    found = conn.execute(
        """SELECT id FROM wines WHERE lower(name) = lower(?) AND lower(ifnull(appellation, '')) = lower(?) AND type = ?""",
        (row["name"], row["appellation"] or "", row["type"])).fetchone()
    return found["id"] if found else None


def commit_rows(conn, rows: list[dict], mode: str = "skip_existing", storage_id: int | None = None) -> dict:
    """Import validated rows.

    mode:
      skip_existing  — a wine+vintage already holding bottles is skipped (safe re-import)
      add            — always add the bottles (new purchase lot)
      replace        — wipe all wines/bottles/history first, then import
    """
    if mode not in ("skip_existing", "add", "replace"):
        raise cellar.Invalid("mode must be skip_existing, add or replace")
    extra_types = cellar.list_types(conn)
    report = {"wines_created": 0, "bottles_added": 0, "skipped": [], "errors": []}
    with transaction(conn):
        if mode == "replace":
            for table in ("movements", "bottles", "lots", "vintages", "field_meta", "wines"):
                conn.execute(f"DELETE FROM {table}")
        for raw in rows:
            row = _validate_row(_as_record(raw), raw.get("line", 0), extra_types)
            if row["errors"]:
                report["errors"].append({"line": row["line"], "errors": row["errors"]})
                continue
            wine_id = _find_wine(conn, row)
            if wine_id is None:
                data = {k: row[k] for k in ("name", "appellation", "type", "region", "country", "producer", "grapes")
                        if row.get(k)}
                wine_id = cellar.create_wine(conn, data, origin="import")
                report["wines_created"] += 1
            else:
                fill = {k: row[k] for k in ("region", "country", "producer", "grapes") if row.get(k)}
                if fill:
                    cellar.update_wine(conn, wine_id, fill, origin="import", protect_user=True)
            vintage_id = cellar.get_or_create_vintage(conn, wine_id, row["year"])
            if row["quantity"] <= 0:
                continue
            if mode == "skip_existing":
                existing = conn.execute("""SELECT count(*) FROM bottles b JOIN lots l ON l.id = b.lot_id
                                            WHERE l.vintage_id = ? AND b.status = 'in_cellar'""",
                                        (vintage_id,)).fetchone()[0]
                if existing:
                    report["skipped"].append({"line": row["line"], "reason": f"already {existing} bottle(s) in cellar"})
                    continue
            result = cellar.add_bottles(
                conn, wine_id, year=row["year"], quantity=row["quantity"], volume_cl=row["volume_cl"],
                unit_price=row["unit_price"], purchase_date=row["purchase_date"], supplier=row["supplier"],
                comment=row["comment"], storage_id=storage_id, reason="import")
            report["bottles_added"] += len(result["bottle_ids"])
    return report


def export_csv(conn) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    labels = {t["code"]: t["label_fr"] for t in cellar.list_types(conn)}
    writer.writerow(["Appellation", "Nom", "Millésime", "Type", "Volume (cl)", "Prix", "Quantité", "Région",
                     "Producteur", "Emplacements"])
    plan = cellar.plan(conn)
    where: dict[tuple, list[str]] = {}
    for b in [b for s in plan["storages"] for b in s["bottles"]] + plan["unplaced"]:
        where.setdefault((b["vintage_id"], b["volume_cl"]), []).append(cellar.location_label(b["location"]) or "-")
    for r in cellar.cellar_rows(conn):
        locs = sorted(set(where.get((r["vintage_id"], r["volume_cl"]), [])))
        writer.writerow([r["appellation"] or "", r["name"], r["year"] or 0, labels.get(r["type"], r["type"]),
                         f"{r['volume_cl']:g}", "" if r["price"] is None else f"{r['price']:.2f}", r["stock"],
                         r["region"] or "", r["producer"] or "", " ; ".join(locs)])
    return buffer.getvalue()
