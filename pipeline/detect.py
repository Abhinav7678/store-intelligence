"""
Detection pipeline: process CCTV clips and emit structured events.
Uses YOLOv8 for person detection, distance-based tracking, and coordinate-based
zone classification from store_layout.json.
"""

import json
import uuid
import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

try:
    import cv2
    import numpy as np
    from ultralytics import YOLO
except ImportError:
    print("Warning: OpenCV or YOLOv8 not installed. Install with: pip install -r requirements-cv.txt")
    sys.exit(1)


class ZoneClassifier:
    """Classify detected persons into zones based on bounding box center
    and pixel coordinates from store_layout.json."""

    def __init__(self, layout_path="data/store_layout.json", store_id=None):
        self.zones = {}
        self.entry_zone = None
        self.frame_w = 1920
        self.frame_h = 1080

        try:
            with open(layout_path) as f:
                layout = json.load(f)

            if store_id and store_id in layout:
                store_layout = layout[store_id]
                self.zones = store_layout.get("zones", {})
                self.entry_zone = store_layout.get("entry_zone", None)
            elif "zones" in layout:
                # Legacy flat format
                self.zones = layout["zones"]
            else:
                # Try first store
                first_key = next(iter(layout), None)
                if first_key and isinstance(layout[first_key], dict):
                    self.zones = layout[first_key].get("zones", {})

            print(f"  Loaded {len(self.zones)} zones for {store_id} from {layout_path}")
        except FileNotFoundError:
            print(f"  ⚠️ {layout_path} not found — using default zones")
            self.zones = {
                "ENTRY": {"x1": 0, "y1": 0, "x2": 400, "y2": 300},
                "SKINCARE": {"x1": 400, "y1": 0, "x2": 800, "y2": 400},
                "MAKEUP": {"x1": 800, "y1": 0, "x2": 1200, "y2": 400},
                "BILLING": {"x1": 1000, "y1": 400, "x2": 1300, "y2": 700},
                "FOH": {"x1": 400, "y1": 300, "x2": 800, "y2": 500}
            }

    def set_frame_size(self, frame_w, frame_h):
        self.frame_w = frame_w
        self.frame_h = frame_h

    def classify(self, bbox):
        try:
            x1, y1, x2, y2 = bbox
            cx = (x1 + x2) / 2
            cy = (y1 + y2) / 2

            layout_w = max(z.get("x2", 0) for z in self.zones.values()) if self.zones else 1300
            layout_h = max(z.get("y2", 0) for z in self.zones.values()) if self.zones else 700

            scale_x = self.frame_w / layout_w if layout_w else 1
            scale_y = self.frame_h / layout_h if layout_h else 1

            for zone_id, coords in self.zones.items():
                zx1 = coords["x1"] * scale_x
                zy1 = coords["y1"] * scale_y
                zx2 = coords["x2"] * scale_x
                zy2 = coords["y2"] * scale_y

                if zx1 <= cx <= zx2 and zy1 <= cy <= zy2:
                    return zone_id

            return None
        except Exception:
            return None


class PersonTracker:
    """Track persons across frames using bounding box distance."""

    def __init__(self, store_id="STORE_1", camera_id="CAM_1"):
        self.active_tracks = {}
        self.exited_tracks = {}
        self.next_track_id = 0
        self.store_id = store_id
        self.camera_id = camera_id
        self.match_distance = 200
        self.exit_timeout_frames = 90
        self.dwell_interval_ms = 30000
        self.reentry_window_frames = 450
        self.staff_zone_threshold = 3

    def _is_likely_staff(self, track):
        zones_visited = set()
        timestamps = []
        for h in track.get("history", []):
            if h.get("zone"):
                zones_visited.add(h["zone"])
            if h.get("frame") is not None:
                timestamps.append(h["frame"])

        if len(zones_visited) >= self.staff_zone_threshold:
            return True

        if timestamps and len(timestamps) > 50:
            span = max(timestamps) - min(timestamps)
            if span > 1800:
                return True

        return False

    def _check_reentry(self, bbox):
        x1, y1, x2, y2 = bbox
        cx = (x1 + x2) / 2
        cy = (y1 + y2) / 2

        for track_id, track in self.exited_tracks.items():
            if track.get("last_bbox") is None:
                continue
            lx1, ly1, lx2, ly2 = track["last_bbox"]
            lcx = (lx1 + lx2) / 2
            lcy = (ly1 + ly2) / 2
            dist = np.sqrt((cx - lcx) ** 2 + (cy - lcy) ** 2)
            if dist < self.match_distance * 2:
                return track_id, track
        return None, None

    def update(self, detections, zone_classifier, timestamp, frame_idx):
        events = []
        matched_tracks = set()

        for det in detections:
            try:
                bbox, conf = det
                x1, y1, x2, y2 = bbox
                cx = (x1 + x2) / 2
                cy = (y1 + y2) / 2
            except Exception:
                continue

            closest_track_id = None
            min_dist = self.match_distance

            for track_id, track in self.active_tracks.items():
                if track.get("last_bbox") is None:
                    continue
                lx1, ly1, lx2, ly2 = track["last_bbox"]
                dist = np.sqrt((cx - (lx1 + lx2) / 2) ** 2 +
                               (cy - (ly1 + ly2) / 2) ** 2)
                if dist < min_dist:
                    min_dist = dist
                    closest_track_id = track_id

            if closest_track_id is not None:
                track_id = closest_track_id
                matched_tracks.add(track_id)
            else:
                reentry_id, reentry_track = self._check_reentry(bbox)

                if reentry_track is not None:
                    track_id = self.next_track_id
                    self.next_track_id += 1
                    self.active_tracks[track_id] = {
                        "visitor_id": reentry_track["visitor_id"],
                        "history": reentry_track.get("history", []),
                        "last_bbox": None,
                        "last_zone": None,
                        "entry_frame": frame_idx,
                        "last_dwell_frame": {},
                        "frames_unseen": 0,
                        "session_seq": reentry_track.get("session_seq", 0),
                    }
                    del self.exited_tracks[reentry_id]
                    matched_tracks.add(track_id)

                    events.append(self._make_event(
                        visitor_id=reentry_track["visitor_id"],
                        event_type="REENTRY",
                        timestamp=timestamp,
                        confidence=float(conf),
                        session_seq=self.active_tracks[track_id]["session_seq"]
                    ))
                    self.active_tracks[track_id]["session_seq"] += 1
                else:
                    track_id = self.next_track_id
                    self.next_track_id += 1
                    visitor_id = f"VIS_{uuid.uuid4().hex[:8]}"
                    self.active_tracks[track_id] = {
                        "visitor_id": visitor_id,
                        "history": [],
                        "last_bbox": None,
                        "last_zone": None,
                        "entry_frame": frame_idx,
                        "last_dwell_frame": {},
                        "frames_unseen": 0,
                        "session_seq": 1,
                    }
                    matched_tracks.add(track_id)

                    events.append(self._make_event(
                        visitor_id=visitor_id,
                        event_type="ENTRY",
                        timestamp=timestamp,
                        confidence=float(conf),
                        session_seq=1
                    ))
                    self.active_tracks[track_id]["session_seq"] += 1

            track = self.active_tracks[track_id]
            track["last_bbox"] = bbox
            track["frames_unseen"] = 0

            current_zone = zone_classifier.classify(bbox)
            if current_zone and current_zone != "ENTRY":
                if track["last_zone"] != current_zone:
                    if track["last_zone"] and track["last_zone"] != "ENTRY":
                        events.append(self._make_event(
                            visitor_id=track["visitor_id"],
                            event_type="ZONE_EXIT",
                            timestamp=timestamp,
                            zone_id=track["last_zone"],
                            confidence=float(conf),
                            session_seq=track["session_seq"]
                        ))
                        track["session_seq"] += 1

                        if track["last_zone"] == "BILLING":
                            events.append(self._make_event(
                                visitor_id=track["visitor_id"],
                                event_type="BILLING_QUEUE_ABANDON",
                                timestamp=timestamp,
                                zone_id="BILLING",
                                confidence=float(conf),
                                session_seq=track["session_seq"]
                            ))
                            track["session_seq"] += 1

                    events.append(self._make_event(
                        visitor_id=track["visitor_id"],
                        event_type="ZONE_ENTER",
                        timestamp=timestamp,
                        zone_id=current_zone,
                        confidence=float(conf),
                        sku_zone=current_zone,
                        session_seq=track["session_seq"]
                    ))
                    track["session_seq"] += 1
                    track["last_dwell_frame"][current_zone] = frame_idx

                    if current_zone == "BILLING":
                        billing_count = sum(
                            1 for t in self.active_tracks.values()
                            if t.get("last_zone") == "BILLING"
                        )
                        events.append(self._make_event(
                            visitor_id=track["visitor_id"],
                            event_type="BILLING_QUEUE_JOIN",
                            timestamp=timestamp,
                            zone_id="BILLING",
                            confidence=float(conf),
                            queue_depth=billing_count,
                            session_seq=track["session_seq"]
                        ))
                        track["session_seq"] += 1
                else:
                    last_dwell = track["last_dwell_frame"].get(current_zone, track["entry_frame"])
                    frames_in_zone = frame_idx - last_dwell
                    fps = 15
                    ms_in_zone = int(frames_in_zone / fps * 1000)

                    if ms_in_zone >= self.dwell_interval_ms:
                        events.append(self._make_event(
                            visitor_id=track["visitor_id"],
                            event_type="ZONE_DWELL",
                            timestamp=timestamp,
                            zone_id=current_zone,
                            dwell_ms=ms_in_zone,
                            confidence=float(conf),
                            sku_zone=current_zone,
                            session_seq=track["session_seq"]
                        ))
                        track["session_seq"] += 1
                        track["last_dwell_frame"][current_zone] = frame_idx

                track["last_zone"] = current_zone

            track["history"].append({"zone": current_zone, "frame": frame_idx})

        for track_id in list(self.active_tracks.keys()):
            if track_id not in matched_tracks:
                self.active_tracks[track_id]["frames_unseen"] += 1

                if self.active_tracks[track_id]["frames_unseen"] > self.exit_timeout_frames:
                    track = self.active_tracks[track_id]
                    is_staff = self._is_likely_staff(track)

                    events.append(self._make_event(
                        visitor_id=track["visitor_id"],
                        event_type="EXIT",
                        timestamp=timestamp,
                        confidence=0.85,
                        is_staff=is_staff,
                        session_seq=track["session_seq"]
                    ))

                    self.exited_tracks[track_id] = {
                        "visitor_id": track["visitor_id"],
                        "last_bbox": track["last_bbox"],
                        "exit_frame": frame_idx,
                        "history": track["history"],
                        "session_seq": track["session_seq"] + 1
                    }
                    del self.active_tracks[track_id]

        for track_id in list(self.exited_tracks.keys()):
            if frame_idx - self.exited_tracks[track_id].get("exit_frame", 0) > self.reentry_window_frames:
                del self.exited_tracks[track_id]

        return events

    def _make_event(self, visitor_id, event_type, timestamp, zone_id=None,
                    dwell_ms=0, confidence=0.9, is_staff=False,
                    queue_depth=None, sku_zone=None, session_seq=1):
        return {
            "event_id": str(uuid.uuid4()),
            "store_id": self.store_id,
            "camera_id": self.camera_id,
            "visitor_id": visitor_id,
            "event_type": event_type,
            "timestamp": timestamp,
            "zone_id": zone_id,
            "dwell_ms": dwell_ms,
            "is_staff": is_staff,
            "confidence": round(confidence, 2),
            "metadata": {
                "queue_depth": queue_depth,
                "sku_zone": sku_zone,
                "session_seq": session_seq
            }
        }


def process_clip(video_path, camera_id, store_id, layout_path, clip_start_time=None):
    """Process a CCTV clip and emit structured events."""
    print(f"\n🎬 Processing: {video_path}")
    print(f"   Camera: {camera_id} | Store: {store_id}")

    model = YOLO("yolov8n.pt")
    print("   ✅ YOLOv8n loaded")

    zone_classifier = ZoneClassifier(layout_path, store_id=store_id)
    tracker = PersonTracker(store_id=store_id, camera_id=camera_id)

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print(f"   ❌ Could not open video: {video_path}")
        return []

    fps = cap.get(cv2.CAP_PROP_FPS) or 15
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    frame_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    frame_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    zone_classifier.set_frame_size(frame_w, frame_h)

    if clip_start_time is None:
        clip_start_time = datetime.now(timezone.utc)

    print(f"   Resolution: {frame_w}x{frame_h} | FPS: {fps:.1f} | Frames: {total_frames}")

    frame_idx = 0
    all_events = []
    process_every_n = 2

    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break

        frame_idx += 1
        if frame_idx % process_every_n != 0:
            continue

        timestamp = (clip_start_time + timedelta(seconds=frame_idx / fps)).isoformat()
        try:
            results = model(frame, classes=[0], conf=0.50, verbose=False)
            detections = []
            for r in results:
                for box in r.boxes:
                    bbox = box.xyxy[0].cpu().numpy().tolist()
                    conf = float(box.conf[0])
                    detections.append((bbox, conf))

            events = tracker.update(detections, zone_classifier, timestamp, frame_idx)
            all_events.extend(events)
        except Exception as e:
            print(f"   ⚠️ Frame {frame_idx} error: {e}")
            continue

        if frame_idx % 300 == 0:
            pct = round(frame_idx / total_frames * 100, 1) if total_frames else 0
            print(f"   📊 Frame {frame_idx}/{total_frames} ({pct}%) | Events: {len(all_events)}")

    cap.release()

    final_ts = (clip_start_time + timedelta(seconds=frame_idx / fps)).isoformat()
    for track_id, track in list(tracker.active_tracks.items()):
        is_staff = tracker._is_likely_staff(track)
        all_events.append(tracker._make_event(
            visitor_id=track["visitor_id"],
            event_type="EXIT",
            timestamp=final_ts,
            confidence=0.80,
            is_staff=is_staff,
            session_seq=track.get("session_seq", 1)
        ))

    print(f"\n   ✅ Done: {len(all_events)} events from {frame_idx} frames")
    return all_events


def process_all_stores(layout_path="data/store_layout.json", start_time=None):
    """Process all clips from all stores defined in store_layout.json."""
    with open(layout_path) as f:
        layout = json.load(f)

    if start_time is None:
        start_time = datetime.now(timezone.utc)

    all_events = []

    for store_id, store_config in layout.items():
        cameras = store_config.get("cameras", {})
        print(f"\n{'='*60}")
        print(f"🏪 Store: {store_id} | Cameras: {len(cameras)}")
        print(f"{'='*60}")

        for camera_id, video_path in cameras.items():
            if not Path(video_path).exists():
                print(f"   ⚠️ Video not found: {video_path} — skipping")
                continue

            events = process_clip(
                video_path=video_path,
                camera_id=camera_id,
                store_id=store_id,
                layout_path=layout_path,
                clip_start_time=start_time
            )

            # Save per-camera output
            output_path = f"data/processed/{store_id}_{camera_id}_events.jsonl"
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "w") as f:
                for event in events:
                    f.write(json.dumps(event) + "\n")
            print(f"   📁 Saved → {output_path}")

            all_events.extend(events)

    # Save combined output
    combined_path = "data/processed/all_events.jsonl"
    with open(combined_path, "w") as f:
        for event in all_events:
            f.write(json.dumps(event) + "\n")

    print(f"\n{'='*60}")
    print(f"📊 TOTAL: {len(all_events)} events across all stores")
    print(f"📁 Combined → {combined_path}")

    # Summary
    event_types = {}
    for e in all_events:
        t = e["event_type"]
        event_types[t] = event_types.get(t, 0) + 1
    print(f"\n📊 Event Summary:")
    for t, count in sorted(event_types.items()):
        print(f"   {t}: {count}")

    stores = set(e["store_id"] for e in all_events)
    for s in stores:
        visitors = set(e["visitor_id"] for e in all_events if e["store_id"] == s)
        staff = set(e["visitor_id"] for e in all_events if e["store_id"] == s and e.get("is_staff"))
        print(f"\n   {s}: {len(visitors)} visitors, {len(staff)} staff, {len(visitors) - len(staff)} customers")

    return all_events


def main():
    parser = argparse.ArgumentParser(description="CCTV Detection Pipeline")
    parser.add_argument("--clip", default=None, help="Path to single video clip")
    parser.add_argument("--camera_id", default="CAM_1", help="Camera ID")
    parser.add_argument("--store_id", default="STORE_1", help="Store ID")
    parser.add_argument("--layout", default="data/store_layout.json", help="Store layout JSON")
    parser.add_argument("--output", default=None, help="Output JSONL file path")
    parser.add_argument("--start_time", default=None, help="Clip start time ISO format")
    parser.add_argument("--all", action="store_true", help="Process all stores and cameras")
    args = parser.parse_args()

    clip_start = None
    if args.start_time:
        try:
            clip_start = datetime.fromisoformat(args.start_time.replace("Z", "+00:00"))
        except Exception:
            clip_start = None

    if args.all or args.clip is None:
        # Process all stores
        process_all_stores(layout_path=args.layout, start_time=clip_start)
    else:
        # Process single clip
        events = process_clip(
            video_path=args.clip,
            camera_id=args.camera_id,
            store_id=args.store_id,
            layout_path=args.layout,
            clip_start_time=clip_start
        )

        if args.output is None:
            args.output = f"data/processed/{args.camera_id}_events.jsonl"

        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        with open(args.output, "w") as f:
            for event in events:
                f.write(json.dumps(event) + "\n")

        print(f"\n📁 Saved {len(events)} events → {args.output}")


if __name__ == "__main__":
    main()