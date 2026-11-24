"""Secure ThaiCandy connector. Configure secrets via environment variables."""
import os
import time
from datetime import datetime, timezone

import requests

BASE_URL = os.environ["THAICANDY_URL"].rstrip("/")
SYSTEM_ID = os.environ["THAICANDY_SYSTEM_ID"]
CONNECTION_TOKEN = os.environ["THAICANDY_CONNECTION_TOKEN"]
HEARTBEAT_SECONDS = max(30, int(os.getenv("THAICANDY_HEARTBEAT_SECONDS", "60")))
TIMEOUT_SECONDS = 10

HEADERS = {
    "X-System-ID": SYSTEM_ID,
    "X-Connection-Token": CONNECTION_TOKEN,
    "Content-Type": "application/json",
}


def send_heartbeat() -> bool:
    response = requests.post(
        f"{BASE_URL}/api/heartbeat",
        headers=HEADERS,
        json={"system_name": os.getenv("THAICANDY_SYSTEM_NAME", SYSTEM_ID)},
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json().get("ok", False)


def send_data(data_type: str, payload: dict, idempotency_key: str | None = None) -> dict:
    headers = dict(HEADERS)
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    response = requests.post(
        f"{BASE_URL}/api/data/ingest",
        headers=headers,
        json={"data_type": data_type, "payload": payload,
                    "occurred_at": datetime.now(timezone.utc).isoformat()},
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


if __name__ == "__main__":
    print(f"ThaiCandy secure connector: {SYSTEM_ID}")
    while True:
        try:
            send_heartbeat()
        except requests.RequestException as exc:
            print(f"Heartbeat failed: {exc.__class__.__name__}")
        time.sleep(HEARTBEAT_SECONDS)
