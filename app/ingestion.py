"""
# PROMPT: Generate event ingestion endpoint with batch processing, idempotent deduplication, and WebSocket publishing
# CHANGES MADE: Added INSERT OR IGNORE for idempotency by event_id, batch limit of 500,
# partial success tracking, structured 503 responses on DB failure, WebSocket publish for live dashboard.
"""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError
from typing import List, Dict, Any
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


# ── Request body schema (fixes Swagger UI "No parameters" issue) ──────────────
class IngestPayload(BaseModel):
    events: List[Event]


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
async def ingest_events(payload: IngestPayload, request: Request):
    """Accept a batch of events under {"events": [...]}. Validates, deduplicates, and stores events.
    Returns a structured summary with partial success information.
    """
    events = payload.events

    if len(events) > 500:
        return JSONResponse(
            status_code=413,
            content={"error": "batch_too_large", "detail": "max 500 events"},
        )

    # Attempt to open DB per request (avoids long-lived file locks)
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
    rejected: List[Dict[str, Any]] = []
    inserted_ids: List[str] = []
    inserted_events: List[Dict[str, Any]] = []

    with _db_lock:
        cur = conn.cursor()
        for idx, evt in enumerate(events):
            # Pydantic has already validated each event; convert back to dict for storage
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
                rejected.append({"index": idx, "error": "db_error"})

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
                # event loop not running in this context; schedule via new loop
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

    status = "partial_success" if rejected or ignored_duplicates else "ok"
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
            f"ingest | trace_id={trace_id} | event_count={len(events)} | "
            f"accepted={accepted} | duplicates={ignored_duplicates} | rejected={len(rejected)}"
        )
    except Exception:
        pass

    return JSONResponse(status_code=200, content=resp)