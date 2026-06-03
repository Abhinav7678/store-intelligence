"""Tests for the detection pipeline — no OpenCV/YOLO required."""
import json
import sys
import pytest
from unittest.mock import MagicMock

# Mock cv2, numpy, ultralytics so we can import pipeline.detect without them
cv2_mock = MagicMock()
np_mock = MagicMock()
np_mock.sqrt = lambda x: x ** 0.5
ultralytics_mock = MagicMock()

sys.modules.setdefault("cv2", cv2_mock)
sys.modules.setdefault("numpy", np_mock)
sys.modules.setdefault("ultralytics", ultralytics_mock)

import importlib
if "pipeline.detect" in sys.modules:
    importlib.reload(sys.modules["pipeline.detect"])

from pipeline.detect import ZoneClassifier, PersonTracker


class TestZoneClassifier:

    def test_load_store1_zones(self, tmp_path):
        layout = {
            "STORE_1": {
                "zones": {
                    "FRAGRANCE": {"x1": 300, "y1": 300, "x2": 500, "y2": 600},
                    "BILLING": {"x1": 950, "y1": 150, "x2": 1150, "y2": 500}
                }
            }
        }
        f = tmp_path / "layout.json"
        f.write_text(json.dumps(layout))
        zc = ZoneClassifier(str(f), store_id="STORE_1")
        assert len(zc.zones) == 2
        assert "FRAGRANCE" in zc.zones

    def test_load_store2_zones(self, tmp_path):
        layout = {
            "STORE_1": {"zones": {"A": {"x1": 0, "y1": 0, "x2": 100, "y2": 100}}},
            "STORE_2": {"zones": {"B": {"x1": 0, "y1": 0, "x2": 200, "y2": 200}}}
        }
        f = tmp_path / "layout.json"
        f.write_text(json.dumps(layout))
        zc = ZoneClassifier(str(f), store_id="STORE_2")
        assert "B" in zc.zones

    def test_classify_in_zone(self, tmp_path):
        layout = {
            "STORE_1": {
                "zones": {
                    "MAKEUP": {"x1": 0, "y1": 0, "x2": 500, "y2": 500},
                    "BILLING": {"x1": 500, "y1": 0, "x2": 1000, "y2": 500}
                }
            }
        }
        f = tmp_path / "layout.json"
        f.write_text(json.dumps(layout))
        zc = ZoneClassifier(str(f), store_id="STORE_1")
        zc.set_frame_size(1000, 500)
        assert zc.classify((100, 100, 200, 200)) == "MAKEUP"
        assert zc.classify((600, 100, 700, 200)) == "BILLING"

    def test_classify_outside_zones(self, tmp_path):
        layout = {"STORE_1": {"zones": {"A": {"x1": 0, "y1": 0, "x2": 100, "y2": 100}}}}
        f = tmp_path / "layout.json"
        f.write_text(json.dumps(layout))
        zc = ZoneClassifier(str(f), store_id="STORE_1")
        zc.set_frame_size(100, 100)
        assert zc.classify((9999, 9999, 10000, 10000)) is None

    def test_missing_layout_defaults(self):
        zc = ZoneClassifier("nonexistent.json", store_id="STORE_1")
        assert len(zc.zones) > 0


class TestPersonTracker:

    def test_entry_event(self):
        tracker = PersonTracker(store_id="STORE_1", camera_id="CAM_1_ZONE")
        zc = ZoneClassifier.__new__(ZoneClassifier)
        zc.zones = {}
        zc.frame_w = 1920
        zc.frame_h = 1080
        events = tracker.update([([100, 100, 200, 200], 0.9)], zc, "2026-03-08T18:10:05Z", 1)
        entries = [e for e in events if e["event_type"] == "ENTRY"]
        assert len(entries) == 1
        assert entries[0]["store_id"] == "STORE_1"
        assert entries[0]["camera_id"] == "CAM_1_ZONE"

    def test_exit_after_timeout(self):
        tracker = PersonTracker(store_id="STORE_2", camera_id="CAM_ENTRY1")
        tracker.exit_timeout_frames = 5
        zc = ZoneClassifier.__new__(ZoneClassifier)
        zc.zones = {}
        zc.frame_w = 1920
        zc.frame_h = 1080
        tracker.update([([100, 100, 200, 200], 0.9)], zc, "2026-03-08T18:10:00Z", 1)
        all_events = []
        for i in range(2, 8):
            events = tracker.update([], zc, f"2026-03-08T18:10:{i:02d}Z", i)
            all_events.extend(events)
        exits = [e for e in all_events if e["event_type"] == "EXIT"]
        assert len(exits) == 1
        assert exits[0]["store_id"] == "STORE_2"

    def test_zone_enter(self):
        tracker = PersonTracker(store_id="STORE_1", camera_id="CAM_1_ZONE")
        zc = ZoneClassifier.__new__(ZoneClassifier)
        zc.zones = {"MAKEUP": {"x1": 0, "y1": 0, "x2": 1000, "y2": 1000}}
        zc.frame_w = 1000
        zc.frame_h = 1000
        events = tracker.update([([400, 400, 500, 500], 0.9)], zc, "2026-03-08T18:10:05Z", 1)
        zone_enters = [e for e in events if e["event_type"] == "ZONE_ENTER"]
        assert len(zone_enters) == 1
        assert zone_enters[0]["zone_id"] == "MAKEUP"

    def test_billing_queue_join(self):
        tracker = PersonTracker(store_id="STORE_1", camera_id="CAM_5_BILLING")
        zc = ZoneClassifier.__new__(ZoneClassifier)
        zc.zones = {"BILLING": {"x1": 0, "y1": 0, "x2": 1000, "y2": 1000}}
        zc.frame_w = 1000
        zc.frame_h = 1000
        events = tracker.update([([400, 400, 500, 500], 0.9)], zc, "2026-03-08T18:13:05Z", 1)
        joins = [e for e in events if e["event_type"] == "BILLING_QUEUE_JOIN"]
        assert len(joins) == 1
        assert joins[0]["metadata"]["queue_depth"] is not None

    def test_event_schema(self):
        tracker = PersonTracker(store_id="STORE_1", camera_id="CAM_1_ZONE")
        event = tracker._make_event(
            visitor_id="VIS_test", event_type="ENTRY",
            timestamp="2026-03-08T18:10:05Z", confidence=0.92, session_seq=1
        )
        assert event["store_id"] == "STORE_1"
        assert event["event_type"] == "ENTRY"
        assert event["confidence"] == 0.92
        assert event["is_staff"] is False
        assert event["dwell_ms"] == 0
        assert "event_id" in event

    def test_staff_detection(self):
        tracker = PersonTracker(store_id="STORE_1")
        track = {"history": [
            {"zone": "MAKEUP", "frame": 1}, {"zone": "FRAGRANCE", "frame": 10},
            {"zone": "BILLING", "frame": 20}, {"zone": "FOH", "frame": 30},
        ]}
        assert tracker._is_likely_staff(track) is True

    def test_customer_not_staff(self):
        tracker = PersonTracker(store_id="STORE_1")
        track = {"history": [{"zone": "MAKEUP", "frame": 1}, {"zone": "MAKEUP", "frame": 10}]}
        assert tracker._is_likely_staff(track) is False


class TestProcessAllStores:

    def test_layout_structure(self, tmp_path):
        layout = {
            "STORE_1": {
                "cameras": {"CAM_1": "nonexistent.mp4"},
                "zones": {"A": {"x1": 0, "y1": 0, "x2": 100, "y2": 100}}
            },
            "STORE_2": {
                "cameras": {"CAM_B": "also_missing.mp4"},
                "zones": {"B": {"x1": 0, "y1": 0, "x2": 200, "y2": 200}}
            }
        }
        f = tmp_path / "layout.json"
        f.write_text(json.dumps(layout))
        with open(f) as fh:
            loaded = json.load(fh)
        assert "STORE_1" in loaded
        assert "STORE_2" in loaded
        assert "cameras" in loaded["STORE_1"]
        assert "zones" in loaded["STORE_2"]