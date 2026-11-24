"""Production entry point: existing ThaiCandy hub + secured data-ingestion API."""
import hashlib
import os
import re
import secrets

from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from main import app, db
from ingestion import configured_admin_key_hash, create_ingestion_router

# Replace the wildcard CORS policy in the legacy app with an explicit allowlist.
app.user_middleware = [m for m in app.user_middleware if m.cls is not CORSMiddleware]
app.middleware_stack = None
allowed_origins = [origin.strip() for origin in os.getenv("THAICANDY_CORS_ORIGINS", "").split(",") if origin.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-System-ID", "X-Connection-Token", "X-Admin-Key", "Idempotency-Key"],
)

ADMIN_KEY_HASH = configured_admin_key_hash()
if not ADMIN_KEY_HASH:
    raise RuntimeError("Set THAICANDY_ADMIN_KEY (32+ random characters) before starting secure_app")


def validate_connection(system_id: str, token: str) -> bool:
    return db.validate_token(token) == system_id


def system_is_active(system_id: str) -> bool:
    system = db.get_systems().get(system_id)
    return bool(system and system.get("status") == "active")


app.include_router(create_ingestion_router(
    validate_connection=validate_connection,
    system_is_active=system_is_active,
    db_path=os.getenv("THAICANDY_INGESTION_DB", "thaicandy_data/ingestion.sqlite3"),
    admin_key_hash=ADMIN_KEY_HASH,
))


_ADMIN_PATHS = {
    ("POST", "/systems/register"),
    ("GET", "/systems/list"),
    ("GET", "/logs"),
    ("GET", "/stats"),
}


@app.middleware("http")
async def protect_owner_routes(request: Request, call_next):
    method, path = request.method.upper(), request.url.path
    protected = (method, path) in _ADMIN_PATHS or bool(
        re.fullmatch(r"/systems/[^/]+/(connector|toggle)", path)
    ) or (method == "DELETE" and bool(re.fullmatch(r"/systems/[^/]+", path)))
    if protected:
        supplied = request.headers.get("X-Admin-Key", "")
        if not supplied or not secrets.compare_digest(hashlib.sha256(supplied.encode()).hexdigest(), ADMIN_KEY_HASH):
            return JSONResponse(status_code=401, content={"detail": "Owner authentication required"})
    return await call_next(request)
