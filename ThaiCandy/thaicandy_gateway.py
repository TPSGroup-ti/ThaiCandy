"""ThaiCandy secure data-ingestion gateway. Untrusted input is stored, never executed."""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_BODY_BYTES = 256 * 1024
MAX_DEPTH = 12
MAX_STRING_CHARS = 20_000
ALLOWED_DATA_TYPES = {
    "customer.profile", "customer.contact", "content.document", "content.media_metadata",
    "finance.invoice", "finance.transaction", "inventory.item", "inventory.movement",
    "operations.event", "analytics.event", "project.record", "integration.webhook", "custom.record",
}
SUSPICIOUS_TEXT = re.compile(
    r"(?i)(ignore\s+(all\s+)?(previous|prior)\s+instructions|reveal\s+(the\s+)?(system\s+)?prompt|"
    r"exfiltrat\w*|bypass\s+(security|authentication|access)|steal\s+credentials|"
    r"<\s*script\b|javascript\s*:|data\s*:\s*text/html)"
)
SENSITIVE_KEY = re.compile(r"(?i)(password|passwd|secret|api[_-]?key|access[_-]?token|refresh[_-]?token|private[_-]?key|authorization)")


class IngestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    source: str = Field(min_length=2, max_length=80, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._:-]*$")
    data_type: str = Field(min_length=3, max_length=64)
    payload: dict[str, Any]
    occurred_at: datetime | None = None

    @field_validator("data_type")
    @classmethod
    def validate_data_type(cls, value: str) -> str:
        if value not in ALLOWED_DATA_TYPES:
            raise ValueError("unsupported data_type")
        return value


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sanitize(value: Any) -> Any:
    """Remove credential-like values before persisting quarantined submissions."""
    if isinstance(value, dict):
        return {key: ("[REDACTED]" if SENSITIVE_KEY.search(str(key)) else _sanitize(child)) for key, child in value.items()}
    if isinstance(value, list):
        return [_sanitize(child) for child in value]
    return value


def _walk(value: Any, depth: int = 0) -> tuple[bool, str | None]:
    if depth > MAX_DEPTH:
        return True, "maximum nesting depth exceeded"
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str) or len(key) > 128:
                return True, "invalid object key"
            if SENSITIVE_KEY.search(key):
                return True, "credential-like field detected"
            bad, reason = _walk(child, depth + 1)
            if bad:
                return True, reason
    elif isinstance(value, list):
        if len(value) > 2_000:
            return True, "array too large"
        for child in value:
            bad, reason = _walk(child, depth + 1)
            if bad:
                return True, reason
    elif isinstance(value, str):
        if len(value) > MAX_STRING_CHARS:
            return True, "string too long"
        if SUSPICIOUS_TEXT.search(value):
            return True, "suspicious instruction or executable content"
    elif value is not None and not isinstance(value, (bool, int, float)):
        return True, "unsupported value type"
    return False, None


class GatewayStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS ingest_records (
                record_id TEXT PRIMARY KEY,
                client_id TEXT NOT NULL,
                source TEXT NOT NULL,
                data_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('accepted','quarantined')),
                quarantine_reason TEXT,
                idempotency_key TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(client_id, idempotency_key)
            )""")

    def connect(self):
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def save(self, client_id: str, req: IngestRequest, payload: dict[str, Any], state: str, reason: str | None, idem: str | None):
        record_id = str(uuid.uuid4())
        created = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            try:
                db.execute(
                    "INSERT INTO ingest_records VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (record_id, client_id, req.source, req.data_type,
                     json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                     state, reason, idem, created),
                )
                return record_id, False
            except sqlite3.IntegrityError:
                if idem:
                    row = db.execute(
                        "SELECT record_id FROM ingest_records WHERE client_id=? AND idempotency_key=?",
                        (client_id, idem),
                    ).fetchone()
                    if row:
                        return row["record_id"], True
                raise

    def quarantine(self, limit: int = 100):
        with self.connect() as db:
            rows = db.execute(
                "SELECT record_id,client_id,source,data_type,quarantine_reason,created_at FROM ingest_records WHERE status='quarantined' ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_for_client(self, record_id: str, client_id: str):
        with self.connect() as db:
            row = db.execute(
                "SELECT record_id,client_id,source,data_type,payload_json,status,quarantine_reason,created_at "
                "FROM ingest_records WHERE record_id=? AND client_id=?",
                (record_id, client_id),
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["payload"] = json.loads(result.pop("payload_json"))
        return result


def create_router(db_path: str, api_keys: dict[str, dict[str, Any]], admin_key_hash: str | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["ThaiCandy Data Gateway"])
    store = GatewayStore(db_path)
    rate_lock = threading.Lock()
    request_times: dict[str, list[float]] = {}

    def authenticate(x_api_key: str | None = Header(default=None)) -> dict[str, Any]:
        if not x_api_key or len(x_api_key) > 512:
            raise HTTPException(status_code=401, detail="API key required", headers={"WWW-Authenticate": "ApiKey"})
        digest = _sha256(x_api_key)
        # Compare against all configured digests to avoid early-exit key matching.
        match = next((cfg for key_hash, cfg in api_keys.items() if secrets.compare_digest(digest, key_hash)), None)
        if match is None:
            raise HTTPException(status_code=401, detail="Invalid API key")
        return match

    @router.get("/health")
    def health():
        return {"service": "thaicandy-data-gateway", "status": "ok", "version": "1.0.0"}

    @router.post("/ingest", status_code=status.HTTP_202_ACCEPTED)
    async def ingest(
        body: IngestRequest,
        request: Request,
        client: dict[str, Any] = Depends(authenticate),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ):
        if "data:write" not in client.get("scopes", []):
            raise HTTPException(status_code=403, detail="API key lacks data:write scope")
        now = time.monotonic()
        client_id = str(client["client_id"])
        with rate_lock:
            recent = [stamp for stamp in request_times.get(client_id, []) if now - stamp < 60]
            if len(recent) >= 120:
                raise HTTPException(status_code=429, detail="Rate limit exceeded; retry later")
            recent.append(now)
            request_times[client_id] = recent
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > MAX_BODY_BYTES:
            raise HTTPException(status_code=413, detail="Payload too large")
        serialized = json.dumps(body.payload, ensure_ascii=False, separators=(",", ":"))
        if len(serialized.encode("utf-8")) > MAX_BODY_BYTES:
            raise HTTPException(status_code=413, detail="Payload too large")
        bad, reason = _walk(body.payload)
        state = "quarantined" if bad else "accepted"
        record_id, duplicate = store.save(
            str(client["client_id"]), body, _sanitize(body.payload), state, reason,
            idempotency_key[:128] if idempotency_key else None,
        )
        if duplicate:
            return JSONResponse(status_code=200, content={"record_id": record_id, "status": "duplicate"})
        return {"record_id": record_id, "status": state, "received_at": datetime.now(timezone.utc).isoformat()}

    @router.get("/records/{record_id}")
    def get_record(record_id: str, client: dict[str, Any] = Depends(authenticate)):
        if "data:read" not in client.get("scopes", []):
            raise HTTPException(status_code=403, detail="API key lacks data:read scope")
        record = store.get_for_client(record_id, str(client["client_id"]))
        if not record:
            raise HTTPException(status_code=404, detail="Record not found")
        return record

    @router.get("/admin/quarantine")
    def list_quarantine(x_admin_key: str | None = Header(default=None, alias="X-Admin-Key"), limit: int = 100):
        if not admin_key_hash or not x_admin_key or not secrets.compare_digest(_sha256(x_admin_key), admin_key_hash):
            raise HTTPException(status_code=401, detail="Admin authentication required")
        return {"items": store.quarantine(max(1, min(limit, 200)))}

    return router


def load_api_keys() -> dict[str, dict[str, Any]]:
    """Load raw client keys from env JSON, retaining only SHA-256 digests in memory."""
    raw = os.getenv("THAICANDY_API_KEYS", "[]")
    entries = json.loads(raw)
    result: dict[str, dict[str, Any]] = {}
    for item in entries:
        key = item.get("key", "")
        client_id = item.get("client_id", "")
        scopes = item.get("scopes", [])
        if len(key) < 32 or not client_id or not isinstance(scopes, list):
            raise RuntimeError("Each API key needs >=32 chars, client_id and scopes")
        result[_sha256(key)] = {"client_id": client_id, "scopes": scopes}
    return result


def create_app(db_path: str | None = None, api_keys: dict[str, dict[str, Any]] | None = None, admin_key_hash: str | None = None) -> FastAPI:
    app = FastAPI(title="ThaiCandy Universal API", version="1.0.0")
    app.include_router(create_router(
        db_path or os.getenv("THAICANDY_GATEWAY_DB", "./data/thaicandy_gateway.sqlite3"),
        api_keys if api_keys is not None else load_api_keys(),
        admin_key_hash if admin_key_hash is not None else (_sha256(os.environ["THAICANDY_ADMIN_KEY"]) if os.getenv("THAICANDY_ADMIN_KEY") else None),
    ))
    return app


app = create_app()
