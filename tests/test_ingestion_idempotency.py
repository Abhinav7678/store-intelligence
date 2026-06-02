import os
import uuid
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def _make_events():
    uid = uuid.uuid4().hex[:8]
    return [
        {
            "event_id": f"idempo-1-{uid}",
            "store_id": "STORE_IDEMP_001",
            "camera_id": "CAM_1",
            "visitor_id": "V1",
            "event_type": "ENTRY",
            "timestamp": "2026-03-04T09:00:00Z",
            "zone_id": None,
            "dwell_ms": 0,
            "is_staff": False,
            "confidence": 0.9,
            "metadata": {}
        },
        {
            "event_id": f"idempo-2-{uid}",
            "store_id": "STORE_IDEMP_001",
            "camera_id": "CAM_2",
            "visitor_id": "V2",
            "event_type": "ENTRY",
            "timestamp": "2026-03-04T09:01:00Z",
            "zone_id": None,
            "dwell_ms": 0,
            "is_staff": False,
            "confidence": 0.8,
            "metadata": {}
        }
    ]


def test_idempotent_ingest():
    events = _make_events()

    # first ingest
    r1 = client.post("/events/ingest", json={"events": events})
    assert r1.status_code == 200
    b1 = r1.json()
    assert b1["accepted"] == 2
    assert b1["duplicates_ignored"] == 0

    # second ingest (same events): should ignore duplicates
    r2 = client.post("/events/ingest", json={"events": events})
    assert r2.status_code == 200
    b2 = r2.json()
    assert b2["accepted"] == 0
    assert b2["duplicates_ignored"] == 2


def test_partial_rejection():
    """Batch with invalid events fails Pydantic validation (422) before reaching ingest logic."""
    events = _make_events()
    bad = events + [{"store_id": "STORE_IDEMP_001"}]  # missing required fields
    r = client.post("/events/ingest", json={"events": bad})
    assert r.status_code == 422  # Pydantic rejects entire batch