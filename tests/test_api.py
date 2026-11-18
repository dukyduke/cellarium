import base64

import httpx

from .conftest import OWNER, PASSWORD, VITIPLACE_CSV

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


def test_requires_auth(anon):
    assert anon.get("/api/summary").status_code == 401
    info = anon.get("/api/session").json()
    assert info["authenticated"] is False and info["setup_required"] is False
    assert anon.post("/api/login", json={"username": OWNER, "password": "nope"}).status_code == 401
    assert anon.post("/api/login", json={"username": "ghost", "password": PASSWORD}).status_code == 401
    assert anon.get("/api/plan").status_code == 401
    assert anon.get("/api/export.json").status_code == 401


def test_login_throttle(anon):
    for _ in range(5):
        anon.post("/api/login", json={"username": OWNER, "password": "wrong"})
    assert anon.post("/api/login", json={"username": OWNER, "password": PASSWORD}).status_code == 429


def test_csrf_header_required_for_cookie_writes(client):
    r = client.post("/api/storages", json={"name": "X", "kind": "box"}, headers={"X-Cellarium": ""})
    assert r.status_code == 403


def test_index_and_static(anon):
    assert anon.get("/").status_code == 200
    assert anon.get("/static/app.js").status_code == 200
    assert anon.get("/static/i18n/fr.json").status_code == 200


def test_full_flow(client):
    rack = client.post("/api/storages", json={"name": "Principale", "kind": "rack", "rows": 6, "cols": 12}).json()["id"]
    box = client.post("/api/storages", json={"name": "Carton 1", "kind": "box"}).json()["id"]
    wine = client.post("/api/wines", json={"name": "Achillée - Gewurztraminer Hespéridé", "appellation": "Alsace",
                                           "region": "Alsace", "type": "white"}).json()
    wid = wine["id"]
    r = client.post(f"/api/wines/{wid}/bottles", json={"year": 2021, "quantity": 1, "unit_price": 24.5,
                                                       "purchase_date": "10/05/2023", "comment": "Vin stocké",
                                                       "storage_id": rack, "positions": [{"row": 2, "col": 5}]})
    assert r.status_code == 200, r.text
    bottle = r.json()["bottle_ids"][0]
    client.post(f"/api/wines/{wid}/bottles", json={"year": 2021, "quantity": 2, "storage_id": box})
    detail = client.get(f"/api/wines/{wid}").json()
    assert detail["stock"] == 3
    assert detail["lots"][0]["purchase_date"] in ("2023-05-10", None)
    vid = detail["vintages"][0]["id"]
    assert client.patch(f"/api/vintages/{vid}", json={"drink_from": 2023, "peak_from": 2026,
                                                      "drink_before": 2029}).status_code == 200
    rows = client.get("/api/cellar").json()
    assert rows[0]["stock"] == 3 and rows[0]["phase"] in ("drink", "peak", "decline")
    plan = client.get("/api/plan").json()
    cell = plan["storages"][0]["bottles"][0]["location"]
    assert (cell["row"], cell["col"]) == (2, 5)
    assert client.post(f"/api/bottles/{bottle}/move", json={"storage_id": rack, "row": 1, "col": 1}).status_code == 200
    assert client.post(f"/api/bottles/{bottle}/out", json={"status": "drunk", "note": "très bon"}).status_code == 200
    hist = client.get("/api/history").json()
    assert [h["kind"] for h in hist][:2] == ["out", "move"]
    summary = client.get("/api/summary").json()
    assert summary["total"] == 2 and summary["by_type"]["white"] == 2
    sched = client.get("/api/schedule").json()
    assert sum(len(v) for v in sched["phases"].values()) == 1
    assert client.patch(f"/api/storages/{rack}", json={"rows": 0}).status_code == 400
    assert client.delete(f"/api/wines/{wid}").status_code == 200
    assert client.get(f"/api/wines/{wid}").status_code == 404


def test_image_upload_and_media(client):
    wid = client.post("/api/wines", json={"name": "Test", "type": "red"}).json()["id"]
    r = client.post(f"/api/wines/{wid}/image", files={"file": ("x.png", PNG, "image/png")})
    assert r.status_code == 200, r.text
    name = r.json()["image"]
    assert client.get(f"/api/media/{name}").content == PNG
    bad = client.post(f"/api/wines/{wid}/image", files={"file": ("x.txt", b"hello", "text/plain")})
    assert bad.status_code == 400
    assert client.get("/api/media/..%2Fcellarium.db").status_code == 404


def test_image_generation(app, client):
    wid = client.post("/api/wines", json={"name": "Test", "appellation": "Pomerol", "type": "red"}).json()["id"]
    assert client.post(f"/api/wines/{wid}/image/generate").status_code == 502  # not configured
    settings = app.state.settings
    settings.image_api_key = "sk-test"
    seen = {}

    def handler(request: httpx.Request):
        seen["auth"] = request.headers["authorization"]
        seen["body"] = request.read().decode()
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(PNG).decode()}]})

    app.state.image_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    r = client.post(f"/api/wines/{wid}/image/generate")
    assert r.status_code == 200, r.text
    assert r.json()["image_origin"] == "ai"
    assert seen["auth"] == "Bearer sk-test" and "Pomerol" in seen["body"]
    assert client.get("/api/session").json()["image_generation"] is True


def test_import_endpoints(client):
    prev = client.post("/api/import/preview", files={"file": ("liste_cave.csv", VITIPLACE_CSV.encode(), "text/csv")})
    assert prev.status_code == 200
    data = prev.json()
    assert data["format"] == "vitiplace" and len(data["rows"]) == 5
    rep = client.post("/api/import/commit", json={"rows": data["rows"]}).json()
    assert rep["bottles_added"] == 11
    csv = client.get("/api/export.csv")
    assert csv.status_code == 200 and "Merlet" in csv.text
    dump = client.get("/api/export.json").json()
    assert dump["format"] == "cellarium"


def test_tokens_and_bearer(client, anon):
    created = client.post("/api/tokens", json={"name": "hermes", "scope": "write"}).json()
    token = created["token"]
    assert token.startswith("cel_") and created["scope"] == "write"
    r = anon.get("/api/summary", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    # bearer writes need no CSRF header
    r = anon.post("/api/storages", json={"name": "B", "kind": "box"}, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    hist_actor = client.post("/api/wines", json={"name": "W", "type": "red"}).json()["id"]
    anon.post(f"/api/wines/{hist_actor}/bottles", json={"quantity": 1}, headers={"Authorization": f"Bearer {token}"})
    assert client.get("/api/history").json()[0]["actor"] == "admin via API (hermes)"
    listed = client.get("/api/tokens").json()
    assert listed[0]["last_used_at"] is not None and "token" not in listed[0]
    # read token: GET ok, writes refused
    ro = client.post("/api/tokens", json={"name": "lecture"}).json()["token"]
    assert anon.get("/api/plan", headers={"Authorization": f"Bearer {ro}"}).status_code == 200
    assert anon.post("/api/storages", json={"name": "C", "kind": "box"},
                     headers={"Authorization": f"Bearer {ro}"}).status_code == 403
    client.delete(f"/api/tokens/{created['id']}")
    assert anon.get("/api/summary", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_reader_is_read_only(client, reader):
    wid = client.post("/api/wines", json={"name": "W", "type": "red"}).json()["id"]
    client.post(f"/api/wines/{wid}/bottles", json={"quantity": 2})
    me = reader.get("/api/session").json()["user"]
    assert me["role"] == "reader" and me["can_write"] is False
    assert reader.get("/api/summary").json()["total"] == 2
    assert reader.get(f"/api/wines/{wid}").status_code == 200
    assert reader.get("/api/plan").status_code == 200
    assert reader.post("/api/wines", json={"name": "X", "type": "red"}).status_code == 403
    assert reader.patch(f"/api/wines/{wid}", json={"name": "Y"}).status_code == 403
    assert reader.post(f"/api/wines/{wid}/bottles", json={"quantity": 1}).status_code == 403
    assert reader.post("/api/import/commit", json={"rows": []}).status_code == 403
    assert reader.get("/api/users").status_code == 403
    assert reader.post("/api/users", json={"username": "x", "password": "12345678"}).status_code == 403
    # a reader can manage its own read token and password
    assert reader.post("/api/tokens", json={"name": "mine", "scope": "write"}).status_code == 400
    tok = reader.post("/api/tokens", json={"name": "mine"})
    assert tok.status_code == 200
    assert reader.post("/api/password", json={"current": "lecture123", "password": "lecture456"}).status_code == 200


def test_users_management(client):
    owner2 = client.post("/api/users", json={"username": "claire", "password": "proprio123", "role": "owner"}).json()
    users = client.get("/api/users").json()
    assert {u["username"] for u in users} == {"admin", "claire"}
    me = client.get("/api/me").json()
    assert client.delete(f"/api/users/{me['id']}").status_code == 400
    assert client.post("/api/users", json={"username": "claire", "password": "proprio123"}).status_code == 400
    assert client.patch(f"/api/users/{owner2['id']}", json={"role": "reader"}).status_code == 200
    # the last owner can be neither demoted nor deleted
    assert client.patch(f"/api/users/{me['id']}", json={"role": "reader"}).status_code == 400
    assert client.delete(f"/api/users/{owner2['id']}").status_code == 200


def test_password_change(client):
    assert client.post("/api/password", json={"current": "bad", "password": "abcdefgh1"}).status_code == 403
    assert client.post("/api/password", json={"current": PASSWORD, "password": "abcdefgh1"}).status_code == 200
    assert client.get("/api/summary").status_code == 200  # new session cookie issued


def test_first_run_setup(tmp_path):
    from fastapi.testclient import TestClient

    from cellarium.app import create_app
    from cellarium.config import Settings

    settings = Settings.from_env(env_file=None, data_dir=tmp_path / "fresh", admin_password=None)
    app = create_app(settings)
    with TestClient(app) as c:
        assert c.get("/api/session").json()["setup_required"] is True
        body = {"username": "guillaume", "password": "abcdefgh", "code": "wrong"}
        assert c.post("/api/setup", json=body).status_code == 403
        body["code"] = app.state.setup_code
        assert c.post("/api/setup", json=body).status_code == 200
        assert c.get("/api/summary").status_code == 200
        assert c.get("/api/me").json()["role"] == "owner"
        assert c.post("/api/setup", json=body).status_code == 409
