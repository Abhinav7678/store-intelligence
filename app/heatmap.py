"""
# PROMPT: Rewrite heatmap endpoint for actual zone_entered/zone_exited events with dwell computation
# CHANGES MADE: Compute dwell from zone_entered→zone_exited pairs, use zone_name for display,
# fixed data_confidence naming, staff exclusion.
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
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception:
        raise HTTPException(status_code=503, detail="db_unavailable")


@router.get("/stores/{store_id}/heatmap")
def store_heatmap(store_id: str):
    conn = _connect()

    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT event_type, visitor_id, zone_id, zone_name, is_staff, timestamp, payload
            FROM events WHERE store_id = ?
        """, (store_id,))
        rows = cur.fetchall()

        zone_counts = {}
        zone_dwell = {}
        zone_enter_times = {}
        sessions = set()

        for row in rows:
            if row["is_staff"]:
                continue
            vid = row["visitor_id"]
            if vid:
                sessions.add(vid)

            et = row["event_type"]
            zone = row["zone_name"] or row["zone_id"] or ""

            if not zone:
                continue

            if et in ("zone_entered", "ZONE_ENTER"):
                zone_counts[zone] = zone_counts.get(zone, 0) + 1
                if vid:
                    zone_enter_times[(vid, zone)] = row["timestamp"]

            elif et in ("zone_exited", "ZONE_EXIT"):
                key = (vid, zone)
                if key in zone_enter_times and row["timestamp"]:
                    try:
                        enter_dt = datetime.fromisoformat(zone_enter_times[key])
                        exit_dt = datetime.fromisoformat(row["timestamp"])
                        dwell_ms = int((exit_dt - enter_dt).total_seconds() * 1000)
                        if dwell_ms > 0:
                            zone_dwell[zone] = zone_dwell.get(zone, 0) + dwell_ms
                    except Exception:
                        pass
                    del zone_enter_times[key]

        total_sessions = len(sessions)

        max_count = max(zone_counts.values()) if zone_counts else 1
        max_dwell = max(zone_dwell.values()) if zone_dwell else 1
        if max_dwell == 0:
            max_dwell = 1

        heatmap = {}
        for z, cnt in zone_counts.items():
            avg_dwell = (zone_dwell.get(z, 0) / cnt) if cnt > 0 else 0
            freq_score = (cnt / max_count) * 100 if max_count > 0 else 0
            dwell_score = (zone_dwell.get(z, 0) / max_dwell) * 100 if max_dwell > 0 else 0
            score = int((freq_score * 0.5) + (dwell_score * 0.5))
            heatmap[z] = {
                "frequency": cnt,
                "score": score,
                "avg_dwell_ms": int(avg_dwell),
            }

        data_confidence = "low" if total_sessions < 20 else "high"

        conn.close()
        return JSONResponse(content={
            "store_id": store_id,
            "total_sessions": total_sessions,
            "data_confidence": data_confidence,
            "zones": heatmap,
        })

    except HTTPException:
        raise
    except Exception as e:
        try:
            conn.close()
        except Exception:
            pass
        raise HTTPException(status_code=503, detail=f"error: {str(e)}")