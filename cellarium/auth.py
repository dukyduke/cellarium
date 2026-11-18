"""Accounts and authentication.

Roles:
  owner  — full access, can modify the cellar and manage accounts (several owners allowed)
  reader — read-only access (to share the cellar)

Every page and API call requires a login (browser session cookie) or an API token
(Authorization: Bearer cel_...). Tokens belong to a user and have a scope:
'read' or 'write'; a 'write' token only writes while its user is an owner.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import re
import secrets
import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass

SESSION_COOKIE = "cellarium_session"
TOKEN_PREFIX = "cel_"
ROLES = ("owner", "reader")
SCOPES = ("read", "write")


class AuthError(ValueError):
    pass


@dataclass
class Principal:
    user_id: int
    username: str
    role: str
    display_name: str | None = None
    via: str = "session"           # 'session' | 'token'
    token_name: str | None = None
    scope: str = "write"

    @property
    def can_write(self) -> bool:
        return self.role == "owner" and self.scope == "write"

    @property
    def is_owner(self) -> bool:
        return self.role == "owner"

    @property
    def actor(self) -> str:
        if self.via == "token":
            return f"{self.username} via API ({self.token_name})"
        return self.username

    def public(self) -> dict:
        return {"id": self.user_id, "username": self.username, "display_name": self.display_name,
                "role": self.role, "can_write": self.can_write}


# ---------------------------------------------------------------- passwords

def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password: str, *, n: int = 2**14, r: int = 8, p: int = 1) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=32)
    return f"scrypt${n}${r}${p}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        candidate = hashlib.scrypt(password.encode(), salt=_unb64(salt), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(candidate, _unb64(digest))
    except (ValueError, TypeError):
        return False


_DUMMY_HASH = hash_password(secrets.token_hex(8))


def _check_password_strength(password: str) -> None:
    if len(password or "") < 8:
        raise AuthError("password must be at least 8 characters")


def _sha256(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ---------------------------------------------------------------- users

def has_users(conn) -> bool:
    return conn.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None


def _user_public(row) -> dict:
    return {k: row[k] for k in ("id", "username", "display_name", "role", "created_at", "last_login_at")}


def list_users(conn) -> list[dict]:
    return [_user_public(r) for r in conn.execute("SELECT * FROM users ORDER BY role, username COLLATE NOCASE")]


def get_user(conn, user_id: int) -> dict:
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row:
        raise AuthError("user not found")
    return _user_public(row)


def create_user(conn, username: str, password: str, role: str = "reader", display_name: str | None = None) -> dict:
    username = (username or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9._@-]{2,64}", username):
        raise AuthError("username: 2-64 letters, digits or . _ @ -")
    if role not in ROLES:
        raise AuthError(f"role must be one of {ROLES}")
    _check_password_strength(password)
    try:
        cur = conn.execute("INSERT INTO users (username, display_name, password_hash, role) VALUES (?, ?, ?, ?)",
                           (username, (display_name or "").strip() or None, hash_password(password), role))
    except sqlite3.IntegrityError:
        raise AuthError("username already exists") from None
    return get_user(conn, cur.lastrowid)


def _owner_count(conn) -> int:
    return conn.execute("SELECT count(*) FROM users WHERE role = 'owner'").fetchone()[0]


def update_user(conn, user_id: int, *, role: str | None = None, display_name: str | None = None,
                password: str | None = None) -> dict:
    user = get_user(conn, user_id)
    if role is not None:
        if role not in ROLES:
            raise AuthError(f"role must be one of {ROLES}")
        if user["role"] == "owner" and role != "owner" and _owner_count(conn) <= 1:
            raise AuthError("the last owner cannot be demoted")
        conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
    if display_name is not None:
        conn.execute("UPDATE users SET display_name = ? WHERE id = ?", (display_name.strip() or None, user_id))
    if password is not None:
        _check_password_strength(password)
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(password), user_id))
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    return get_user(conn, user_id)


def delete_user(conn, user_id: int) -> None:
    user = get_user(conn, user_id)
    if user["role"] == "owner" and _owner_count(conn) <= 1:
        raise AuthError("the last owner cannot be deleted")
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))


def set_password_by_name(conn, username: str, password: str) -> dict:
    row = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    if not row:
        raise AuthError("user not found")
    return update_user(conn, row["id"], password=password)


def authenticate(conn, username: str, password: str) -> Principal | None:
    row = conn.execute("SELECT * FROM users WHERE username = ?", ((username or "").strip(),)).fetchone()
    if not row:
        verify_password(password, _DUMMY_HASH)  # same timing whether the user exists or not
        return None
    if not verify_password(password, row["password_hash"]):
        return None
    conn.execute("UPDATE users SET last_login_at = datetime('now') WHERE id = ?", (row["id"],))
    return Principal(row["id"], row["username"], row["role"], row["display_name"])


def check_user_password(conn, user_id: int, password: str) -> bool:
    row = conn.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,)).fetchone()
    return bool(row) and verify_password(password, row["password_hash"])


# ---------------------------------------------------------------- sessions

def create_session(conn, user_id: int, days: int) -> str:
    token = secrets.token_urlsafe(32)
    expires = (dt.datetime.now(dt.UTC) + dt.timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
                 (_sha256(token), user_id, expires))
    conn.execute("DELETE FROM sessions WHERE expires_at < datetime('now')")
    return token


def session_principal(conn, token: str | None) -> Principal | None:
    if not token:
        return None
    row = conn.execute(
        """SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id
            WHERE s.token_hash = ? AND s.expires_at > datetime('now')""", (_sha256(token),)).fetchone()
    if not row:
        return None
    return Principal(row["id"], row["username"], row["role"], row["display_name"])


def drop_session(conn, token: str | None) -> None:
    if token:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_sha256(token),))


# ---------------------------------------------------------------- API tokens

def create_token(conn, user_id: int, name: str, scope: str = "read") -> dict:
    name = (name or "").strip()
    if not name:
        raise AuthError("token name is required")
    if scope not in SCOPES:
        raise AuthError(f"scope must be one of {SCOPES}")
    user = get_user(conn, user_id)
    if scope == "write" and user["role"] != "owner":
        raise AuthError("only owners can create write tokens")
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    cur = conn.execute("INSERT INTO api_tokens (user_id, name, prefix, token_hash, scope) VALUES (?, ?, ?, ?, ?)",
                       (user_id, name, token[:10], _sha256(token), scope))
    return {"id": cur.lastrowid, "name": name, "token": token, "prefix": token[:10], "scope": scope,
            "username": user["username"]}


def list_tokens(conn, user_id: int | None = None) -> list[dict]:
    sql = """SELECT t.id, t.name, t.prefix, t.scope, t.created_at, t.last_used_at, t.user_id, u.username
               FROM api_tokens t JOIN users u ON u.id = t.user_id"""
    params: tuple = ()
    if user_id is not None:
        sql += " WHERE t.user_id = ?"
        params = (user_id,)
    return [dict(r) for r in conn.execute(sql + " ORDER BY t.id", params)]


def revoke_token(conn, token_id: int, user_id: int | None = None) -> bool:
    if user_id is None:
        return conn.execute("DELETE FROM api_tokens WHERE id = ?", (token_id,)).rowcount > 0
    return conn.execute("DELETE FROM api_tokens WHERE id = ? AND user_id = ?", (token_id, user_id)).rowcount > 0


def token_principal(conn, token: str | None) -> Principal | None:
    if not token or not token.startswith(TOKEN_PREFIX):
        return None
    row = conn.execute(
        """SELECT t.id AS token_id, t.name AS token_name, t.scope, u.id, u.username, u.role, u.display_name
             FROM api_tokens t JOIN users u ON u.id = t.user_id WHERE t.token_hash = ?""",
        (_sha256(token),)).fetchone()
    if not row:
        return None
    conn.execute("UPDATE api_tokens SET last_used_at = datetime('now') WHERE id = ?", (row["token_id"],))
    return Principal(row["id"], row["username"], row["role"], row["display_name"], via="token",
                     token_name=row["token_name"], scope=row["scope"])


def bearer(header: str | None) -> str | None:
    if header and header.lower().startswith("bearer "):
        return header[7:].strip()
    return None


class LoginThrottle:
    """In-memory brute-force guard: 5 failures per client in 5 minutes -> locked for 5 minutes."""

    def __init__(self, limit: int = 5, window: float = 300):
        self.limit, self.window = limit, window
        self.failures: dict[str, list[float]] = defaultdict(list)

    def blocked(self, client: str) -> bool:
        now = time.monotonic()
        self.failures[client] = [t for t in self.failures[client] if now - t < self.window]
        return len(self.failures[client]) >= self.limit

    def fail(self, client: str) -> None:
        self.failures[client].append(time.monotonic())

    def reset(self, client: str) -> None:
        self.failures.pop(client, None)
