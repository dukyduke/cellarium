"""SQLite storage: connection helper and schema migrations.

Migrations are append-only: add a new entry to MIGRATIONS, never edit an old one.
The current schema version is kept in PRAGMA user_version.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

MIGRATIONS: list[str] = [
    # 1 — initial schema
    """
    CREATE TABLE settings (
        key   TEXT PRIMARY KEY,
        value TEXT
    );

    CREATE TABLE wine_types (
        code     TEXT PRIMARY KEY,
        label_fr TEXT NOT NULL,
        label_en TEXT NOT NULL,
        color    TEXT NOT NULL,
        sort     INTEGER NOT NULL DEFAULT 100,
        builtin  INTEGER NOT NULL DEFAULT 0
    );

    INSERT INTO wine_types (code, label_fr, label_en, color, sort, builtin) VALUES
        ('red',       'Rouge',        'Red',       '#7b1e3a', 10, 1),
        ('white',     'Blanc',        'White',     '#e9d77a', 20, 1),
        ('rose',      'Rosé',         'Rosé',      '#f2a7b8', 30, 1),
        ('sparkling', 'Effervescent', 'Sparkling', '#c9d9e8', 40, 1),
        ('sweet',     'Liquoreux',    'Sweet',     '#d99a2b', 50, 1),
        ('cider',     'Cidre',        'Cider',     '#b5c95a', 60, 1);

    CREATE TABLE wines (
        id            INTEGER PRIMARY KEY,
        name          TEXT NOT NULL,
        producer      TEXT,
        appellation   TEXT,
        region        TEXT,
        country       TEXT,
        type          TEXT NOT NULL REFERENCES wine_types(code),
        grapes        TEXT,
        description   TEXT,
        serving_temp  TEXT,
        food_pairing  TEXT,
        notes         TEXT,
        image         TEXT,
        image_origin  TEXT,
        created_at    TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX wines_name ON wines(name);

    CREATE TABLE vintages (
        id            INTEGER PRIMARY KEY,
        wine_id       INTEGER NOT NULL REFERENCES wines(id) ON DELETE CASCADE,
        year          INTEGER,
        drink_from    INTEGER,
        peak_from     INTEGER,
        peak_to       INTEGER,
        drink_before  INTEGER,
        notes         TEXT
    );
    CREATE UNIQUE INDEX vintages_wine_year ON vintages(wine_id, ifnull(year, 0));

    -- Provenance of descriptive fields: who filled them and from which sources.
    CREATE TABLE field_meta (
        entity     TEXT NOT NULL,          -- 'wine' | 'vintage'
        entity_id  INTEGER NOT NULL,
        field      TEXT NOT NULL,
        origin     TEXT NOT NULL,          -- 'user' | 'import' | 'ai'
        sources    TEXT,                   -- JSON list of URLs / references
        updated_at TEXT NOT NULL DEFAULT (datetime('now')),
        PRIMARY KEY (entity, entity_id, field)
    );

    CREATE TABLE storages (
        id     INTEGER PRIMARY KEY,
        name   TEXT NOT NULL,
        kind   TEXT NOT NULL CHECK (kind IN ('rack', 'box')),
        rows   INTEGER,
        cols   INTEGER,
        sort   INTEGER NOT NULL DEFAULT 100,
        notes  TEXT
    );

    CREATE TABLE lots (
        id             INTEGER PRIMARY KEY,
        vintage_id     INTEGER NOT NULL REFERENCES vintages(id) ON DELETE CASCADE,
        purchase_date  TEXT,
        volume_cl      REAL NOT NULL DEFAULT 75,
        unit_price     REAL,
        quantity       INTEGER NOT NULL,
        supplier       TEXT,
        comment        TEXT,
        created_at     TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE bottles (
        id          INTEGER PRIMARY KEY,
        lot_id      INTEGER NOT NULL REFERENCES lots(id) ON DELETE CASCADE,
        storage_id  INTEGER REFERENCES storages(id) ON DELETE SET NULL,
        row         INTEGER,
        col         INTEGER,
        status      TEXT NOT NULL DEFAULT 'in_cellar'
                    CHECK (status IN ('in_cellar', 'drunk', 'gifted', 'lost')),
        out_date    TEXT,
        out_note    TEXT,
        rating      INTEGER
    );
    CREATE INDEX bottles_lot ON bottles(lot_id);
    CREATE UNIQUE INDEX bottles_slot ON bottles(storage_id, row, col)
        WHERE status = 'in_cellar' AND row IS NOT NULL;

    CREATE TABLE movements (
        id          INTEGER PRIMARY KEY,
        ts          TEXT NOT NULL DEFAULT (datetime('now')),
        date        TEXT NOT NULL DEFAULT (date('now')),
        kind        TEXT NOT NULL,         -- 'in' | 'out' | 'move' | 'restore'
        reason      TEXT,                  -- for 'out': drunk | gifted | lost ; for 'in': purchase | import
        wine_id     INTEGER REFERENCES wines(id) ON DELETE SET NULL,
        bottle_id   INTEGER REFERENCES bottles(id) ON DELETE SET NULL,
        count       INTEGER NOT NULL DEFAULT 1,
        label       TEXT,                  -- snapshot "Appellation — Name 2019" kept after deletion
        year        INTEGER,
        from_loc    TEXT,
        to_loc      TEXT,
        note        TEXT,
        actor       TEXT                   -- who did it ("guillaume", "guillaume via MCP (hermes)")
    );
    CREATE INDEX movements_date ON movements(date);

    -- Accounts: 'owner' can modify the cellar (several owners allowed), 'reader' is read-only.
    CREATE TABLE users (
        id             INTEGER PRIMARY KEY,
        username       TEXT NOT NULL UNIQUE COLLATE NOCASE,
        display_name   TEXT,
        password_hash  TEXT NOT NULL,
        role           TEXT NOT NULL CHECK (role IN ('owner', 'reader')),
        created_at     TEXT NOT NULL DEFAULT (datetime('now')),
        last_login_at  TEXT
    );

    CREATE TABLE sessions (
        token_hash  TEXT PRIMARY KEY,
        user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at  TEXT NOT NULL DEFAULT (datetime('now')),
        expires_at  TEXT NOT NULL
    );

    -- API / MCP tokens belong to a user; scope 'write' only works while that user is an owner.
    CREATE TABLE api_tokens (
        id            INTEGER PRIMARY KEY,
        user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name          TEXT NOT NULL,
        prefix        TEXT NOT NULL,
        token_hash    TEXT NOT NULL UNIQUE,
        scope         TEXT NOT NULL DEFAULT 'read' CHECK (scope IN ('read', 'write')),
        created_at    TEXT NOT NULL DEFAULT (datetime('now')),
        last_used_at  TEXT
    );
    """,
]


class Connection(sqlite3.Connection):
    """sqlite3 connection carrying the acting user, recorded in the movements history."""

    actor: str | None = None


def connect(db_path: Path | str, actor: str | None = None) -> Connection:
    conn = sqlite3.connect(db_path, timeout=30, isolation_level=None, check_same_thread=False, factory=Connection)
    conn.actor = actor
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def migrate(db_path: Path | str) -> int:
    conn = connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        for index, script in enumerate(MIGRATIONS[version:], start=version + 1):
            conn.execute("BEGIN")
            try:
                for statement in _split(script):
                    conn.execute(statement)
                conn.execute(f"PRAGMA user_version = {index}")
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()


def _split(script: str) -> list[str]:
    """Split a migration script into statements (no ';' inside string literals here)."""
    statements, buffer = [], []
    for line in script.splitlines():
        stripped = line.strip()
        if stripped.startswith("--"):
            continue
        buffer.append(line)
        if stripped.endswith(";"):
            statement = "\n".join(buffer).strip()
            if statement.strip(";").strip():
                statements.append(statement)
            buffer = []
    rest = "\n".join(buffer).strip()
    if rest:
        statements.append(rest)
    return statements


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Explicit transaction; nested calls join the outer one."""
    if conn.in_transaction:
        yield conn
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")
