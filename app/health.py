# PROMPT: Generate a health check endpoint using raw SQLite showing per-store last event and STALE_FEED detection
# CHANGES MADE: Replaced SQLAlchemy with raw SQLite to match rest of app, fixed MAX timestamp query, fixed lag calculation using total_seconds()

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


def _init_db(conn: sqlite3.Connection):
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS events (
            event_id TEXT PRIMARY KEY,
            store_id TEXT,
            camera_id TEXT,
            visitor_id TEXT,
            event_type TEXT,
            timestamp TEXT,
            payload TEXT
        )
    """)
    conn.commit()


@router.get("/health")
def health_check():
    """
    Service status, last event timestamp per store.
    Returns STALE_FEED warning if last event > 10 minutes ago.
    """
    conn = _connect()
    if conn is None:
        return JSONResponse(
            status_code=503,
            content={
                "status": "ERROR",
                "message": "db_unavailable",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "stores": {}
            }
        )

    try:
        _init_db(conn)
        cur = conn.cursor()

        # Get LATEST timestamp per store (not random via distinct)
        cur.execute("""
            SELECT store_id, MAX(timestamp) as last_event_at
            FROM events
            GROUP BY store_id
        """)
        rows = cur.fetchall()

        now = datetime.now(timezone.utc)
        store_status = {}

        for row in rows:
            store_id = row["store_id"]
            last_event_at = row["last_event_at"]

            try:
                # Handle both "Z" suffix and "+00:00" format
                ts_str = last_event_at.replace("Z", "+00:00")
                last_ts = datetime.fromisoformat(ts_str)
                if last_ts.tzinfo is None:
                    last_ts = last_ts.replace(tzinfo=timezone.utc)

                # Use total_seconds() not .seconds (which caps at 59)
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
        return {
            "status": "OK",
            "timestamp": now.isoformat(),
            "stores": store_status
        }

    except Exception as e:
        try:
            conn.close()
        except Exception:
            pass
        return JSONResponse(
            status_code=503,
            content={
                "status": "ERROR",
                "message": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "stores": {}
            }
        )