import pytest
from fastapi.testclient import TestClient

from cellarium.app import create_app
from cellarium.config import Settings
from cellarium.db import connect, migrate

PASSWORD = "correct horse battery"
OWNER = "admin"

VITIPLACE_CSV = """\ufeffAppellation,Nom,Millésimé,Robe,Quantité
Alsace,Achillée - Gewurztraminer Hespéridé,2021,Blanc,1
Champagne,La Petite Montagne Brut - Marzilly (Domaine de),0,Effervescent,4
Coteaux du Layon,Les 3 Clochers,2012,Liquoreux,2
Pessac-Léognan,Merlet (domaine de),2015,Rouge,3
Cabernet d'Anjou,3 Monts (Domaine) - Cabernet Anjou,0,Rosé,1
"""


@pytest.fixture
def settings(tmp_path):
    return Settings.from_env(env_file=None, data_dir=tmp_path / "data", admin_password=PASSWORD)


@pytest.fixture
def conn(settings):
    migrate(settings.db_path)
    c = connect(settings.db_path)
    yield c
    c.close()


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        r = c.post("/api/login", json={"username": OWNER, "password": PASSWORD})
        assert r.status_code == 200, r.text
        c.headers["X-Cellarium"] = "1"
        yield c


@pytest.fixture
def anon(app):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def reader(app, client):
    """A logged-in read-only account (created by the owner)."""
    r = client.post("/api/users", json={"username": "ami", "password": "lecture123", "role": "reader"})
    assert r.status_code == 200, r.text
    with TestClient(app) as c:
        assert c.post("/api/login", json={"username": "ami", "password": "lecture123"}).status_code == 200
        c.headers["X-Cellarium"] = "1"
        yield c
