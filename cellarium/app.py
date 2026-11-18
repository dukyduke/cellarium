"""Application factory: FastAPI (web UI + REST API) and the MCP endpoint behind one ASGI app."""

from __future__ import annotations

import logging
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from mcp.server.transport_security import TransportSecuritySettings
from starlette.types import ASGIApp, Receive, Scope, Send

from . import __version__, api, auth, cellar
from .config import STATIC_DIR, Settings
from .db import connect, migrate
from .mcp_server import build_mcp

log = logging.getLogger("cellarium")
MCP_PATH = "/mcp"


def _bootstrap(settings: Settings) -> str | None:
    """Apply migrations and create the first owner. Returns a setup code when no account exists."""
    migrate(settings.db_path)
    conn = connect(settings.db_path)
    try:
        if settings.admin_password and not auth.has_users(conn):
            auth.create_user(conn, settings.admin_user, settings.admin_password, "owner")
            log.warning("Owner account '%s' created from CELLARIUM_ADMIN_PASSWORD.", settings.admin_user)
        if auth.has_users(conn):
            return None
    finally:
        conn.close()
    code = secrets.token_hex(4)
    log.warning("No account yet. Open the web UI and use this setup code: %s", code)
    print(f"[cellarium] First start: setup code = {code}", flush=True)
    return code


def create_app(settings: Settings | None = None) -> "McpGateway":
    settings = settings or Settings.from_env()
    setup_code = _bootstrap(settings)
    mcp = build_mcp(settings)
    gateway: McpGateway

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # A fresh Streamable HTTP app per startup: its session manager can only run once.
        gateway.mcp_app = mcp.streamable_http_app(
            streamable_http_path=MCP_PATH,
            # Bearer tokens already protect the endpoint; Host checks would break reverse proxies.
            transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
            stateless_http=True,
            json_response=True,
        )
        async with mcp.session_manager.run():
            yield

    app = FastAPI(title="Cellarium", version=__version__, lifespan=lifespan,
                  docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None)
    app.state.settings = settings
    app.state.setup_code = setup_code
    app.state.throttle = auth.LoginThrottle()
    app.state.mcp = mcp

    @app.exception_handler(cellar.CellarError)
    async def cellar_error(_request: Request, exc: cellar.CellarError):
        return JSONResponse({"detail": exc.message, **exc.details}, status_code=exc.status)

    @app.exception_handler(ValueError)
    async def value_error(_request: Request, exc: ValueError):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    for router in (api.public, api.account, api.owners, api.router):
        app.include_router(router)

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return {"ok": True, "version": __version__}

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    gateway = McpGateway(app, settings)
    return gateway


class McpGateway:
    """Routes /mcp to the MCP app after checking the bearer token; everything else goes to FastAPI."""

    def __init__(self, web: FastAPI, settings: Settings):
        self.web, self.settings = web, settings
        self.mcp_app: ASGIApp | None = None
        self.state = web.state  # convenience for tests

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if scope["type"] == "http" and (path == MCP_PATH or path.startswith(MCP_PATH + "/")):
            if path != MCP_PATH:
                scope = dict(scope, path=MCP_PATH)
            headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
            conn = connect(self.settings.db_path)
            try:
                principal = auth.token_principal(conn, auth.bearer(headers.get("authorization")))
            finally:
                conn.close()
            if not principal or self.mcp_app is None:
                response = JSONResponse(
                    {"detail": "valid bearer token required (create one in Settings > API tokens)"},
                    status_code=401 if not principal else 503, headers={"WWW-Authenticate": "Bearer"})
                await response(scope, receive, send)
                return
            await self.mcp_app(scope, receive, send)
            return
        await self.web(scope, receive, send)
