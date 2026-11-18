"""Command line: `python -m cellarium [serve|create-user|set-password|list-users|create-token|list-tokens]`."""

from __future__ import annotations

import argparse
import getpass
import os
import sys

from . import auth
from .config import Settings
from .db import connect, migrate


def _ask_password() -> str | None:
    password = getpass.getpass("Password: ")
    if password != getpass.getpass("Repeat: "):
        print("Passwords differ.", file=sys.stderr)
        return None
    return password


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cellarium", description="Cellarium wine cellar")
    sub = parser.add_subparsers(dest="command")
    serve = sub.add_parser("serve", help="run the web server (default)")
    serve.add_argument("--host", default=os.environ.get("CELLARIUM_HOST", "127.0.0.1"))
    serve.add_argument("--port", type=int, default=int(os.environ.get("CELLARIUM_PORT", "8770")))
    serve.add_argument("--reload", action="store_true")
    cu = sub.add_parser("create-user", help="create an account")
    cu.add_argument("username")
    cu.add_argument("--role", choices=auth.ROLES, default="reader")
    cu.add_argument("--display-name")
    sp = sub.add_parser("set-password", help="set or reset a user's password")
    sp.add_argument("username")
    sub.add_parser("list-users", help="list accounts")
    tok = sub.add_parser("create-token", help="create an API/MCP token for a user")
    tok.add_argument("username")
    tok.add_argument("name")
    tok.add_argument("--scope", choices=auth.SCOPES, default="read")
    sub.add_parser("list-tokens", help="list API/MCP tokens")
    args = parser.parse_args(argv)

    if args.command in (None, "serve"):
        import uvicorn

        uvicorn.run("cellarium.app:create_app", factory=True, host=getattr(args, "host", "127.0.0.1"),
                    port=getattr(args, "port", 8770), reload=getattr(args, "reload", False),
                    proxy_headers=True, forwarded_allow_ips="*")
        return 0

    settings = Settings.from_env()
    migrate(settings.db_path)
    conn = connect(settings.db_path)
    try:
        if args.command == "create-user":
            password = _ask_password()
            if not password:
                return 1
            user = auth.create_user(conn, args.username, password, args.role, args.display_name)
            print(f"Created {user['role']} '{user['username']}'.")
        elif args.command == "set-password":
            password = _ask_password()
            if not password:
                return 1
            auth.set_password_by_name(conn, args.username, password)
            print("Password updated; the user's sessions were closed.")
        elif args.command == "list-users":
            for u in auth.list_users(conn):
                print(f"{u['id']:>3}  {u['role']:<6}  {u['username']}  last login {u['last_login_at']}")
        elif args.command == "create-token":
            row = conn.execute("SELECT id FROM users WHERE username = ?", (args.username,)).fetchone()
            if not row:
                print("Unknown user.", file=sys.stderr)
                return 1
            token = auth.create_token(conn, row["id"], args.name, args.scope)
            print(f"Token '{token['name']}' ({token['scope']}) for {token['username']} — shown once:\n{token['token']}")
        elif args.command == "list-tokens":
            for t in auth.list_tokens(conn):
                print(f"{t['id']:>3}  {t['prefix']}…  {t['scope']:<5}  {t['username']}/{t['name']}  "
                      f"last used {t['last_used_at']}")
    except auth.AuthError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
