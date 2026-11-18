"""REST API (/api). Thin layer over cellar.py — put business rules there, not here."""

from __future__ import annotations

import hmac
import json
import sqlite3
from typing import Any, Iterator

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from . import __version__, auth, cellar, images, importers
from .config import Settings
from .db import connect

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
CSRF_HEADER = "x-cellarium"


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_conn(request: Request) -> Iterator[sqlite3.Connection]:
    conn = connect(request.app.state.settings.db_path)
    try:
        yield conn
    finally:
        conn.close()


def resolve_principal(request: Request, conn: sqlite3.Connection) -> auth.Principal | None:
    token = auth.bearer(request.headers.get("authorization"))
    if token:
        return auth.token_principal(conn, token)
    principal = auth.session_principal(conn, request.cookies.get(auth.SESSION_COOKIE))
    if principal and request.method not in SAFE_METHODS and request.headers.get(CSRF_HEADER) != "1":
        # Cookie sessions must send a custom header on writes: blocks cross-site form posts (CSRF).
        raise HTTPException(403, "missing X-Cellarium header")
    return principal


def current_principal(request: Request, conn: sqlite3.Connection = Depends(get_conn)) -> auth.Principal:
    principal = resolve_principal(request, conn)
    if not principal:
        raise HTTPException(401, "authentication required")
    conn.actor = principal.actor  # recorded in the movements history
    request.state.principal = principal
    return principal


def require_writer(request: Request, principal: auth.Principal = Depends(current_principal)) -> auth.Principal:
    if request.method not in SAFE_METHODS and not principal.can_write:
        raise HTTPException(403, "read-only access")
    return principal


def require_owner(request: Request, principal: auth.Principal = Depends(current_principal)) -> auth.Principal:
    if not principal.is_owner or (request.method not in SAFE_METHODS and not principal.can_write):
        raise HTTPException(403, "owner access required")
    return principal


public = APIRouter(prefix="/api")
account = APIRouter(prefix="/api", dependencies=[Depends(current_principal)])
owners = APIRouter(prefix="/api", dependencies=[Depends(require_owner)])
router = APIRouter(prefix="/api", dependencies=[Depends(require_writer)])


# ---------------------------------------------------------------- session / setup

class LoginBody(BaseModel):
    username: str
    password: str


class SetupBody(BaseModel):
    username: str
    password: str = Field(min_length=8)
    display_name: str | None = None
    code: str


class PasswordBody(BaseModel):
    current: str
    password: str = Field(min_length=8)


def _client(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _set_session_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(auth.SESSION_COOKIE, token, max_age=settings.session_days * 86400, httponly=True,
                        samesite="lax", secure=settings.secure_cookies, path="/")


@public.get("/session")
def session_info(request: Request, conn=Depends(get_conn), settings: Settings = Depends(get_settings)):
    principal = auth.token_principal(conn, auth.bearer(request.headers.get("authorization"))) or \
        auth.session_principal(conn, request.cookies.get(auth.SESSION_COOKIE))
    return {
        "authenticated": principal is not None,
        "user": principal.public() if principal else None,
        "setup_required": not auth.has_users(conn),
        "version": __version__,
        "image_generation": settings.image_generation_enabled and bool(principal and principal.can_write),
    }


@public.post("/setup")
def setup(body: SetupBody, request: Request, response: Response, conn=Depends(get_conn),
          settings: Settings = Depends(get_settings)):
    """First start only: create the first owner account, guarded by the code printed in the server log."""
    if auth.has_users(conn):
        raise HTTPException(409, "already configured")
    expected = request.app.state.setup_code
    if not expected or not hmac.compare_digest(body.code.strip(), expected):
        raise HTTPException(403, "invalid setup code (see the server log)")
    user = auth.create_user(conn, body.username, body.password, "owner", body.display_name)
    request.app.state.setup_code = None
    _set_session_cookie(response, auth.create_session(conn, user["id"], settings.session_days), settings)
    return {"ok": True}


@public.post("/login")
def login(body: LoginBody, request: Request, response: Response, conn=Depends(get_conn),
          settings: Settings = Depends(get_settings)):
    throttle: auth.LoginThrottle = request.app.state.throttle
    client = _client(request)
    if throttle.blocked(client):
        raise HTTPException(429, "too many attempts, retry in a few minutes")
    principal = auth.authenticate(conn, body.username, body.password)
    if not principal:
        throttle.fail(client)
        raise HTTPException(401, "wrong username or password")
    throttle.reset(client)
    _set_session_cookie(response, auth.create_session(conn, principal.user_id, settings.session_days), settings)
    return {"ok": True, "user": principal.public()}


@public.post("/logout")
def logout(request: Request, response: Response, conn=Depends(get_conn)):
    auth.drop_session(conn, request.cookies.get(auth.SESSION_COOKIE))
    response.delete_cookie(auth.SESSION_COOKIE, path="/")
    return {"ok": True}


# ---------------------------------------------------------------- own account (owners and readers)

@account.get("/me")
def me(principal: auth.Principal = Depends(current_principal)):
    return principal.public()


@account.post("/password")
def change_password(body: PasswordBody, response: Response, conn=Depends(get_conn),
                    principal: auth.Principal = Depends(current_principal),
                    settings: Settings = Depends(get_settings)):
    if principal.via != "session":
        raise HTTPException(403, "use the web interface to change a password")
    if not auth.check_user_password(conn, principal.user_id, body.current):
        raise HTTPException(403, "wrong current password")
    auth.update_user(conn, principal.user_id, password=body.password)
    _set_session_cookie(response, auth.create_session(conn, principal.user_id, settings.session_days), settings)
    return {"ok": True}


class TokenBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    scope: str = "read"


@account.get("/tokens")
def tokens(conn=Depends(get_conn), principal: auth.Principal = Depends(current_principal)):
    return auth.list_tokens(conn, None if principal.is_owner else principal.user_id)


@account.post("/tokens")
def create_token(body: TokenBody, conn=Depends(get_conn), principal: auth.Principal = Depends(current_principal)):
    if principal.via != "session":
        raise HTTPException(403, "tokens are created from the web interface")
    return auth.create_token(conn, principal.user_id, body.name, body.scope)


@account.delete("/tokens/{token_id}")
def revoke_token(token_id: int, conn=Depends(get_conn), principal: auth.Principal = Depends(current_principal)):
    owner_wide = principal.is_owner and principal.can_write
    if not auth.revoke_token(conn, token_id, None if owner_wide else principal.user_id):
        raise HTTPException(404, "token not found")
    return {"ok": True}


# ---------------------------------------------------------------- accounts (owners only)

class UserBody(BaseModel):
    username: str
    password: str = Field(min_length=8)
    role: str = "reader"
    display_name: str | None = None


class UserPatch(BaseModel):
    role: str | None = None
    display_name: str | None = None
    password: str | None = None


@owners.get("/users")
def users(conn=Depends(get_conn)):
    return auth.list_users(conn)


@owners.post("/users")
def create_user(body: UserBody, conn=Depends(get_conn)):
    return auth.create_user(conn, body.username, body.password, body.role, body.display_name)


@owners.patch("/users/{user_id}")
def update_user(user_id: int, body: UserPatch, conn=Depends(get_conn)):
    return auth.update_user(conn, user_id, role=body.role, display_name=body.display_name, password=body.password)


@owners.delete("/users/{user_id}")
def delete_user(user_id: int, conn=Depends(get_conn), principal: auth.Principal = Depends(current_principal)):
    if user_id == principal.user_id:
        raise HTTPException(400, "you cannot delete your own account")
    auth.delete_user(conn, user_id)
    return {"ok": True}


# ---------------------------------------------------------------- reference data & views

@router.get("/types")
def types(conn=Depends(get_conn)):
    return cellar.list_types(conn)


class TypeBody(BaseModel):
    code: str
    label_fr: str
    label_en: str
    color: str = "#999999"
    sort: int = 100


@router.post("/types")
def add_type(body: TypeBody, conn=Depends(get_conn)):
    return cellar.add_type(conn, body.code, body.label_fr, body.label_en, body.color, body.sort)


@router.delete("/types/{code}")
def delete_type(code: str, conn=Depends(get_conn)):
    cellar.delete_type(conn, code)
    return {"ok": True}


@router.get("/summary")
def summary(conn=Depends(get_conn)):
    return cellar.summary(conn)


@router.get("/cellar")
def cellar_list(include_empty: bool = False, conn=Depends(get_conn)):
    return cellar.cellar_rows(conn, include_empty=include_empty)


@router.get("/schedule")
def schedule(conn=Depends(get_conn)):
    return cellar.schedule(conn)


@router.get("/plan")
def plan(conn=Depends(get_conn)):
    return cellar.plan(conn)


@router.get("/history")
def history(kind: str | None = None, year: int | None = None, limit: int = 500, conn=Depends(get_conn)):
    return cellar.history(conn, kind=kind, year=year, limit=min(limit, 5000))


# ---------------------------------------------------------------- wines

@router.get("/wines")
def wines(q: str | None = None, type: str | None = None, in_stock: bool = False, limit: int = 200,
          conn=Depends(get_conn)):
    return cellar.search_wines(conn, q, type, in_stock, min(limit, 1000))


@router.post("/wines")
def create_wine(data: dict[str, Any] = Body(...), conn=Depends(get_conn)):
    wine_id = cellar.create_wine(conn, data)
    return cellar.get_wine(conn, wine_id)


@router.get("/wines/{wine_id}")
def get_wine(wine_id: int, conn=Depends(get_conn)):
    return cellar.get_wine(conn, wine_id)


@router.patch("/wines/{wine_id}")
def update_wine(wine_id: int, data: dict[str, Any] = Body(...), conn=Depends(get_conn)):
    result = cellar.update_wine(conn, wine_id, data)
    return {**result, "wine": cellar.get_wine(conn, wine_id)}


@router.delete("/wines/{wine_id}")
def delete_wine(wine_id: int, conn=Depends(get_conn), settings: Settings = Depends(get_settings)):
    image = cellar.get_wine(conn, wine_id)["image"]
    cellar.delete_wine(conn, wine_id)
    images.delete_image(settings, image)
    return {"ok": True}


@router.post("/wines/{wine_id}/image")
async def upload_image(wine_id: int, file: UploadFile = File(...), conn=Depends(get_conn),
                       settings: Settings = Depends(get_settings)):
    data = await file.read()
    try:
        name = images.save_image(settings, data)
    except images.ImageError as exc:
        raise HTTPException(400, str(exc)) from None
    images.delete_image(settings, cellar.set_wine_image(conn, wine_id, name, "upload"))
    return cellar.get_wine(conn, wine_id)


@router.delete("/wines/{wine_id}/image")
def remove_image(wine_id: int, conn=Depends(get_conn), settings: Settings = Depends(get_settings)):
    images.delete_image(settings, cellar.set_wine_image(conn, wine_id, None, None))
    return cellar.get_wine(conn, wine_id)


@router.post("/wines/{wine_id}/image/generate")
async def generate_image(wine_id: int, request: Request, conn=Depends(get_conn),
                         settings: Settings = Depends(get_settings)):
    wine = cellar.get_wine(conn, wine_id)
    labels = {t["code"]: t["label_en"] for t in cellar.list_types(conn)}
    prompt = images.build_prompt(wine, labels.get(wine["type"], "wine"))
    try:
        data = await images.generate_image(settings, prompt, getattr(request.app.state, "image_client", None))
        name = images.save_image(settings, data)
    except images.ImageError as exc:
        raise HTTPException(502, str(exc)) from None
    images.delete_image(settings, cellar.set_wine_image(conn, wine_id, name, "ai"))
    return cellar.get_wine(conn, wine_id)


@router.get("/media/{name}")
def media(name: str, settings: Settings = Depends(get_settings)):
    path = images.image_path(settings, name)
    if not path:
        raise HTTPException(404, "image not found")
    return FileResponse(path, headers={"Cache-Control": "private, max-age=31536000, immutable"})


# ---------------------------------------------------------------- vintages, lots, bottles

@router.patch("/vintages/{vintage_id}")
def update_vintage(vintage_id: int, data: dict[str, Any] = Body(...), conn=Depends(get_conn)):
    return cellar.update_vintage(conn, vintage_id, data)


class AddBottlesBody(BaseModel):
    year: int | str | None = None
    quantity: int = 1
    volume_cl: float = 75
    unit_price: float | None = None
    purchase_date: str | None = None
    supplier: str | None = None
    comment: str | None = None
    storage_id: int | None = None
    positions: list[dict] | None = None


@router.post("/wines/{wine_id}/bottles")
def add_bottles(wine_id: int, body: AddBottlesBody, conn=Depends(get_conn)):
    return cellar.add_bottles(conn, wine_id, **body.model_dump())


@router.patch("/lots/{lot_id}")
def update_lot(lot_id: int, data: dict[str, Any] = Body(...), conn=Depends(get_conn)):
    cellar.update_lot(conn, lot_id, data)
    return {"ok": True}


@router.delete("/lots/{lot_id}")
def delete_lot(lot_id: int, conn=Depends(get_conn)):
    cellar.delete_lot(conn, lot_id)
    return {"ok": True}


class OutBody(BaseModel):
    status: str = "drunk"
    date: str | None = None
    note: str | None = None
    rating: int | None = None


class MoveBody(BaseModel):
    storage_id: int | None = None
    row: int | None = None
    col: int | None = None
    swap: bool = False


@router.get("/bottles/{bottle_id}")
def get_bottle(bottle_id: int, conn=Depends(get_conn)):
    return cellar.get_bottle(conn, bottle_id)


@router.post("/bottles/{bottle_id}/out")
def bottle_out(bottle_id: int, body: OutBody, conn=Depends(get_conn)):
    return cellar.remove_bottle(conn, bottle_id, body.status, body.date, body.note, body.rating)


@router.post("/bottles/{bottle_id}/restore")
def bottle_restore(bottle_id: int, conn=Depends(get_conn)):
    return cellar.restore_bottle(conn, bottle_id)


@router.post("/bottles/{bottle_id}/move")
def bottle_move(bottle_id: int, body: MoveBody, conn=Depends(get_conn)):
    return cellar.move_bottle(conn, bottle_id, body.storage_id, body.row, body.col, body.swap)


@router.delete("/bottles/{bottle_id}")
def bottle_delete(bottle_id: int, conn=Depends(get_conn)):
    cellar.delete_bottle(conn, bottle_id)
    return {"ok": True}


# ---------------------------------------------------------------- storages

@router.get("/storages")
def storages(conn=Depends(get_conn)):
    return cellar.list_storages(conn)


@router.post("/storages")
def create_storage(data: dict[str, Any] = Body(...), conn=Depends(get_conn)):
    return {"id": cellar.create_storage(conn, data)}


@router.patch("/storages/{storage_id}")
def update_storage(storage_id: int, data: dict[str, Any] = Body(...), unplace_overflow: bool = False,
                   conn=Depends(get_conn)):
    return cellar.update_storage(conn, storage_id, data, unplace_overflow)


@router.delete("/storages/{storage_id}")
def delete_storage(storage_id: int, conn=Depends(get_conn)):
    return cellar.delete_storage(conn, storage_id)


# ---------------------------------------------------------------- import / export

@router.post("/import/preview")
async def import_preview(file: UploadFile = File(...), conn=Depends(get_conn)):
    raw = await file.read()
    if file.filename and file.filename.lower().endswith(".json"):
        try:
            payload = json.loads(raw)
        except ValueError:
            raise HTTPException(400, "invalid JSON") from None
        counts = {t: len(v) for t, v in (payload.get("data") or {}).items()}
        return {"format": "cellarium-json", "counts": counts, "rows": []}
    return importers.parse_file(raw, file.filename or "", cellar.list_types(conn))


class CommitBody(BaseModel):
    rows: list[dict]
    mode: str = "skip_existing"
    storage_id: int | None = None


@router.post("/import/commit")
def import_commit(body: CommitBody, conn=Depends(get_conn)):
    return importers.commit_rows(conn, body.rows, body.mode, body.storage_id)


@router.post("/import/restore")
async def import_restore(file: UploadFile = File(...), confirm: str = Form(...), conn=Depends(get_conn),
                         settings: Settings = Depends(get_settings)):
    if confirm != "REPLACE":
        raise HTTPException(400, "confirm must be REPLACE")
    try:
        payload = json.loads(await file.read())
    except ValueError:
        raise HTTPException(400, "invalid JSON") from None
    return cellar.import_json(conn, payload, settings.images_dir)


@router.get("/export.csv")
def export_csv(conn=Depends(get_conn)):
    return PlainTextResponse("\ufeff" + importers.export_csv(conn), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": 'attachment; filename="cellarium.csv"'})


@router.get("/export.json")
def export_json(conn=Depends(get_conn), settings: Settings = Depends(get_settings)):
    return JSONResponse(cellar.export_json(conn, settings.images_dir),
                        headers={"Content-Disposition": 'attachment; filename="cellarium.json"'})
