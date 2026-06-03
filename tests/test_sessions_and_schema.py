"""
Tests for Pydantic schema validation and session reconstruction logic.

Verifies that:
  1. The Event schema correctly validates a well-formed event payload.
  2. Session reconstruction properly handles re-entry and exit semantics:
     an explicit EXIT followed by a new ENTRY creates separate sessions,
     even for the same visitor_id.

PROMPT: "Generate tests for schema validation using Pydantic Event model
and session reconstruction. Test that a valid event payload passes
validation. Test that a visitor who exits and re-enters produces two
separate sessions — verify the reconstruct_sessions helper handles
EXIT → ENTRY boundaries correctly."

CHANGES MADE: Added synthetic payload rows to simulate a full visitor
journey: ENTRY → ZONE_ENTER → EXIT, then a re-entry with ENTRY →
ZONE_ENTER. Verified that reconstruct_sessions splits these into at
least 2 sessions based on the explicit EXIT event boundary. Used
timedelta offsets to ensure timestamps are ordered and realistic.
"""
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
