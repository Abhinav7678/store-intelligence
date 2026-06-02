"""
# PROMPT: Generate zone heatmap endpoint with visit frequency, dwell time, and normalized 0-100 scores
# CHANGES MADE: Combined visit count and dwell time into composite score,
# added staff exclusion, data confidence flag, try/finally for connection safety.
# Fixed: ZeroDivisionError when dwell_ms is 0, added os.makedirs in _connect.
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
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        return sqlite3.connect(DB_PATH, timeout=5)
    except Exception:
        raise FileNotFoundError("DB not available")


@router.get("/stores/{store_id}/heatmap")
def store_heatmap(store_id: str):
    try:
        conn = _connect()
    except Exception:
        raise HTTPException(status_code=503, detail="db_unavailable")

    try:
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

        cur = conn.cursor()
        start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).strftime("%Y-%m-%d %H:%M:%S")
        cur.execute(
            "SELECT payload FROM events WHERE store_id = ? AND timestamp >= ?",
            (store_id, start),
        )
        rows = cur.fetchall()

        zone_counts = {}
        zone_dwell = {}
        sessions = set()

        for (payload_json,) in rows:
            try:
                payload = json.loads(payload_json)
            except Exception:
                continue
            if payload.get("is_staff"):
                continue
            vid = payload.get("visitor_id")
            if vid:
                sessions.add(vid)
            et = payload.get("event_type")
            if et in ("ZONE_ENTER", "ZONE_DWELL", "ZONE_EXIT"):
                zone = payload.get("zone_id")
                if not zone:
                    continue
                zone_counts[zone] = zone_counts.get(zone, 0) + 1
                zone_dwell[zone] = zone_dwell.get(zone, 0) + int(payload.get("dwell_ms", 0))

        total_sessions = len(sessions)

        # Composite score: 50% visit frequency + 50% dwell time, normalized to 0-100
        max_count = max(zone_counts.values()) if zone_counts else 1
        max_dwell_val = max(zone_dwell.values()) if zone_dwell else 0
        max_dwell = max_dwell_val if max_dwell_val > 0 else 1

        heatmap = {}
        for z, cnt in zone_counts.items():
            avg_dwell = (zone_dwell.get(z, 0) / cnt) if cnt > 0 else 0
            freq_score = (cnt / max_count) * 100 if max_count > 0 else 0
            dwell_score = (zone_dwell.get(z, 0) / max_dwell) * 100 if max_dwell > 0 else 0
            # Composite: weighted blend of frequency and dwell
            score = int((freq_score * 0.5) + (dwell_score * 0.5))
            heatmap[z] = {
                "frequency": cnt,
                "score": score,
                "avg_dwell_ms": int(avg_dwell),
            }

        data_confidence = total_sessions < 20
        return JSONResponse(content={
            "store_id": store_id,
            "total_sessions": total_sessions,
            "data_confidence_low": data_confidence,
            "zones": heatmap,
        })

    finally:
        conn.close()