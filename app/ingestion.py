"""
# PROMPT: Rewrite event ingestion to accept actual challenge data format with 3 event shapes
# CHANGES MADE: Extract store_id from store_code/store_id, visitor_id from id_token/track_id,
# timestamp from event_timestamp/event_time/queue_join_ts. Use model_validate (Pydantic v2).
"""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from typing import List, Dict, Any
import sqlite3
import os
import json
import uuid
import threading
import logging
import asyncio

from app.schemas import Event
from app import ws

router = APIRouter()
logger = logging.getLogger("store_intelligence.ingestion")

os.makedirs("data", exist_ok=True)
DB_PATH = os.path.join("data", "events.db")
_db_lock = threading.Lock()


def _init_db(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS events (
            event_id TEXT PRIMARY KEY,
            store_id TEXT,
            camera_id TEXT,
            visitor_id TEXT,
            event_type TEXT,
            timestamp TEXT,
            zone_id TEXT,
            zone_name TEXT,
            is_staff INTEGER DEFAULT 0,
            payload TEXT
        )
    """)
    conn.commit()


@router.post("/ingest")
async def ingest_events(request: Request):
    """Accept batch of events in actual challenge format.
    Validates per-event, deduplicates by event_id, stores with full payload.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "invalid_json", "detail": "Request body is not valid JSON"})

    raw_events = body.get("events", [])
    if not isinstance(raw_events, list):
        return JSONResponse(status_code=400, content={"error": "invalid_format", "detail": "'events' must be a list"})

    if len(raw_events) > 500:
        return JSONResponse(status_code=413, content={"error": "batch_too_large", "detail": "max 500 events"})

    valid_events: List[Event] = []
    rejected: List[Dict[str, Any]] = []

    for idx, raw in enumerate(raw_events):
        try:
            evt = Event.model_validate(raw)
            valid_events.append(evt)
        except ValidationError as ve:
            rejected.append({
                "index": idx,
                "event_id": raw.get("event_id") or raw.get("queue_event_id") if isinstance(raw, dict) else None,
                "error": "validation_error",
                "detail": str(ve.errors()[0]["msg"]) if ve.errors() else str(ve),
            })

    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
    except Exception:
        return JSONResponse(status_code=503, content={"error": "service_unavailable", "detail": "db_unavailable"})

    try:
        _init_db(conn)
    except Exception as e:
        logger.exception("failed to init db: %s", e)
        return JSONResponse(status_code=503, content={"error": "service_unavailable", "detail": "db_init_failed"})

    accepted = 0
    ignored_duplicates = 0
    inserted_ids: List[str] = []
    inserted_events: List[Dict[str, Any]] = []

    with _db_lock:
        cur = conn.cursor()
        for evt in valid_events:
            event_id = evt.get_event_id()
            store_id = evt.get_store_id()
            visitor_id = evt.get_visitor_id()
            camera_id = evt.get_camera_id()
            timestamp = evt.get_timestamp()
            is_staff = 1 if evt.get_is_staff() else 0
            event_type = evt.event_type
            zone_id = evt.zone_id or ""
            zone_name = evt.zone_name or ""

            raw_dict = evt.model_dump(exclude_none=False)
            payload_json = json.dumps(raw_dict, default=str)

            try:
                cur.execute("""
                    INSERT OR IGNORE INTO events
                        (event_id, store_id, camera_id, visitor_id, event_type, timestamp, zone_id, zone_name, is_staff, payload)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (event_id, store_id, camera_id, visitor_id, event_type, timestamp, zone_id, zone_name, is_staff, payload_json))
                if cur.rowcount == 1:
                    accepted += 1
                    inserted_ids.append(event_id)
                    inserted_events.append(raw_dict)
                else:
                    ignored_duplicates += 1
            except sqlite3.DatabaseError as dbe:
                logger.exception("db error on insert: %s", dbe)
                rejected.append({"index": -1, "event_id": event_id, "error": "db_error"})

        try:
            conn.commit()
        except Exception as e:
            logger.exception("commit failed: %s", e)
            conn.close()
            return JSONResponse(status_code=503, content={"error": "service_unavailable", "detail": "db_commit_failed"})

    # Publish to WebSocket
    try:
        for ev in inserted_events:
            try:
                asyncio.create_task(ws.publish_event(ev))
            except RuntimeError:
                pass
    except Exception:
        logger.exception("ws publish failed")
    finally:
        try:
            conn.close()
        except Exception:
            pass

    status = "partial_success" if rejected else "ok"

    return JSONResponse(status_code=200, content={
        "status": status,
        "accepted": accepted,
        "duplicates_ignored": ignored_duplicates,
        "rejected": rejected,
        "inserted_ids": inserted_ids,
    })