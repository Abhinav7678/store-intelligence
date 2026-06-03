"""
# PROMPT: Generate tests for schema validation using actual challenge event format
# and session reconstruction with entry/exit/zone_entered events.
# CHANGES MADE: Updated to use model_validate instead of parse_obj. Test events
# use actual format fields (id_token, store_code, event_timestamp).
"""
from app.schemas import Event
from app.sessions import reconstruct_sessions
from datetime import datetime, timedelta
import json


def test_schema_validation_entry():
    ev = {
        "event_type": "entry",
        "id_token": "ID_60001",
        "store_code": "store_1076",
        "camera_id": "cam1",
        "event_timestamp": "2026-03-08T18:10:05.120000",
        "is_staff": False,
        "gender_pred": "F",
        "age_pred": 28,
        "age_bucket": "25-34",
        "is_face_hidden": False,
        "group_id": None,
        "group_size": None,
    }
    e = Event.model_validate(ev)
    assert e.event_type == "entry"
    assert e.id_token == "ID_60001"
    assert e.get_store_id() == "store_1076"
    assert e.get_visitor_id() == "ID_60001"


def test_schema_validation_zone():
    ev = {
        "event_type": "zone_entered",
        "track_id": 101,
        "store_id": "ST1076",
        "camera_id": "CAM2",
        "zone_id": "PURPLLE_MUM_1076_Z01",
        "zone_name": "Left Shelf",
        "zone_type": "SHELF",
        "is_revenue_zone": "Yes",
        "event_time": "2026-03-08T18:10:45.280000",
        "zone_hotspot_x": 412.6,
        "zone_hotspot_y": 238.4,
        "gender": "F",
        "age": 28,
        "age_bucket": "25-34",
    }
    e = Event.model_validate(ev)
    assert e.event_type == "zone_entered"
    assert e.get_visitor_id() == "101"
    assert e.get_timestamp() == "2026-03-08T18:10:45.280000"


def test_reconstruct_sessions_reentry_and_exit():
    now = datetime.utcnow()
    rows = []
    # First session
    rows.append(json.dumps({"id_token": "V1", "event_type": "entry", "event_timestamp": (now - timedelta(minutes=40)).isoformat()}))
    rows.append(json.dumps({"track_id": 101, "event_type": "zone_entered", "zone_name": "Left Shelf", "event_time": (now - timedelta(minutes=39)).isoformat()}))
    rows.append(json.dumps({"id_token": "V1", "event_type": "exit", "event_timestamp": (now - timedelta(minutes=38)).isoformat()}))
    # Re-entry
    rows.append(json.dumps({"id_token": "V1", "event_type": "entry", "event_timestamp": (now - timedelta(minutes=30)).isoformat()}))
    rows.append(json.dumps({"track_id": 102, "event_type": "zone_entered", "zone_name": "Center Display", "event_time": (now - timedelta(minutes=29)).isoformat()}))

    sessions = reconstruct_sessions(rows)
    assert len(sessions) >= 2