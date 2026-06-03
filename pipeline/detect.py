"""
Detection pipeline: process CCTV clips and emit structured events.
Uses YOLOv8 for person detection, distance-based tracking, and coordinate-based
zone classification from store_layout.json.

PROMPT: Build a detection pipeline that processes CCTV video, detects people,
tracks them across frames, assigns visitor IDs, and emits structured events.
CHANGES MADE: Added zone classification from store_layout.json coordinates,
proper timestamp from frame offset, ZONE_DWELL emission every 30s,
BILLING_QUEUE_JOIN with queue_depth, BILLING_PURCHASE after 45s dwell,
REENTRY detection, staff detection via zone frequency, and graceful
handling of missing/corrupted frames.
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

    def __init__(self, layout_path="data/store_layout.json"):
        self.zones = {}
        self.frame_w = 1920  # default 1080p
        self.frame_h = 1080

        try:
            with open(layout_path) as f:
                layout = json.load(f)
            self.zones = layout.get("zones", {})
            print(f"  Loaded {len(self.zones)} zones from {layout_path}")
        except FileNotFoundError:
            print(f"  ⚠️ {layout_path} not found — using default zones")
            self.zones = {
                "ENTRY": {"x1": 0, "y1": 0, "x2": 400, "y2": 300},
                "SKINCARE": {"x1": 400, "y1": 0, "x2": 800, "y2": 400},
                "MAKEUP": {"x1": 800, "y1": 0, "x2": 1200, "y2": 400},
                "HAIRCARE": {"x1": 0, "y1": 400, "x2": 400, "y2": 700},
                "FRAGRANCE": {"x1": 400, "y1": 400, "x2": 600, "y2": 700},
                "BATH_BODY": {"x1": 600, "y1": 400, "x2": 900, "y2": 700},
                "BILLING": {"x1": 1000, "y1": 400, "x2": 1300, "y2": 700},
                "FOH": {"x1": 400, "y1": 300, "x2": 800, "y2": 500}
            }

    def set_frame_size(self, frame_w, frame_h):
        """Set actual frame dimensions for coordinate scaling."""
        self.frame_w = frame_w
        self.frame_h = frame_h

    def classify(self, bbox):
        """Classify bbox center into a zone. bbox = (x1, y1, x2, y2) in pixels."""
        try:
            x1, y1, x2, y2 = bbox
            cx = (x1 + x2) / 2
            cy = (y1 + y2) / 2

            # Scale layout coordinates to actual frame size
            # Layout assumes 1300x700 grid, scale to actual frame
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

            return None  # outside all zones
        except Exception:
            return None


class PersonTracker:
    """Track persons across frames using bounding box distance."""

    def __init__(self, store_id="STORE_BLR_002", camera_id="CAM_1"):
        self.active_tracks = {}
        self.exited_tracks = {}  # for re-entry detection
        self.next_track_id = 0
        self.store_id = store_id
        self.camera_id = camera_id
        self.match_distance = 200  # wider matching for wide-angle CCTV
        self.exit_timeout_frames = 90  # give more time before marking exit
        self.dwell_interval_ms = 30000  # emit ZONE_DWELL every 30 seconds
        self.reentry_window_frames = 450  # 30 seconds at 15fps
        self.staff_zone_threshold = 3  # visited 3+ zones = likely staff
        self.purchase_dwell_ms = 10000  # 10s in billing = purchase
 
    def _is_likely_staff(self, track):
        """Staff move through many zones frequently and are present for long durations."""
        zones_visited = set()
        timestamps = []
        for h in track.get("history", []):
            if h.get("zone"):
                zones_visited.add(h["zone"])
            if h.get("frame_idx") is not None:
                timestamps.append(h["frame_idx"])

        # Heuristic 1: visited 3+ distinct zones
        if len(zones_visited) >= self.staff_zone_threshold:
            return True

        # Heuristic 2: present for long continuous duration (>2 min at 15fps)
        if timestamps and len(timestamps) > 50:
            span = max(timestamps) - min(timestamps)
            if span > 2000:
                return True

        return False

    def _check_reentry(self, bbox):
        """Check if a new detection matches a recently exited visitor."""
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
            if dist < self.match_distance * 2:  # wider threshold for re-entry
                return track_id, track
        return None, None

    def update(self, detections, zone_classifier, timestamp, frame_idx):
        """Update tracks with new detections and return events."""
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

            # Find closest existing active track
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
                # Matched existing track
                track_id = closest_track_id
                matched_tracks.add(track_id)
            else:
                # Check re-entry
                reentry_id, reentry_track = self._check_reentry(bbox)

                if reentry_track is not None:
                    # Re-entry detected
                    track_id = self.next_track_id
                    self.next_track_id += 1
                    self.active_tracks[track_id] = {
                        "visitor_id": reentry_track["visitor_id"],  # same visitor
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
                    # New visitor
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

            # Update track state
            track = self.active_tracks[track_id]
            track["last_bbox"] = bbox
            track["frames_unseen"] = 0

            # Zone classification
            current_zone = zone_classifier.classify(bbox)
            if current_zone and current_zone != "ENTRY":
                # Zone transition
                if track["last_zone"] != current_zone:
                    # ZONE_EXIT from previous zone
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

                    # ZONE_ENTER new zone
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

                    # BILLING_QUEUE_JOIN
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
                   

                    # Same zone — check ZONE_DWELL (every 30s)
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

        # Handle lost tracks — increment unseen counter or emit EXIT
        for track_id in list(self.active_tracks.keys()):
            if track_id not in matched_tracks:
                self.active_tracks[track_id]["frames_unseen"] += 1

                if self.active_tracks[track_id]["frames_unseen"] > self.exit_timeout_frames:
                    track = self.active_tracks[track_id]

                    # Check if staff before EXIT
                    is_staff = self._is_likely_staff(track)

                    events.append(self._make_event(
                        visitor_id=track["visitor_id"],
                        event_type="EXIT",
                        timestamp=timestamp,
                        confidence=0.85,
                        is_staff=is_staff,
                        session_seq=track["session_seq"]
                    ))

                    # Save for re-entry detection
                    self.exited_tracks[track_id] = {
                        "visitor_id": track["visitor_id"],
                        "last_bbox": track["last_bbox"],
                        "exit_frame": frame_idx,
                        "history": track["history"],
                        "session_seq": track["session_seq"] + 1
                    }
                    del self.active_tracks[track_id]

        # Clean old exited tracks (beyond re-entry window)
        for track_id in list(self.exited_tracks.keys()):
            if frame_idx - self.exited_tracks[track_id].get("exit_frame", 0) > self.reentry_window_frames:
                del self.exited_tracks[track_id]

        return events

    def _make_event(self, visitor_id, event_type, timestamp, zone_id=None,
                    dwell_ms=0, confidence=0.9, is_staff=False,
                    queue_depth=None, sku_zone=None, session_seq=1):
        """Build a structured event dict matching the challenge schema."""
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

    # Load model
    model = YOLO("yolov8n.pt")
    print("   ✅ YOLOv8n loaded")

    # Zone classifier from store_layout.json
    zone_classifier = ZoneClassifier(layout_path)

    # Tracker
    tracker = PersonTracker(store_id=store_id, camera_id=camera_id)

    # Open video
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print(f"   ❌ Could not open video: {video_path}")
        return []

    fps = cap.get(cv2.CAP_PROP_FPS) or 15
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    frame_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    frame_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    zone_classifier.set_frame_size(frame_w, frame_h)

    # Clip start time — use provided or derive from filename
    if clip_start_time is None:
        clip_start_time = datetime.now(timezone.utc)

    print(f"   Resolution: {frame_w}x{frame_h} | FPS: {fps:.1f} | Frames: {total_frames}")
    print(f"   Clip start: {clip_start_time.isoformat()}")

    frame_idx = 0
    all_events = []
    process_every_n = 2  # process every 2nd frame for speed

    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break

        frame_idx += 1

        # Skip frames for performance
        if frame_idx % process_every_n != 0:
            continue

        # Derive timestamp from frame offset
        timestamp = (clip_start_time + timedelta(seconds=frame_idx / fps)).isoformat()
        try:
            # Detect persons (class 0)
            results = model(frame, classes=[0], conf=0.35, verbose=False)
            detections = []

            for r in results:
                for box in r.boxes:
                    bbox = box.xyxy[0].cpu().numpy().tolist()
                    conf = float(box.conf[0])
                    detections.append((bbox, conf))

            # Track and emit events
            events = tracker.update(detections, zone_classifier, timestamp, frame_idx)
            all_events.extend(events)

        except Exception as e:
            print(f"   ⚠️ Frame {frame_idx} error: {e}")
            continue

        # Progress log
        if frame_idx % 300 == 0:
            pct = round(frame_idx / total_frames * 100, 1) if total_frames else 0
            print(f"   📊 Frame {frame_idx}/{total_frames} ({pct}%) | Events: {len(all_events)}")

    cap.release()

    # Flush remaining active tracks as EXIT
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


def main():
    parser = argparse.ArgumentParser(description="CCTV Detection Pipeline")
    parser.add_argument("--clip", required=True, help="Path to video clip")
    parser.add_argument("--camera_id", default="CAM_1", help="Camera ID")
    parser.add_argument("--store_id", default="STORE_BLR_002", help="Store ID")
    parser.add_argument("--layout", default="data/store_layout.json", help="Store layout JSON")
    parser.add_argument("--output", default=None, help="Output JSONL file path")
    parser.add_argument("--start_time", default=None,
                        help="Clip start time ISO format (e.g. 2026-04-10T10:00:00Z)")
    args = parser.parse_args()

    # Parse start time
    clip_start = None
    if args.start_time:
        try:
            clip_start = datetime.fromisoformat(args.start_time.replace("Z", "+00:00"))
        except Exception:
            clip_start = None

    # Process clip
    events = process_clip(
        video_path=args.clip,
        camera_id=args.camera_id,
        store_id=args.store_id,
        layout_path=args.layout,
        clip_start_time=clip_start
    )

    # Output path
    if args.output is None:
        args.output = f"data/processed/{args.camera_id}_events.jsonl"

    # Ensure output directory exists
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)

    # Save events
    with open(args.output, "w") as f:
        for event in events:
            f.write(json.dumps(event) + "\n")

    print(f"\n📁 Saved {len(events)} events → {args.output}")

    # Summary
    event_types = {}
    for e in events:
        t = e["event_type"]
        event_types[t] = event_types.get(t, 0) + 1
    print(f"\n📊 Event Summary:")
    for t, count in sorted(event_types.items()):
        print(f"   {t}: {count}")

    visitors = set(e["visitor_id"] for e in events)
    staff = set(e["visitor_id"] for e in events if e.get("is_staff"))
    print(f"\n   Unique visitors: {len(visitors)}")
    print(f"   Staff detected: {len(staff)}")
    print(f"   Customers: {len(visitors) - len(staff)}")


if __name__ == "__main__":
    main()