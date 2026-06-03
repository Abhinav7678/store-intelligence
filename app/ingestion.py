"""
# PROMPT: Generate event ingestion endpoint with batch processing, idempotent deduplication, and WebSocket publishing
# CHANGES MADE: Added INSERT OR IGNORE for idempotency by event_id, batch limit of 500,
# partial success tracking, structured 503 responses on DB failure, WebSocket publish for live dashboard.
# Fixed: per-event validation for partial success — valid events accepted, malformed ones rejected individually.
"""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError
from typing import List, Dict, Any, Optional
import sqlite3
import os
import json
import threading
import logging
import asyncio

from app.schemas import Event
from app import ws

router = APIRouter()
logger = logging.getLogger("store_intelligence.ingestion")

# Ensure data directory and DB exist
os.makedirs("data", exist_ok=True)
DB_PATH = os.path.join("data", "events.db")
_db_lock = threading.Lock()


def _init_db(conn: sqlite3.Connection):
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
            event_id TEXT PRIMARY KEY,
            store_id TEXT,
            camera_id TEXT,
            visitor_id TEXT,
            event_type TEXT,
            timestamp TEXT,
            payload TEXT
        )
        """
    )
    conn.commit()


@router.post("/ingest")
async def ingest_events(request: Request):
    """Accept a batch of events under {"events": [...]}.
    Validates per-event, deduplicates, and stores.
    Returns partial success — valid events accepted, malformed ones rejected individually.
    """
    # Parse raw JSON body
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_json", "detail": "Request body is not valid JSON"},
        )

    raw_events = body.get("events", [])
    if not isinstance(raw_events, list):
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_format", "detail": "'events' must be a list"},
        )

    if len(raw_events) > 500:
        return JSONResponse(
            status_code=413,
            content={"error": "batch_too_large", "detail": "max 500 events"},
        )

    # Per-event validation
    valid_events: List[Event] = []
    rejected: List[Dict[str, Any]] = []

    for idx, raw in enumerate(raw_events):
        try:
            evt = Event.parse_obj(raw)
            valid_events.append(evt)
        except ValidationError as ve:
            rejected.append({
                "index": idx,
                "event_id": raw.get("event_id", None) if isinstance(raw, dict) else None,
                "error": "validation_error",
                "detail": str(ve.errors()[0]["msg"]) if ve.errors() else str(ve),
            })

    # Attempt to open DB
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"error": "service_unavailable", "detail": "db_unavailable"},
        )

    try:
        _init_db(conn)
    except Exception as e:
        logger.exception("failed to init db: %s", e)
        return JSONResponse(
            status_code=503,
            content={"error": "service_unavailable", "detail": "db_init_failed"},
        )

    accepted = 0
    ignored_duplicates = 0
    inserted_ids: List[str] = []
    inserted_events: List[Dict[str, Any]] = []

    with _db_lock:
        cur = conn.cursor()
        for evt in valid_events:
            raw = evt.dict()
            payload_json = json.dumps(raw, default=str)
            try:
                cur.execute(
                    """
                    INSERT OR IGNORE INTO events
                        (event_id, store_id, camera_id, visitor_id, event_type, timestamp, payload)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        evt.event_id,
                        evt.store_id,
                        evt.camera_id,
                        evt.visitor_id,
                        evt.event_type,
                        evt.timestamp.replace("T", " ").split(".")[0].replace("Z", ""),
                        payload_json,
                    ),
                )
                if cur.rowcount == 1:
                    accepted += 1
                    inserted_ids.append(evt.event_id)
                    inserted_events.append(raw)
                else:
                    ignored_duplicates += 1
            except sqlite3.DatabaseError as dbe:
                logger.exception("db error on insert: %s", dbe)
                rejected.append({"index": -1, "event_id": evt.event_id, "error": "db_error"})

        try:
            conn.commit()
        except Exception as e:
            logger.exception("commit failed: %s", e)
            conn.close()
            return JSONResponse(
                status_code=503,
                content={"error": "service_unavailable", "detail": "db_commit_failed"},
            )

    # Publish accepted events to websocket manager (best-effort, non-blocking)
    try:
        for ev in inserted_events:
            try:
                asyncio.create_task(ws.publish_event(ev))
            except RuntimeError:
                try:
                    loop = asyncio.new_event_loop()
                    loop.run_until_complete(ws.publish_event(ev))
                    loop.close()
                except Exception:
                    logger.exception("failed to publish event to ws")
    except Exception:
        logger.exception("ws publish loop failed")
    finally:
        try:
            conn.close()
        except Exception:
            pass

    status = "partial_success" if rejected else ("ok" if accepted > 0 else "ok")
    if ignored_duplicates > 0 and accepted == 0 and not rejected:
        status = "ok"

    resp = {
        "status": status,
        "accepted": accepted,
        "duplicates_ignored": ignored_duplicates,
        "rejected": rejected,
        "inserted_ids": inserted_ids,
    }

    # Structured log
    try:
        trace_id = request.headers.get("X-Trace-ID", "-")
        logger.info(
            f"ingest | trace_id={trace_id} | event_count={len(raw_events)} | "
            f"accepted={accepted} | duplicates={ignored_duplicates} | rejected={len(rejected)}"
        )
    except Exception:
        pass

    return JSONResponse(status_code=200, content=resp)