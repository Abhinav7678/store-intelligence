"""
# PROMPT: Generate anomaly detection endpoint with queue spike, dead zone, and conversion drop detection
# CHANGES MADE: Query actual DB columns instead of parsing payload JSON for event_type/visitor_id.
# Added per-zone dead zone detection, 7-day baseline for conversion drop, severity levels.
# Fixed timestamp format to match ingestion storage format (space-separated, no T/Z).
"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
import sqlite3
import os
from datetime import datetime, timedelta, timezone
import json

router = APIRouter()
DB_PATH = os.path.join("data", "events.db")
LAYOUT_PATH = os.path.join("data", "store_layout.json")


def _connect():
    try:
        return sqlite3.connect(DB_PATH, timeout=5)
    except Exception:
        raise FileNotFoundError("DB not available")


def _load_known_zones(store_id: str):
    try:
        with open(LAYOUT_PATH, "r") as f:
            layout = json.load(f)
        if isinstance(layout, dict):
            store_layout = layout.get(store_id, layout)
            if isinstance(store_layout, dict):
                zones = list(store_layout.get("zones", {}).keys())
                if zones:
                    return zones
        if isinstance(layout, list):
            return [z.get("zone_id") or z.get("name") for z in layout if z.get("zone_id") or z.get("name")]
    except Exception:
        pass
    return ["SKINCARE", "SNACKS", "BEVERAGES", "ELECTRONICS", "BILLING"]


@router.get("/stores/{store_id}/anomalies")
def store_anomalies(store_id: str):
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

        now = datetime.now(timezone.utc)
        # MATCH ingestion format: "2026-06-02 15:30:00" (no T, no Z)
        window_start = (now - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")

        cur.execute(
            "SELECT event_type, visitor_id, payload FROM events WHERE store_id = ? AND timestamp >= ? ORDER BY timestamp",
            (store_id, window_start),
        )
        rows = cur.fetchall()

        queue_depths = []
        zone_visits = {}
        visitors = set()
        purchases = 0

        for event_type, visitor_id, payload_json in rows:
            # Parse payload for metadata and is_staff
            metadata = {}
            is_staff = False
            zone_id = None
            try:
                payload = json.loads(payload_json) if payload_json else {}
                metadata = payload.get("metadata") or {}
                is_staff = payload.get("is_staff", False)
                zone_id = payload.get("zone_id")
                if not event_type:
                    event_type = payload.get("event_type", "")
                if not visitor_id:
                    visitor_id = payload.get("visitor_id")
            except Exception:
                pass

            if is_staff:
                continue

            if visitor_id:
                visitors.add(visitor_id)

            event_type = event_type or ""

            # Queue depth from BILLING_QUEUE_JOIN
            if event_type == "BILLING_QUEUE_JOIN":
                qd = metadata.get("queue_depth") if isinstance(metadata, dict) else None
                if qd is not None:
                    try:
                        queue_depths.append(int(qd))
                    except (ValueError, TypeError):
                        pass

            # Zone visits from ZONE_ENTER
            if event_type == "ZONE_ENTER":
                if zone_id:
                    zone_visits[zone_id] = zone_visits.get(zone_id, 0) + 1

            
        total_visitors = len(visitors)
        anomalies = []

        # ── 1. Queue spike: avg queue depth > 5 ──
        if queue_depths:
            avg_q = sum(queue_depths) / len(queue_depths)
            if avg_q > 5:
                anomalies.append({
                    "type": "BILLING_QUEUE_SPIKE",
                    "severity": "WARN" if avg_q <= 15 else "CRITICAL",
                    "detail": f"avg_queue={avg_q:.1f}",
                    "suggested_action": "Open an additional billing register or reassign staff to checkout",
                })

        # ── 2. Dead zone: per-zone detection ──
        known_zones = _load_known_zones(store_id)
        for zone in known_zones:
            zone_upper = zone.upper()
            visited = any(z.upper() == zone_upper for z in zone_visits)
            if not visited:
                anomalies.append({
                    "type": "DEAD_ZONE",
                    "severity": "INFO",
                    "zone": zone,
                    "detail": f"zone={zone} had 0 visits in last 30m",
                    "suggested_action": f"Check camera feed for {zone}, inspect floor placement or signage",
                })

        # ── 3. Conversion drop: compare to 7-day baseline ──
        conversion_now = (purchases / total_visitors) if total_visitors else 0.0

        baseline_start = (now - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
        baseline_end = (now - timedelta(days=6)).strftime("%Y-%m-%d %H:%M:%S")
        cur.execute(
            "SELECT event_type, visitor_id, payload FROM events WHERE store_id = ? AND timestamp >= ? AND timestamp < ?",
            (store_id, baseline_start, baseline_end),
        )
        baseline_rows = cur.fetchall()

        baseline_visitors = set()
        baseline_purchases = 0
        for event_type_b, visitor_id_b, payload_json_b in baseline_rows:
            try:
                p = json.loads(payload_json_b) if payload_json_b else {}
                if p.get("is_staff"):
                    continue
            except Exception:
                pass
            if visitor_id_b:
                baseline_visitors.add(visitor_id_b)
            if (event_type_b or "") == "BILLING_PURCHASE":
                baseline_purchases += 1

        baseline_conv = (baseline_purchases / len(baseline_visitors)) if baseline_visitors else 0.0

        if baseline_conv and conversion_now < (baseline_conv * 0.6):
            anomalies.append({
                "type": "CONVERSION_DROP",
                "severity": "WARN",
                "detail": f"now={conversion_now:.3f} baseline={baseline_conv:.3f}",
                "suggested_action": "Investigate promotion, staffing, or POS issues",
            })
        elif not baseline_visitors and total_visitors >= 10 and purchases == 0:
            anomalies.append({
                "type": "CONVERSION_DROP",
                "severity": "WARN",
                "detail": f"now={conversion_now:.3f} baseline=none",
                "suggested_action": "Investigate promotions, staff, or POS",
            })

        return JSONResponse(content={"store_id": store_id, "anomalies": anomalies})

    finally:
        conn.close()