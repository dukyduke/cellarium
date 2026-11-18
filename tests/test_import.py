from cellarium import cellar, importers

from .conftest import VITIPLACE_CSV


def test_parse_vitiplace_csv(conn):
    parsed = importers.parse_csv(VITIPLACE_CSV.encode("utf-8"), cellar.list_types(conn))
    assert parsed["format"] == "vitiplace"
    assert parsed["missing_columns"] == []
    rows = parsed["rows"]
    assert len(rows) == 5
    assert [r["type"] for r in rows] == ["white", "sparkling", "sweet", "red", "rose"]
    assert rows[1]["year"] is None and rows[1]["quantity"] == 4
    assert all(not r["errors"] for r in rows)


def test_parse_generic_semicolon_cp1252(conn):
    text = "Nom;Appellation;Année;Couleur;Qté;Prix;Volume;Date d'achat\nCuvée X;Saumur;2019;Rouge;2;12,50;1,5 L;10/05/2023\nY;;2020;Cidre;1;;;\nZ;;2020;Orange;1;;;\n"
    parsed = importers.parse_csv(text.encode("cp1252"), cellar.list_types(conn))
    a, b, c = parsed["rows"]
    assert a["volume_cl"] == 150 and a["unit_price"] == 12.5 and a["purchase_date"] == "2023-05-10"
    assert b["type"] == "cider"
    assert c["errors"]


def test_commit_and_reimport_skips(conn):
    parsed = importers.parse_csv(VITIPLACE_CSV, cellar.list_types(conn))
    rep = importers.commit_rows(conn, parsed["rows"])
    assert rep["wines_created"] == 5 and rep["bottles_added"] == 11
    s = cellar.summary(conn)
    assert s["by_type"] == {"red": 3, "white": 1, "rose": 1, "sparkling": 4, "sweet": 2, "cider": 0}
    rep2 = importers.commit_rows(conn, parsed["rows"])
    assert rep2["bottles_added"] == 0 and len(rep2["skipped"]) == 5
    rep3 = importers.commit_rows(conn, parsed["rows"], mode="replace")
    assert rep3["bottles_added"] == 11
    assert cellar.summary(conn)["total"] == 11
    hist = cellar.history(conn)
    assert all(m["reason"] == "import" for m in hist)


def test_commit_revalidates_client_rows(conn):
    rows = [{"line": 2, "name": "Hacked", "type": "nope", "quantity": 3}]
    rep = importers.commit_rows(conn, rows)
    assert rep["errors"] and rep["bottles_added"] == 0


def test_export_csv_reimportable(conn):
    importers.commit_rows(conn, importers.parse_csv(VITIPLACE_CSV, cellar.list_types(conn))["rows"])
    out = importers.export_csv(conn)
    parsed = importers.parse_csv(out, cellar.list_types(conn))
    assert len(parsed["rows"]) == 5 and not any(r["errors"] for r in parsed["rows"])
    assert sum(r["quantity"] for r in parsed["rows"]) == 11


def test_real_vitiplace_sample_shape():
    """The real export header (BOM + 'Millésimé') is recognised as Vitiplace."""
    head = "\ufeffAppellation,Nom,Millésimé,Robe,Quantité\nHaut-Médoc, Picourneau (château),2015,Rouge,1\n"
    parsed = importers.parse_csv(head.encode())
    assert parsed["format"] == "vitiplace"
    assert parsed["rows"][0]["name"] == "Picourneau (château)"
