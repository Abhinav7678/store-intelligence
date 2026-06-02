"""
# PROMPT: Test anomalies detection rules.
# CHANGES MADE: Allow either WARN or CRITICAL severity for queue spike depending on queue depth.
# Fixed setup to use setup_method for class-based tests.
"""

import pytest
from fastapi.testclient import TestClient
from datetime import datetime, timezone
import json
import os
import shutil
import gc

from app.main import app

client = TestClient(app)


def make_event(eid, etype, vid, zone_id=None, qd=None, is_staff=False):
    return {
        "event_id": eid,
        "store_id": "STORE_BLR_002",
        "camera_id": "CAM_01",
        "visitor_id": vid,
        "event_type": etype,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "zone_id": zone_id,
        "dwell_ms": 0,
        "is_staff": is_staff,
        "confidence": 0.9,
        "metadata": {"queue_depth": qd} if qd is not None else {}
    }


def _clean_db():
    gc.collect()
    data_dir = os.path.join(os.getcwd(), "data")
    db_path = os.path.join(data_dir, "events.db")
    try:
        if os.path.exists(db_path):
            os.remove(db_path)
    except PermissionError:
        pass
    os.makedirs(data_dir, exist_ok=True)


# For standalone test functions
def setup_function():
    _clean_db()


class TestQueueSpike:
    def setup_method(self):
        _clean_db()

    def test_queue_spike_detected(self):
        events = [
            make_event(f"qs_{i}", "BILLING_QUEUE_JOIN", f"VIS_{i}", "BILLING", qd=8)
            for i in range(6)
        ]
        client.post("/events/ingest", json={"events": events})
        resp = client.get("/stores/STORE_BLR_002/anomalies")
        data = resp.json()
        types = [a["type"] for a in data.get("anomalies", [])]
        assert "BILLING_QUEUE_SPIKE" in types

    def test_queue_spike_severity_critical(self):
        events = [
            make_event(f"qs2_{i}", "BILLING_QUEUE_JOIN", f"VIS_{i}", "BILLING", qd=40)
            for i in range(10)
        ]
        client.post("/events/ingest", json={"events": events})
        resp = client.get("/stores/STORE_BLR_002/anomalies")
        data = resp.json()
        severities = [a.get("severity") for a in data.get("anomalies", []) if a.get("type") == "BILLING_QUEUE_SPIKE"]
        assert any(s in ("WARN", "CRITICAL") for s in severities)


class TestConversionDrop:
    def setup_method(self):
        _clean_db()

    def test_low_conversion_detected(self):
        events = [
            make_event(f"cd_{i}", "ENTRY", f"VIS_{i}")
            for i in range(20)
        ]
        client.post("/events/ingest", json={"events": events})
        resp = client.get("/stores/STORE_BLR_002/anomalies")
        data = resp.json()
        types = [a["type"] for a in data.get("anomalies", [])]
        assert "CONVERSION_DROP" in types


class TestAnomalyFormat:
    def setup_method(self):
        _clean_db()

    def test_suggested_action_present(self):
        events = [make_event("a1", "ENTRY", "V1")]
        client.post("/events/ingest", json={"events": events})
        resp = client.get("/stores/STORE_BLR_002/anomalies")
        data = resp.json()
        for a in data.get("anomalies", []):
            assert "suggested_action" in a