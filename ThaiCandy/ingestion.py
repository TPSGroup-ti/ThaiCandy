"""ThaiCandy secure, system-scoped JSON ingestion API."""
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
from typing import Any, Callable

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_BODY_BYTES = 256 * 1024
MAX_DEPTH = 12
MAX_STRING_CHARS = 20_000
ALLOWED_TYPES = {
    "customer.profile", "customer.contact", "content.document", "content.media_metadata",
    "finance.invoice", "finance.transaction", "inventory.item", "inventory.movement",
    "operations.event", "analytics.event", "project.record", "integration.webhook", "custom.record",
}
SENSITIVE_KEY = re.compile(r"(?i)(password|passwd|secret|api[_-]?key|access[_-]?token|refresh[_-]?token|private[_-]?key|authorization|card[_-]?number|cvv)")
SUSPICIOUS_TEXT = re.compile(
    r"(?i)(ignore\s+(all\s+)?(previous|prior)\s+instructions|reveal\s+(the\s+)?(system\s+)?prompt|"
    r"exfiltrat\w*|bypass\s+(security|authentication|access)|steal\s+credentials|"
    r"<\s*script\b|javascript\s*:|data\s*:\s*text/html)"
)


class IngestBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    data_type: str = Field(min_length=3, max_length=64)
    payload: dict[str, Any]
    occurred_at: datetime | None = None

    @field_validator("data_type")
    @classmethod
    def validate_type(cls, value: str) -> str:
        if value not in ALLOWED_TYPES:
            raise ValueError("unsupported data_type")
        return value


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def inspect_payload(value: Any, depth: int = 0) -> tuple[bool, str | None]:
    if depth > MAX_DEPTH:
        return True, "maximum nesting depth exceeded"
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str) or len(key) > 128:
                return True, "invalid object key"
            if SENSITIVE_KEY.search(key):
                return True, "credential or payment field detected"
            flagged, reason = inspect_payload(child, depth + 1)
            if flagged:
                return flagged, reason
    elif isinstance(value, list):
        if len(value) > 2_000:
            return True, "array too large"
        for child in value:
            flagged, reason = inspect_payload(child, depth + 1)
            if flagged:
                return flagged, reason
    elif isinstance(value, str):
        if len(value) > MAX_STRING_CHARS:
            return True, "string too long"
        if SUSPICIOUS_TEXT.search(value):
            return True, "suspicious instruction or executable content"
    elif value is not None and not isinstance(value, (bool, int, float)):
        return True, "unsupported value type"
    return False, None


def redact_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: ("[REDACTED]" if SENSITIVE_KEY.search(str(key)) else redact_sensitive(child)) for key, child in value.items()}
    if isinstance(value, list):
        return [redact_sensitive(child) for child in value]
    return value


class IngestionStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS incoming_records (
                record_id TEXT PRIMARY KEY,
                system_id TEXT NOT NULL,
                data_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('accepted','quarantined')),
                quarantine_reason TEXT,
                idempotency_key TEXT,
                occurred_at TEXT,
                received_at TEXT NOT NULL,
                UNIQUE(system_id, idempotency_key)
            )""")

    def connect(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def insert(self, system_id: str, body: IngestBody, state: str, reason: str | None, idem: str | None):
        record_id = str(uuid.uuid4())
        received_at = datetime.now(timezone.utc).isoformat()
        payload = redact_sensitive(body.payload)
        with self.connect() as db:
            try:
                db.execute(
                    "INSERT INTO incoming_records VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (record_id, system_id, body.data_type,
                     json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                     state, reason, idem, body.occurred_at.isoformat() if body.occurred_at else None, received_at),
                )
                return record_id, False
            except sqlite3.IntegrityError:
                if idem:
                    row = db.execute("SELECT record_id FROM incoming_records WHERE system_id=? AND idempotency_key=?", (system_id, idem)).fetchone()
                    if row:
                        return row["record_id"], True
                raise

    def get_for_system(self, record_id: str, system_id: str):
        with self.connect() as db:
            row = db.execute(
                "SELECT record_id,system_id,data_type,payload_json,status,quarantine_reason,occurred_at,received_at FROM incoming_records WHERE record_id=? AND system_id=?",
                (record_id, system_id),
            ).fetchone()
        return dict(row) if row else None

    def quarantine_items(self, limit: int):
        with self.connect() as db:
            rows = db.execute(
                "SELECT record_id,system_id,data_type,payload_json,quarantine_reason,received_at FROM incoming_records WHERE status='quarantined' ORDER BY received_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            result.append(item)
        return result


def create_ingestion_router(
    validate_connection: Callable[[str, str], bool],
    system_is_active: Callable[[str], bool],
    db_path: str = "thaicandy_data/ingestion.sqlite3",
    admin_key_hash: str | None = None,
) -> APIRouter:
    router = APIRouter(tags=["ThaiCandy Data Ingestion"])
    store = IngestionStore(db_path)
    lock = threading.Lock()
    request_windows: dict[str, list[float]] = {}

    def authenticate(
        x_system_id: str | None = Header(default=None, alias="X-System-ID"),
        x_connection_token: str | None = Header(default=None, alias="X-Connection-Token"),
    ) -> str:
        if not x_system_id or not x_connection_token:
            raise HTTPException(status_code=401, detail="System ID and connection token are required")
        if len(x_system_id) > 64 or len(x_connection_token) > 512 or not validate_connection(x_system_id, x_connection_token):
            raise HTTPException(status_code=403, detail="Invalid system credentials")
        if not system_is_active(x_system_id):
            raise HTTPException(status_code=403, detail="System is disabled")
        return x_system_id

    @router.post("/api/data/ingest", status_code=202)
    async def ingest(
        body: IngestBody,
        request: Request,
        system_id: str = Depends(authenticate),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ):
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > MAX_BODY_BYTES:
            raise HTTPException(status_code=413, detail="Payload exceeds 256 KiB")
        if len(json.dumps(body.payload, ensure_ascii=False).encode("utf-8")) > MAX_BODY_BYTES:
            raise HTTPException(status_code=413, detail="Payload exceeds 256 KiB")
        now = time.monotonic()
        with lock:
            recent = [stamp for stamp in request_windows.get(system_id, []) if now - stamp < 60]
            if len(recent) >= 120:
                raise HTTPException(status_code=429, detail="Rate limit exceeded")
            recent.append(now)
            request_windows[system_id] = recent
        flagged, reason = inspect_payload(body.payload)
        state = "quarantined" if flagged else "accepted"
        record_id, duplicate = store.insert(system_id, body, state, reason, idempotency_key[:128] if idempotency_key else None)
        if duplicate:
            return JSONResponse(status_code=200, content={"record_id": record_id, "status": "duplicate"})
        return {"record_id": record_id, "status": state, "received_at": datetime.now(timezone.utc).isoformat()}

    @router.get("/api/data/records/{record_id}")
    def get_record(record_id: str, system_id: str = Depends(authenticate)):
        record = store.get_for_system(record_id, system_id)
        if not record:
            raise HTTPException(status_code=404, detail="Record not found")
        record["payload"] = json.loads(record.pop("payload_json"))
        return record

    @router.get("/api/data/quarantine")
    def list_quarantine(
        x_admin_key: str | None = Header(default=None, alias="X-Admin-Key"),
        limit: int = 100,
    ):
        if not admin_key_hash or not x_admin_key or not secrets.compare_digest(sha256(x_admin_key), admin_key_hash):
            raise HTTPException(status_code=401, detail="Owner authentication required")
        return {"items": store.quarantine_items(max(1, min(limit, 200)))}

    return router


def configured_admin_key_hash() -> str | None:
    key = os.getenv("THAICANDY_ADMIN_KEY")
    if not key:
        return None
    if len(key) < 32:
        raise RuntimeError("THAICANDY_ADMIN_KEY must contain at least 32 characters")
    return sha256(key)
