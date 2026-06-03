"""
Session reconstruction and purchase attribution module.

Reconstructs visitor sessions from raw event payloads stored in the database,
then correlates billing-zone presence with POS transaction timestamps to
determine whether a visitor made a purchase (funnel conversion).
"""
from typing import List, Dict, Any
import json
import sqlite3
import os
from datetime import datetime, timedelta


POS_DB_PATH = os.path.join("data", "store_intelligence.db")


def _load_pos_transactions(store_id: str, date_prefix: str) -> List[str]:
    """Load POS transaction timestamps for a store on a given date."""
    try:
        conn = sqlite3.connect(POS_DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT order_date, order_time
            FROM pos_transactions
            WHERE store_id = ?
            AND order_date = ?
        """, (store_id, date_prefix)).fetchall()
        conn.close()
        txn_times = []
        for r in rows:
            ot = r['order_time']
            ts = r['order_date'] + 'T' + ot if ot else r['order_date']
            txn_times.append(ts)
        return txn_times
    except Exception:
        return []


def _parse_ts(ts_str: str) -> datetime:
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            from datetime import timezone
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        from datetime import timezone
        return datetime.now(timezone.utc)


def reconstruct_sessions(payload_rows: List[str], store_id: str = None) -> List[Dict[str, Any]]:
    """Given DB payload JSON strings ordered by timestamp, return a list of session dicts.

    Each session: {"visitor_id": str, "events": [dict], "entered": bool,
                   "zone_visit": bool, "queued": bool, "purchased": bool,
                   "start_ts": datetime, "end_ts": datetime}

    Purchase is determined by POS time-window correlation:
    A visitor who was in the billing zone within 5 minutes before a POS transaction
    counts as a converted visitor.
    """
    sessions = []
    per_visitor = {}

    def close_session(vid):
        sess = per_visitor.pop(vid, None)
        if sess:
            sessions.append(sess)

    for payload_json in payload_rows:
        try:
            payload = json.loads(payload_json)
        except Exception:
            continue
        if payload.get("is_staff"):
            continue
        vid = payload.get("visitor_id")
        if not vid:
            continue
        ts_raw = payload.get("timestamp")
        ts = _parse_ts(ts_raw) if ts_raw else datetime.utcnow()

        sess = per_visitor.get(vid)
        if not sess:
            sess = {
                "visitor_id": vid,
                "events": [],
                "entered": False,
                "zone_visit": False,
                "queued": False,
                "purchased": False,
                "billing_timestamps": [],
                "start_ts": ts,
                "end_ts": ts,
            }
            per_visitor[vid] = sess

        # session gap heuristic
        if (ts - sess["end_ts"]) > timedelta(minutes=30):
            close_session(vid)
            sess = {
                "visitor_id": vid,
                "events": [],
                "entered": False,
                "zone_visit": False,
                "queued": False,
                "purchased": False,
                "billing_timestamps": [],
                "start_ts": ts,
                "end_ts": ts,
            }
            per_visitor[vid] = sess

        sess["events"].append(payload)
        sess["end_ts"] = ts

        et = payload.get("event_type")
        if et == "ENTRY":
            sess["entered"] = True
        if et == "ZONE_ENTER":
            sess["zone_visit"] = True
        if et == "BILLING_QUEUE_JOIN":
            sess["queued"] = True
            sess["billing_timestamps"].append(ts)
        
        if et == "ZONE_ENTER" and payload.get("zone_id", "").upper() in ("BILLING", "BILLING_COUNTER"):
            sess["billing_timestamps"].append(ts)
        if et == "EXIT":
            close_session(vid)

    # close remaining open sessions
    for vid in list(per_visitor.keys()):
        close_session(vid)

    # --- POS correlation: mark sessions as purchased ---
    if store_id and sessions:
        # Get date from first session
        first_date = sessions[0]["start_ts"].strftime("%Y-%m-%d")
        pos_times = _load_pos_transactions(store_id, first_date)
        pos_datetimes = [_parse_ts(t) for t in pos_times]

        for sess in sessions:
            if sess["purchased"]:
                continue
            for billing_ts in sess.get("billing_timestamps", []):
                for pos_ts in pos_datetimes:
                    # Visitor in billing zone within 5 min before POS transaction
                    diff = pos_ts - billing_ts
                    if timedelta(0) <= diff <= timedelta(minutes=5):
                        sess["purchased"] = True
                        break
                if sess["purchased"]:
                    break

    # Clean up internal field
    for sess in sessions:
        sess.pop("billing_timestamps", None)

    return sessions