import pytest

from cellarium import cellar


def make_wine(conn, **kw):
    data = {"name": "Merlet (domaine de)", "appellation": "Pessac-Léognan", "type": "red", **kw}
    return cellar.create_wine(conn, data)


def test_types_include_cider(conn):
    codes = [t["code"] for t in cellar.list_types(conn)]
    assert codes == ["red", "white", "rose", "sparkling", "sweet", "cider"]


@pytest.mark.parametrize("window,year,expected", [
    ({}, 2026, "unknown"),
    ({"drink_from": 2028}, 2026, "keep"),
    ({"drink_from": 2023, "peak_from": 2026, "drink_before": 2029}, 2024, "drink"),
    ({"drink_from": 2023, "peak_from": 2026, "drink_before": 2029}, 2026, "peak"),
    ({"drink_from": 2023, "peak_from": 2026, "drink_before": 2029}, 2027, "decline"),
    ({"drink_from": 2023, "peak_from": 2026, "drink_before": 2029}, 2031, "decline"),
    ({"peak_from": 2030, "peak_to": 2035}, 2026, "keep"),
    ({"drink_from": 2020, "drink_before": 2030}, 2026, "drink"),
    ({"drink_before": 2024}, 2026, "decline"),
    ({"drink_from": 2020, "peak_from": 2024, "peak_to": 2028}, 2027, "peak"),
])
def test_phase_for(window, year, expected):
    assert cellar.phase_for(window, year) == expected


def test_overdue():
    assert cellar.is_overdue({"drink_before": 2025}, 2026)
    assert not cellar.is_overdue({"drink_before": 2026}, 2026)


def test_add_bottles_fills_rack_in_order(conn):
    wid = make_wine(conn)
    rack = cellar.create_storage(conn, {"name": "Principale", "kind": "rack", "rows": 2, "cols": 2})
    res = cellar.add_bottles(conn, wid, year=2015, quantity=3, unit_price=30, storage_id=rack)
    assert res["placed"] == 3 and res["unplaced"] == 0
    plan = cellar.plan(conn)
    slots = sorted((b["location"]["row"], b["location"]["col"]) for b in plan["storages"][0]["bottles"])
    assert slots == [(1, 1), (1, 2), (2, 1)]
    # rack full after one more; the extra one is unplaced
    res = cellar.add_bottles(conn, wid, year=2015, quantity=2, storage_id=rack)
    assert res["placed"] == 1 and res["unplaced"] == 1


def test_box_and_unplaced(conn):
    wid = make_wine(conn, type="cider", name="Cidre bouché", appellation="Pays d'Auge")
    box = cellar.create_storage(conn, {"name": "Carton cidres", "kind": "box"})
    cellar.add_bottles(conn, wid, quantity=6, storage_id=box)
    cellar.add_bottles(conn, wid, quantity=2)
    plan = cellar.plan(conn)
    assert len(plan["storages"][0]["bottles"]) == 6
    assert all(b["location"]["row"] is None for b in plan["storages"][0]["bottles"])
    assert len(plan["unplaced"]) == 2
    s = cellar.summary(conn)
    assert s["by_type"]["cider"] == 8 and s["unplaced"] == 2


def test_cellar_rows_and_summary(conn):
    wid = make_wine(conn)
    cellar.add_bottles(conn, wid, year=2015, quantity=2, unit_price=20)
    cellar.add_bottles(conn, wid, year=2015, quantity=1, unit_price=26)
    cellar.add_bottles(conn, wid, year=2015, quantity=1, volume_cl=150, unit_price=60)
    rows = cellar.cellar_rows(conn)
    assert [(r["year"], r["volume_cl"], r["stock"]) for r in rows] == [(2015, 75, 3), (2015, 150, 1)]
    assert rows[0]["price"] == pytest.approx(22.0)
    s = cellar.summary(conn)
    assert s["total"] == 4 and s["by_type"]["red"] == 4 and s["value"] == 126


def test_remove_restore_and_history(conn):
    wid = make_wine(conn)
    rack = cellar.create_storage(conn, {"name": "R", "kind": "rack", "rows": 1, "cols": 3})
    ids = cellar.add_bottles(conn, wid, year=2015, quantity=2, storage_id=rack)["bottle_ids"]
    b = cellar.remove_bottle(conn, ids[0], "drunk", "2026-11-01", "anniversaire", 4)
    assert b["status"] == "drunk"
    with pytest.raises(cellar.Conflict):
        cellar.remove_bottle(conn, ids[0])
    assert cellar.summary(conn)["total"] == 1
    kinds = [m["kind"] for m in cellar.history(conn)]
    assert kinds == ["in", "out"] or sorted(kinds) == ["in", "out"]
    restored = cellar.restore_bottle(conn, ids[0])
    assert restored["status"] == "in_cellar" and restored["location"]["row"] == 1


def test_move_conflict_and_swap(conn):
    wid = make_wine(conn)
    rack = cellar.create_storage(conn, {"name": "R", "kind": "rack", "rows": 2, "cols": 2})
    a, b = cellar.add_bottles(conn, wid, year=2015, quantity=2, storage_id=rack)["bottle_ids"]
    with pytest.raises(cellar.Conflict):
        cellar.move_bottle(conn, a, rack, 1, 2)
    moved = cellar.move_bottle(conn, a, rack, 1, 2, swap=True)
    assert moved["location"]["col"] == 2 and moved["swapped_with"] == b
    assert cellar.get_bottle(conn, b)["location"]["col"] == 1
    with pytest.raises(cellar.Invalid):
        cellar.move_bottle(conn, a, rack, 3, 1)
    assert cellar.move_bottle(conn, a, None)["location"] is None


def test_resize_and_delete_storage(conn):
    wid = make_wine(conn)
    rack = cellar.create_storage(conn, {"name": "R", "kind": "rack", "rows": 2, "cols": 3})
    ids = cellar.add_bottles(conn, wid, quantity=6, storage_id=rack)["bottle_ids"]
    with pytest.raises(cellar.Conflict) as err:
        cellar.update_storage(conn, rack, {"rows": 1})
    assert len(err.value.details["bottles"]) == 3
    res = cellar.update_storage(conn, rack, {"rows": 1}, unplace_overflow=True)
    assert len(res["unplaced"]) == 3
    res = cellar.delete_storage(conn, rack)
    assert len(res["unplaced"]) == 3
    assert len(cellar.plan(conn)["unplaced"]) == 6
    assert set(ids) == {b["id"] for b in cellar.plan(conn)["unplaced"]}


def test_ai_cannot_overwrite_user_fields(conn):
    wid = make_wine(conn, region="Bordeaux")
    res = cellar.update_wine(conn, wid, {"region": "Graves", "grapes": "Merlot, Cabernet sauvignon"},
                             origin="ai", sources=["https://example.org"], protect_user=True)
    assert res["updated"] == ["grapes"]
    assert res["skipped"][0]["field"] == "region"
    wine = cellar.get_wine(conn, wid)
    assert wine["region"] == "Bordeaux"
    assert wine["meta"]["grapes"] == {"origin": "ai", "sources": ["https://example.org"],
                                      "updated_at": wine["meta"]["grapes"]["updated_at"]}
    # AI may refine its own previous value
    res = cellar.update_wine(conn, wid, {"grapes": "Merlot"}, origin="ai", sources=["https://example.org/2"],
                             protect_user=True)
    assert res["updated"] == ["grapes"]


def test_vintage_window_validation(conn):
    wid = make_wine(conn)
    vid = cellar.get_or_create_vintage(conn, wid, 2015)
    with pytest.raises(cellar.Invalid):
        cellar.update_vintage(conn, vid, {"drink_from": 2030, "drink_before": 2025})
    with pytest.raises(cellar.Invalid):
        cellar.update_vintage(conn, vid, {"drink_from": 2010})
    res = cellar.update_vintage(conn, vid, {"drink_from": 2020, "peak_from": 2025, "peak_to": 2030, "drink_before": 2035})
    assert len(res["updated"]) == 4


def test_non_vintage_is_unique(conn):
    wid = make_wine(conn, type="sparkling", name="Brut", appellation="Champagne")
    assert cellar.get_or_create_vintage(conn, wid, 0) == cellar.get_or_create_vintage(conn, wid, None)
    assert cellar.get_or_create_vintage(conn, wid, "NV") == cellar.get_or_create_vintage(conn, wid, "")


def test_export_import_json_roundtrip(conn):
    wid = make_wine(conn)
    rack = cellar.create_storage(conn, {"name": "R", "kind": "rack", "rows": 2, "cols": 2})
    cellar.add_bottles(conn, wid, year=2015, quantity=2, storage_id=rack)
    dump = cellar.export_json(conn)
    cellar.delete_wine(conn, wid)
    assert cellar.summary(conn)["total"] == 0
    cellar.import_json(conn, dump)
    assert cellar.summary(conn)["total"] == 2
