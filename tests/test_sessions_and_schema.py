# PROMPT: Test schema validation and session reconstruction helper. / # CHANGES MADE: Added a few synthetic payload rows to simulate re-entry and exit semantics.

from app.schemas import Event, BoundingBox
from app.sessions import reconstruct_sessions
from datetime import datetime, timedelta
import json


def test_schema_validation():
    ev = {
        "event_id": "s-1",
        "store_id": "S1",
        "camera_id": "C1",
        "visitor_id": "V1",
        "event_type": "ENTRY",
        "timestamp": "2026-03-05T12:00:00Z",
        "zone_id": None,
        "dwell_ms": 0,
        "is_staff": False,
        "confidence": 0.7,
        "metadata": {}
    }
    e = Event.parse_obj(ev)
    assert e.event_id == "s-1"


def test_reconstruct_sessions_reentry_and_exit():
    now = datetime.utcnow()
    rows = []
    # first session
    rows.append(json.dumps({"visitor_id": "V1", "event_type": "ENTRY", "timestamp": (now - timedelta(minutes=40)).isoformat() + "Z"}))
    rows.append(json.dumps({"visitor_id": "V1", "event_type": "ZONE_ENTER", "zone_id": "A", "timestamp": (now - timedelta(minutes=39)).isoformat() + "Z"}))
    rows.append(json.dumps({"visitor_id": "V1", "event_type": "EXIT", "timestamp": (now - timedelta(minutes=38)).isoformat() + "Z"}))
    # re-entry within short time
    rows.append(json.dumps({"visitor_id": "V1", "event_type": "ENTRY", "timestamp": (now - timedelta(minutes=30)).isoformat() + "Z"}))
    rows.append(json.dumps({"visitor_id": "V1", "event_type": "ZONE_ENTER", "zone_id": "B", "timestamp": (now - timedelta(minutes=29)).isoformat() + "Z"}))

    sessions = reconstruct_sessions(rows)
    # Expect two closed sessions because of explicit EXIT then new ENTRY
    assert len(sessions) >= 2
