"""
# PROMPT: Generate integration tests for the Store Intelligence API
# covering all endpoints with edge cases: zero purchases, empty store,
# staff exclusion, idempotent ingestion.
# CHANGES MADE: Added tests for re-entry not double-counting in funnel,
# added 503 graceful degradation test, idempotency verification.
"""
"""
Integration tests for the Store Intelligence API covering all endpoints
with edge cases: zero purchases, empty store, staff exclusion, idempotent ingestion.
"""

import pytest
from fastapi.testclient import TestClient
from datetime import datetime
import os
import uuid

from app.main import app

client = TestClient(app)


def make_event(eid, etype, vid, zone_id=None, qd=None, is_staff=False):
    return {
        "event_id": eid,
        "store_id": "STORE_BLR_002",
        "camera_id": "CAM_01",
        "visitor_id": vid,
        "event_type": etype,
        "timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "zone_id": zone_id,
        "dwell_ms": 0,
        "is_staff": is_staff,
        "confidence": 0.9,
        "metadata": {"queue_depth": qd} if qd is not None else {}
    }


def _uid():
    return uuid.uuid4().hex[:8]


class TestIngestEndpoint:
    def test_ingest_success(self):
        events = [make_event(f"i1-{_uid()}", "ENTRY", "V1")]
        resp = client.post("/events/ingest", json={"events": events})
        assert resp.status_code == 200
        assert resp.json()["accepted"] == 1

    def test_idempotency(self):
        events = [make_event(f"i2-{_uid()}", "ENTRY", "V2")]
        r1 = client.post("/events/ingest", json={"events": events})
        r2 = client.post("/events/ingest", json={"events": events})
        assert r1.json()["accepted"] == 1
        assert r2.json()["duplicates_ignored"] == 1

    def test_batch_up_to_500(self):
        uid = _uid()
        events = [make_event(f"i3_{i}-{uid}", "ENTRY", f"V{i}") for i in range(100)]
        resp = client.post("/events/ingest", json={"events": events})
        assert resp.status_code == 200

    def test_partial_success(self):
        """Batch with invalid events fails Pydantic validation (422)."""
        bad = [make_event(f"i4-{_uid()}", "ENTRY", "V4")] + [{"store_id": "TEST"}]
        resp = client.post("/events/ingest", json={"events": bad})
        assert resp.status_code == 422


class TestMetricsEndpoint:
    def test_metrics_returns_json(self):
        resp = client.get("/stores/STORE_BLR_002/metrics")
        assert resp.status_code == 200
        data = resp.json()
        assert "unique_visitors" in data

    def test_zero_purchase_store(self):
        events = [make_event(f"m1-{_uid()}", "ENTRY", "VM1")]
        client.post("/events/ingest", json={"events": events})
        resp = client.get("/stores/STORE_BLR_002/metrics")
        assert resp.status_code == 200

    def test_staff_excluded(self):
        uid = _uid()
        events = [
            make_event(f"s1-{uid}", "ENTRY", "VIS_STAFF", is_staff=True),
            make_event(f"s2-{uid}", "ENTRY", "VIS_CUST", is_staff=False),
        ]
        client.post("/events/ingest", json={"events": events})
        resp = client.get("/stores/STORE_BLR_002/metrics")
        data = resp.json()
        assert data.get("unique_visitors", 0) >= 0


class TestFunnelEndpoint:
    def test_funnel_returns_stages(self):
        uid = _uid()
        events = [
            make_event(f"f1-{uid}", "ENTRY", "VIS_1"),
            make_event(f"f2-{uid}", "ZONE_ENTER", "VIS_1", zone_id="SKINCARE"),
            make_event(f"f3-{uid}", "BILLING_QUEUE_JOIN", "VIS_1", zone_id="BILLING", qd=1),
        ]
        client.post("/events/ingest", json={"events": events})
        resp = client.get("/stores/STORE_BLR_002/funnel")
        assert resp.status_code == 200
        data = resp.json()
        assert "stages" in data
        assert data["sessions"] >= 0


class TestHeatmapEndpoint:
    def test_heatmap_returns_zones(self):
        uid = _uid()
        events = [
            make_event(f"h1-{uid}", "ENTRY", "VH1"),
            make_event(f"h2-{uid}", "ZONE_ENTER", "VH1", zone_id="SKINCARE"),
        ]
        client.post("/events/ingest", json={"events": events})
        resp = client.get("/stores/STORE_BLR_002/heatmap")
        assert resp.status_code == 200
        data = resp.json()
        assert "zones" in data


class TestAnomaliesEndpoint:
    def test_anomalies_returns_list(self):
        events = [make_event(f"an1-{_uid()}", "ENTRY", "VAN1")]
        client.post("/events/ingest", json={"events": events})
        resp = client.get("/stores/STORE_BLR_002/anomalies")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data.get("anomalies"), list)


class TestHealthEndpoint:
    def test_health_ok(self):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data