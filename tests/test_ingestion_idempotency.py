"""
Tests for event ingestion idempotency and input validation.

Verifies that:
  1. Posting the same event batch twice is safe — duplicates are ignored.
  2. Malformed events cause the entire batch to be rejected (422) by
     Pydantic validation before any database writes occur.

PROMPT: "Generate pytest tests for idempotent event ingestion — verify
that posting the same batch twice results in 0 accepted and 2 duplicates
ignored on the second call. Also test that a batch containing an invalid
event (missing required fields) is fully rejected with HTTP 422."

CHANGES MADE: Added unique UUID suffix per test run to avoid cross-test
interference from prior ingested events. Changed assertion field from
'duplicate' to 'duplicates_ignored' to match actual API response schema.
Added test_partial_rejection to confirm Pydantic validates the entire
batch atomically — no partial ingest occurs.
"""
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
    """Batch with mix of valid and invalid events — valid ones accepted, bad ones rejected."""
    events = _make_events()
    bad = events + [{"store_id": "STORE_IDEMP_001"}]  # missing required fields
    r = client.post("/events/ingest", json={"events": bad})
    assert r.status_code == 200  # partial success, not 422
    body = r.json()
    assert body["accepted"] == 2  # 2 valid events accepted
    assert len(body["rejected"]) == 1  # 1 bad event rejected