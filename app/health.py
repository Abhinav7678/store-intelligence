"""
# PROMPT: Update health endpoint to parse actual timestamp format (ISO with microseconds, no Z)
# CHANGES MADE: Handle "2026-03-08T18:10:05.120000" format, fixed STALE_FEED detection.
"""
from fastapi import APIRouter
from fastapi.responses import JSONResponse
import sqlite3
import os
from datetime import datetime, timezone

router = APIRouter()
DB_PATH = os.path.join("data", "events.db")


def _connect():
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception:
        return None


@router.get("/health")
def health_check():
    conn = _connect()
    if conn is None:
        return JSONResponse(status_code=503, content={
            "status": "ERROR",
            "message": "db_unavailable",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "stores": {}
        })

    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY, store_id TEXT, camera_id TEXT,
                visitor_id TEXT, event_type TEXT, timestamp TEXT,
                zone_id TEXT, zone_name TEXT, is_staff INTEGER DEFAULT 0, payload TEXT
            )
        """)
        conn.commit()

        cur = conn.cursor()
        cur.execute("""
            SELECT store_id, MAX(timestamp) as last_event_at
            FROM events GROUP BY store_id
        """)
        rows = cur.fetchall()

        now = datetime.now(timezone.utc)
        store_status = {}

        for row in rows:
            store_id = row["store_id"]
            last_event_at = row["last_event_at"]

            try:
                # Handle multiple formats: with Z, with +00:00, plain ISO, with microseconds
                ts_str = last_event_at.replace("Z", "+00:00")
                if "+" not in ts_str and ts_str.count("-") <= 2:
                    ts_str = ts_str + "+00:00"
                last_ts = datetime.fromisoformat(ts_str)
                if last_ts.tzinfo is None:
                    last_ts = last_ts.replace(tzinfo=timezone.utc)

                lag_minutes = int((now - last_ts).total_seconds() // 60)

                store_status[store_id] = {
                    "last_event_at": last_event_at,
                    "lag_minutes": lag_minutes,
                    "status": "STALE_FEED" if lag_minutes > 10 else "OK"
                }
            except Exception:
                store_status[store_id] = {
                    "last_event_at": last_event_at,
                    "lag_minutes": None,
                    "status": "UNKNOWN"
                }

        conn.close()
        return {"status": "OK", "timestamp": now.isoformat(), "stores": store_status}

    except Exception as e:
        try:
            conn.close()
        except Exception:
            pass
        return JSONResponse(status_code=503, content={
            "status": "ERROR", "message": str(e),
            "timestamp": datetime.now(timezone.utc).isoformat(), "stores": {}
        })