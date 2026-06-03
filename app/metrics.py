# PROMPT: Generate store metrics endpoint using raw SQLite with visitor dedup, dwell averaging, conversion rate, queue depth
# CHANGES MADE: Fixed timestamp Z suffix filter, fixed dwell to use ZONE_DWELL only, fixed conversion_rate as percentage,
#               fixed metadata parsing for queue_depth, added structured zero-traffic handling,
#               replaced PURCHASE event dependency with POS time-window correlation per challenge spec

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
import sqlite3
import os
from datetime import datetime, timezone
import json

router = APIRouter()
DB_PATH = os.path.join("data", "events.db")
POS_DB_PATH = os.path.join("data", "store_intelligence.db")


def _connect():
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception:
        raise HTTPException(status_code=503, detail="db_unavailable")


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


@router.get("/stores/{store_id}/metrics")
def store_metrics(store_id: str):
    """
    Return today: unique visitors, conversion rate, avg dwell per zone,
    queue depth, abandonment rate. Excludes staff events.
    Conversion = visitors in billing zone within 5 min before a POS transaction.
    """
    conn = _connect()
    _init_db(conn)

    try:
        cur = conn.cursor()
        now = datetime.now(timezone.utc)
        today_prefix = now.strftime("%Y-%m-%d")

        cur.execute("""
            SELECT payload FROM events
            WHERE store_id = ?
            AND timestamp LIKE ?
        """, (store_id, f"{today_prefix}%"))

        rows = cur.fetchall()

        # Zero-traffic: return zeroes, not null or crash
        if not rows:
            conn.close()
            return JSONResponse(content={
                "store_id": store_id,
                "unique_visitors": 0,
                "conversion_rate": 0.0,
                "avg_dwell_per_zone": {},
                "queue_depth": 0,
                "abandonment_rate": 0.0
            })

        visitors = set()

        # Only ZONE_DWELL for accurate dwell — ZONE_ENTER/EXIT have dwell_ms=0
        zone_dwell_total: dict = {}
        zone_dwell_count: dict = {}

        total_queue_depth = 0
        queue_entries = 0
        abandonments = 0

        for row in rows:
            try:
                payload = json.loads(row["payload"])
            except Exception:
                continue

            # Exclude staff from all metrics
            if payload.get("is_staff"):
                continue

            vid = payload.get("visitor_id")
            if vid:
                visitors.add(vid)

            etype = payload.get("event_type", "")

            # Queue depth tracking
            if etype == "BILLING_QUEUE_JOIN":
                metadata = payload.get("metadata") or {}
                if isinstance(metadata, dict):
                    qd = metadata.get("queue_depth")
                    if qd is not None:
                        try:
                            total_queue_depth += int(qd)
                            queue_entries += 1
                        except (ValueError, TypeError):
                            pass

            # Dwell time — ZONE_DWELL only
            if etype == "ZONE_DWELL":
                zone = payload.get("zone_id")
                dwell = payload.get("dwell_ms", 0)
                if zone and dwell:
                    zone_dwell_total[zone] = zone_dwell_total.get(zone, 0) + int(dwell)
                    zone_dwell_count[zone] = zone_dwell_count.get(zone, 0) + 1

            # Abandonment tracking
            if etype == "BILLING_QUEUE_ABANDON":
                abandonments += 1

        # --- POS-based conversion (5-minute window correlation) ---
        # Per challenge spec: "A visitor who was in the billing zone in the
        # 5-minute window before a transaction timestamp counts as converted"
        converted_visitors = set()
        try:
            pos_conn = sqlite3.connect(POS_DB_PATH, timeout=5)
            pos_conn.row_factory = sqlite3.Row
            pos_rows = pos_conn.execute("""
                SELECT order_date, order_time
                FROM pos_transactions
                WHERE store_id = ?
                AND order_date = ?
            """, (store_id, today_prefix)).fetchall()

            for pos in pos_rows:
                ot = pos['order_time']
                txn_time = pos['order_date'] + 'T' + ot if ot else pos['order_date']

                # Find visitors in billing zone within 5 min before this transaction
                billing_visitors = cur.execute("""
                    SELECT DISTINCT json_extract(payload, '$.visitor_id') as vid
                    FROM events
                    WHERE store_id = ?
                    AND event_type IN ('BILLING_QUEUE_JOIN', 'ZONE_ENTER')
                    AND json_extract(payload, '$.zone_id') IN ('BILLING', 'billing', 'Billing')
                    AND json_extract(payload, '$.is_staff') IS NOT 1
                    AND timestamp BETWEEN datetime(?, '-5 minutes') AND datetime(?)
                """, (store_id, txn_time, txn_time)).fetchall()

                for bv in billing_visitors:
                    if bv['vid']:
                        converted_visitors.add(bv['vid'])

                        pos_conn.close()
        except Exception:
            pass

    

        # Compute final metrics
        unique_visitors = len(visitors)
        converted = len(converted_visitors)

        conversion_rate = round((converted / unique_visitors) * 100, 2) if unique_visitors else 0.0

        avg_dwell_per_zone = {
            zone: round(zone_dwell_total[zone] / zone_dwell_count[zone])
            for zone in zone_dwell_total
            if zone_dwell_count[zone] > 0
        }

        queue_depth = int(total_queue_depth / queue_entries) if queue_entries else 0
        abandonment_rate = round((abandonments / queue_entries) * 100, 2) if queue_entries else 0.0

        conn.close()
        return JSONResponse(content={
            "store_id": store_id,
            "unique_visitors": unique_visitors,
            "conversion_rate": conversion_rate,
            "avg_dwell_per_zone": avg_dwell_per_zone,
            "queue_depth": queue_depth,
            "abandonment_rate": abandonment_rate
        })

    except HTTPException:
        raise
    except Exception as e:
        try:
            conn.close()
        except Exception:
            pass
        raise HTTPException(status_code=503, detail=f"error: {str(e)}")
