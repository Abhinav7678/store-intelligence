"""
# PROMPT: Rewrite funnel endpoint for actual challenge data format with drop-off percentages
# CHANGES MADE: Use actual event_types (entry/exit/zone_entered/queue_completed/queue_abandoned),
# de-duplicate re-entries, compute drop-off % between stages.
"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
import sqlite3
import os
import json
from datetime import datetime, timezone

router = APIRouter()
DB_PATH = os.path.join("data", "events.db")


def _connect():
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception:
        raise HTTPException(status_code=503, detail="db_unavailable")


@router.get("/stores/{store_id}/funnel")
def store_funnel(store_id: str):
    conn = _connect()

    try:
        cur = conn.cursor()
        now = datetime.now(timezone.utc)
        today_prefix = now.strftime("%Y-%m-%d")

        cur.execute("""
            SELECT event_type, visitor_id, zone_id, zone_name, is_staff, timestamp, payload
            FROM events WHERE store_id = ? ORDER BY timestamp
        """, (store_id,))
        rows = cur.fetchall()

        # Build per-visitor session data
        visitor_data = {}

        for row in rows:
            if row["is_staff"]:
                continue
            vid = row["visitor_id"]
            if not vid:
                continue

            if vid not in visitor_data:
                visitor_data[vid] = {
                    "entered": False,
                    "zone_visit": False,
                    "queued": False,
                    "purchased": False,
                }

            et = row["event_type"]
            if et in ("entry", "ENTRY"):
                visitor_data[vid]["entered"] = True
            elif et in ("zone_entered", "ZONE_ENTER"):
                visitor_data[vid]["zone_visit"] = True
            elif et in ("queue_completed", "queue_abandoned", "BILLING_QUEUE_JOIN"):
                visitor_data[vid]["queued"] = True
                if et == "queue_completed":
                    visitor_data[vid]["purchased"] = True

        sessions = list(visitor_data.values())
        total = len(sessions)

        entries = sum(1 for s in sessions if s["entered"])
        zone_visits = sum(1 for s in sessions if s["zone_visit"])
        queued = sum(1 for s in sessions if s["queued"])
        purchased = sum(1 for s in sessions if s["purchased"])

        def pct(n):
            return round((n / total) * 100, 2) if total else 0.0

        def dropoff(prev, curr):
            return round(((prev - curr) / prev) * 100, 2) if prev else 0.0

        conn.close()
        return JSONResponse(content={
            "store_id": store_id,
            "sessions": total,
            "stages": {
                "entry": {"count": entries, "pct": pct(entries), "drop_off_pct": 0.0},
                "zone_visit": {"count": zone_visits, "pct": pct(zone_visits), "drop_off_pct": dropoff(entries, zone_visits)},
                "billing_queue": {"count": queued, "pct": pct(queued), "drop_off_pct": dropoff(zone_visits, queued)},
                "purchase": {"count": purchased, "pct": pct(purchased), "drop_off_pct": dropoff(queued, purchased)},
            },
        })

    except HTTPException:
        raise
    except Exception as e:
        try:
            conn.close()
        except Exception:
            pass
        raise HTTPException(status_code=503, detail=f"error: {str(e)}")