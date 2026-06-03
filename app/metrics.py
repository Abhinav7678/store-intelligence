"""
# PROMPT: Rewrite metrics endpoint for actual challenge data: entry/exit with id_token,
# zone_entered/zone_exited with track_id, queue_completed/queue_abandoned, POS with DD-MM-YYYY dates
# CHANGES MADE: Compute dwell from zone_entered/zone_exited time pairs, queue depth from
# queue_position_at_join, abandonment from queue_abandoned events, POS correlation with actual CSV format.
"""
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


@router.get("/stores/{store_id}/metrics")
def store_metrics(store_id: str):
    """Metrics: unique visitors, conversion rate, avg dwell per zone,
    queue depth, abandonment rate. Excludes staff."""
    conn = _connect()

    try:
        cur = conn.cursor()
        now = datetime.now(timezone.utc)
        today_prefix = now.strftime("%Y-%m-%d")

        # Get all events for this store today
        cur.execute("""
            SELECT event_type, visitor_id, zone_id, zone_name, is_staff, timestamp, payload
            FROM events
            WHERE store_id = ? AND timestamp LIKE ?
        """, (store_id, f"{today_prefix}%"))
        rows = cur.fetchall()

        # Also try matching without date filter if no results (events may have different date)
        if not rows:
            cur.execute("""
                SELECT event_type, visitor_id, zone_id, zone_name, is_staff, timestamp, payload
                FROM events WHERE store_id = ?
            """, (store_id,))
            rows = cur.fetchall()

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
        zone_enter_times = {}  # (visitor_id, zone_id) -> timestamp
        zone_dwell_total = {}
        zone_dwell_count = {}
        total_queue_depth = 0
        queue_entries = 0
        abandonments = 0
        billing_visitors = set()

        for row in rows:
            event_type = row["event_type"]
            visitor_id = row["visitor_id"]
            is_staff = row["is_staff"]

            if is_staff:
                continue

            # Count unique visitors from entry events
            if event_type in ("entry", "ENTRY"):
                if visitor_id:
                    visitors.add(visitor_id)

            # Zone dwell: compute from zone_entered → zone_exited pairs
            if event_type in ("zone_entered", "ZONE_ENTER"):
                zone = row["zone_name"] or row["zone_id"] or ""
                ts = row["timestamp"]
                if visitor_id and zone:
                    zone_enter_times[(visitor_id, zone)] = ts

            if event_type in ("zone_exited", "ZONE_EXIT"):
                zone = row["zone_name"] or row["zone_id"] or ""
                ts = row["timestamp"]
                key = (visitor_id, zone)
                if key in zone_enter_times and ts:
                    try:
                        enter_dt = datetime.fromisoformat(zone_enter_times[key])
                        exit_dt = datetime.fromisoformat(ts)
                        dwell_ms = int((exit_dt - enter_dt).total_seconds() * 1000)
                        if dwell_ms > 0:
                            zone_dwell_total[zone] = zone_dwell_total.get(zone, 0) + dwell_ms
                            zone_dwell_count[zone] = zone_dwell_count.get(zone, 0) + 1
                    except Exception:
                        pass
                    del zone_enter_times[key]

            # Queue events
            if event_type in ("queue_completed", "queue_abandoned"):
                queue_entries += 1
                try:
                    payload = json.loads(row["payload"]) if row["payload"] else {}
                    qd = payload.get("queue_position_at_join")
                    if qd is not None:
                        total_queue_depth += int(qd)
                except Exception:
                    pass

                if event_type == "queue_abandoned":
                    abandonments += 1
                else:
                    if visitor_id:
                        billing_visitors.add(visitor_id)

        # If no entry events found, count all unique visitor_ids
        if not visitors:
            for row in rows:
                if not row["is_staff"] and row["visitor_id"]:
                    visitors.add(row["visitor_id"])

        # POS-based conversion
        converted_visitors = set()
        try:
            if os.path.exists(POS_DB_PATH):
                pos_conn = sqlite3.connect(POS_DB_PATH, timeout=5)
                pos_conn.row_factory = sqlite3.Row
                pos_rows = pos_conn.execute("""
                    SELECT DISTINCT order_id, order_date, order_time
                    FROM pos_transactions WHERE store_id = ?
                """, (store_id,)).fetchall()

                for pos in pos_rows:
                    try:
                        od = pos['order_date']
                        ot = pos['order_time']
                        # Handle DD-MM-YYYY format
                        if '-' in od and len(od.split('-')[0]) == 2:
                            parts = od.split('-')
                            od = f"{parts[2]}-{parts[1]}-{parts[0]}"
                        txn_time = f"{od}T{ot}" if ot else od

                        # Check for visitors in billing zone within 5 min before transaction
                        bv_rows = cur.execute("""
                            SELECT DISTINCT visitor_id FROM events
                            WHERE store_id = ?
                            AND event_type IN ('queue_completed', 'BILLING_QUEUE_JOIN')
                            AND is_staff = 0
                            AND timestamp BETWEEN datetime(?, '-5 minutes') AND datetime(?)
                        """, (store_id, txn_time, txn_time)).fetchall()
                        for bv in bv_rows:
                            if bv['visitor_id']:
                                converted_visitors.add(bv['visitor_id'])
                    except Exception:
                        pass
                pos_conn.close()
        except Exception:
            pass

        # Also count queue_completed visitors as converted
        converted_visitors.update(billing_visitors)

        unique_visitors = len(visitors)
        converted = len(converted_visitors)
        conversion_rate = round((converted / unique_visitors) * 100, 2) if unique_visitors else 0.0

        avg_dwell_per_zone = {
            zone: round(zone_dwell_total[zone] / zone_dwell_count[zone])
            for zone in zone_dwell_total if zone_dwell_count.get(zone, 0) > 0
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