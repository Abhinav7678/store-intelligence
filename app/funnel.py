"""
# PROMPT: Generate funnel analysis endpoint with session reconstruction and drop-off percentages
# CHANGES MADE: Added re-entry deduplication to prevent double-counting visitors,
# POS-based purchase detection via session reconstruction, drop-off percentages per stage.
"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
import sqlite3
import os
from datetime import datetime, timezone
import json

from app.sessions import reconstruct_sessions

router = APIRouter()
DB_PATH = os.path.join("data", "events.db")


def _connect():
    try:
        return sqlite3.connect(DB_PATH, timeout=5)
    except Exception:
        raise FileNotFoundError("DB not available")


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


@router.get("/stores/{store_id}/funnel")
def store_funnel(store_id: str):
    try:
        conn = _connect()
        _init_db(conn)
    except Exception:
        raise HTTPException(status_code=503, detail="db_unavailable")

    try:
        cur = conn.cursor()
        start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).strftime("%Y-%m-%d %H:%M:%S")
        cur.execute(
            "SELECT payload FROM events WHERE store_id = ? AND timestamp >= ? ORDER BY timestamp",
            (store_id, start),
        )
        rows = cur.fetchall()

        payload_rows = [r[0] for r in rows]
        sessions = reconstruct_sessions(payload_rows, store_id=store_id)

        # ── Deduplicate re-entry sessions per visitor ──
        # Challenge requirement: "Re-entries must not double-count a visitor"
        # If a visitor exits and re-enters, merge all their sessions into one,
        # preserving the deepest funnel stage reached across all visits.
        seen = {}
        for s in sessions:
            vid = s["visitor_id"]
            if vid not in seen:
                seen[vid] = s
            else:
                seen[vid]["entered"] = seen[vid]["entered"] or s["entered"]
                seen[vid]["zone_visit"] = seen[vid]["zone_visit"] or s["zone_visit"]
                seen[vid]["queued"] = seen[vid]["queued"] or s["queued"]
                seen[vid]["purchased"] = seen[vid]["purchased"] or s["purchased"]
        sessions = list(seen.values())
        # ── End dedup ──

        total_sessions = len(sessions)
        entries = sum(1 for s in sessions if s["entered"])
        zone_visits = sum(1 for s in sessions if s["zone_visit"])
        queue = sum(1 for s in sessions if s["queued"])
        purchases = sum(1 for s in sessions if s["purchased"])

        def pct(n):
            return round((n / total_sessions) * 100, 2) if total_sessions else 0.0

        return JSONResponse(content={
            "store_id": store_id,
            "sessions": total_sessions,
            "stages": {
                "entry": {"count": entries, "pct": pct(entries)},
                "zone_visit": {"count": zone_visits, "pct": pct(zone_visits)},
                "billing_queue": {"count": queue, "pct": pct(queue)},
                "purchase": {"count": purchases, "pct": pct(purchases)},
            },
        })
    finally:
        conn.close()