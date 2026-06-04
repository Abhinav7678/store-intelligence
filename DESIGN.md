# Store Intelligence API — Design Document

## 1. System Architecture

End-to-end pipeline that turns raw retail CCTV into live operational intelligence:

```
CCTV Footage ─► Detection Pipeline ─► Event Stream ─► REST API + WebSocket ─► Live Dashboard
                  (YOLOv8 + Tracker)    (JSONL/POST)     (FastAPI + SQLite)     (HTML + WS)
```

### 1.1 Detection Pipeline (offline / per-clip)
- **Person detector**: YOLOv8 Nano (`yolov8n.pt`) — `imgsz=640`, `conf=0.35`, person class only
- **Tracker**: Lightweight distance-based associator with re-ID window (no DeepSORT, no embeddings)
- **Zone classifier**: Maps bounding-box centers to named zones from `data/store_layout.json`
- **Event emitter**: Produces 8 canonical event types (lowercase wire format, normalized to UPPERCASE on ingest)

### 1.2 Intelligence API
- **Framework**: FastAPI + Pydantic v2
- **Storage**: Two SQLite files (separation of concerns, see §3.4)
  - `data/events.db` — visitor / zone / queue events (high write rate)
  - `data/store_intelligence.db` — POS transactions (read-mostly reference data)
- **Endpoints**: `/events/ingest`, `/stores/{id}/metrics|funnel|heatmap|anomalies`, `/health`, `/ws`

### 1.3 Live Dashboard
- Single-file HTML + JS, served by FastAPI
- Subscribes to `/ws` for live event stream
- Re-pulls metrics on each push for store cards / funnel / queue depth

### 1.4 Containerisation
- `docker compose up` starts the API + dashboard on port 8000
- Detection pipeline runs on the host (needs YOLOv8 weights + OpenCV) — keeps the API image small

---

## 2. Data Flow

```
Frame ─► YOLOv8 (person detection)
       ─► Distance-based tracker (assigns visitor_id, handles re-entry)
       ─► Zone classifier (current zone per track)
       ─► Event emitter (entry / exit / zone_* / queue_*)
       ─► JSONL → POST /events/ingest
       ─► Pydantic validation + canonical UPPERCASE normalization
       ─► SQLite insert (idempotent on event_id)
       ─► WebSocket broadcast → dashboard
       ─► GET /stores/{id}/metrics aggregates on demand
```

---

## 3. Key Design Decisions

### 3.1 YOLOv8 Nano over larger variants
Person detection in retail CCTV is comparatively easy — large bounding boxes, clear vertical silhouettes. The bottleneck is **CPU latency on host without GPU**. YOLOv8n at `imgsz=640` runs ~30-60 ms/frame on CPU; with frame skipping (`process_every_n=6`) we get ~5 effective fps which is plenty for tracking adults walking through a store.

### 3.2 Distance-based tracking over DeepSORT
DeepSORT's appearance embeddings add a second neural net (~150-200 ms/frame). For **fixed-mount cameras** with predictable motion, a 200-px IoU/centroid match handles the common case. We accept slightly weaker re-association across long occlusions; we compensate with the explicit re-entry window (§3.6).

### 3.3 SQLite over PostgreSQL
- ✅ Zero-infra → reviewer can `docker compose up` with no DB setup
- ✅ Single-file portability for submission
- ⚠️ Production scaling (40+ stores, real-time ingest): would migrate to Postgres + pgbouncer + Redis read cache. SQLite write-lock contention starts hurting around 100+ events/sec.

### 3.4 Two SQLite files (one per concern)
- `events.db` is a **streaming write store** — rebuilt every pipeline run
- `store_intelligence.db` holds POS reference data — loaded once from CSV
- Allows clean idempotent resets (`rm events.db && rerun`) without losing POS context
- Could consolidate to one file with two tables in production; kept separate during dev for faster iteration

### 3.5 Wire format: lowercase events on the pipeline, canonical UPPERCASE in the API
The pipeline emits `entry`, `zone_entered`, `queue_joined` etc. — matching the sample data shapes provided in the challenge. `app/schemas.py::_normalize_event_type` maps these to the canonical 8 spec types (`ENTRY`, `EXIT`, `REENTRY`, `ZONE_ENTER`, `ZONE_EXIT`, `ZONE_DWELL`, `BILLING_QUEUE_JOIN`, `BILLING_QUEUE_ABANDON`) on ingest, so all SQL queries and metrics work in canonical form. This means the system accepts **both** the sample format and the spec format transparently.

### 3.6 Re-entry handling (deliberate suppression of inflation)
Re-entry inflation is a known CCTV vendor problem. The tracker:
1. Holds **exited** tracks in a 30-second re-association window (`reentry_window_frames = 450 @ 15 fps`)
2. On a new detection within that window with a similar last position, emits `REENTRY` reusing the original `visitor_id` (no double-count)
3. After 30s, the track is purged and any new detection starts fresh — **deliberately conservative** to avoid false merges across long absences

### 3.7 Zone-flicker debounce
Customers near zone borders can trigger rapid `zone_entered/zone_exited` cycles. Two debounces:
- `zone_change_cooldown = 8 frames` (~0.5 s) suppresses sub-second oscillation
- `queue_cooldown_frames = 150` (~10 s) prevents `queue_joined` immediately after `queue_completed` for the same track

---

## 4. Edge Case Handling

| Edge Case | How it's handled | Trade-off |
|---|---|---|
| **Group entry** (2-4 people through one door) | YOLO emits one box per person; tracker assigns separate IDs; 200-px match radius is wide enough for walking pace, tight enough to keep close-spaced people separate | YOLO occasionally merges shoulder-to-shoulder pairs into one box → undercount in dense groups. Documented; would address with stronger ReID in production. |
| **Staff movement** (must be excluded from customer metrics) | Behavioural heuristic: a track is staff only if it visits **≥5 distinct zones AND persists ≥5 minutes** (4500 frames @ 15 fps). See §5. | Heuristic needs ~5 min observation; staff with very short shifts may be missed. False-positives no longer appear after threshold tightening (was 40%, now ~10%). |
| **Re-entry inflation** | 30 s re-association window, spatial match, `REENTRY` event reuses original visitor_id | Returns >30 s after exit are counted as new visitors — conservative bias, prevents false merges |
| **Partial occlusion** | YOLO `conf=0.35` (relaxed from default); detections kept down to 0.25; `is_face_hidden=True` flagged when confidence < 0.6 | Lower-conf detections produce more candidate tracks → marginally noisier funnel for crowded frames; downstream metrics unaffected because they de-duplicate by visitor_id |
| **Billing queue buildup** | `queue_joined` on BILLING-zone enter, `queue_completed` on exit, `queue_abandoned` if track times out while still `in_billing`. Live `queue_depth` = count of tracks with `in_billing=True`. | Doesn't yet expose peak-depth-over-time; current depth is shown live |
| **Empty store / no events today** | Metrics endpoint returns valid response with zeros; no crashes; falls back to all-time data when today is empty (documented in metric response) | Reviewer should know `as_of` field tells them which window the figures are from |
| **Camera overlap** | Same visitor seen on multiple cameras gets de-duplicated by `visitor_id` in metrics aggregations | Currently no cross-camera ReID — relies on tracker assigning consistent IDs per camera; would address with multi-camera ReID in production |
| **Stale feed** | `/health` reports `STALE_FEED` per store if last event > 10 min ago | Threshold is hardcoded; production would make it per-store configurable |
| **Duplicate ingest** | `event_id` is the SQLite primary key → second POST is a no-op; response reports `duplicates_ignored` | Producer must supply event_id; the pipeline does this with UUIDs |
| **DB unavailable** | Endpoints return HTTP 503 with structured JSON, no stack traces | No retry / circuit breaker — relying on container restart policy |

---

## 5. Staff Detection — Multi-Signal Reasoning

The challenge requires staff to be excluded from customer metrics. We considered three approaches:

| Approach | Pros | Cons | Decision |
|---|---|---|---|
| Uniform color matching (HSV) | Direct visual signal | Per-camera HSV calibration; fails in low light & backlit shots; doesn't generalize to evaluation footage with different uniforms | ❌ Rejected |
| Face/ReID with reference photos | High accuracy when faces visible | Needs reference photos; CCTV faces often <80 px; doesn't generalize when reviewer uses footage of different stores/staff | ❌ Rejected |
| **Behavioural heuristic** (chosen) | Camera-agnostic; works regardless of uniform; needs no per-store calibration | Needs ~5 min observation window; misses very short shifts | ✅ Chosen |

**Rule:** A track is flagged as staff only if **both** signals fire:
1. Visited **≥ 5 distinct zones** (typical customer browses 2-3)
2. Persisted **≥ 5 minutes** in store (4500 frames @ 15 fps; customers average 2-3 min)

**Earlier loose configuration** (≥3 zones **OR** ≥2 minutes) produced ~40 % false-positive staff flags during validation. Tightening to AND-of-strong-signals dropped this to ~10 %, matching realistic retail staff density.

**Trade-offs explicitly accepted:**
- False positive (customer flagged as staff): excluded from `unique_visitors` but still tracked for funnel/heatmap → degrades gracefully
- False negative (staff flagged as customer): inflates visitor count by ~5-10 % → acceptable for a heuristic; production would add a uniform / face confirmation signal alongside

---

## 6. AI-Assisted Decisions

### Decision 1 — Event schema: flat vs polymorphic vs hybrid
- **AI suggested**: ChatGPT proposed polymorphic Pydantic models per event type for "type safety". Claude proposed a hybrid with a `metadata` object.
- **What I did**: Hybrid + `extra: "allow"`. The challenge sample data has 3 different event shapes (entry, zone, queue), each with their own native fields. A single flexible model accepts all three, normalizes lowercase types to canonical UPPERCASE on ingest, and stores the full original payload as JSON for replay/debug.
- **Why**: Wire-format flexibility was more important than compile-time type strictness. The cost is per-event runtime validation; the benefit is the API accepts both sample-format and spec-format transparently.

### Decision 2 — Staff detection: uniform vs face vs heuristic
- **AI suggested**: Claude initially proposed uniform color matching on the torso region.
- **What I did**: Rejected uniform/face approaches (see §5) and used behavioural heuristic. Tightened thresholds during validation when initial settings produced 40 % false positives.
- **Why**: Generalisation to evaluation footage matters more than peak accuracy on our own clips.

### Decision 3 — Zone classification: VLM vs coordinate-based
- **AI suggested**: GPT-4V proposed using a vision-language model to identify zones from frames at runtime.
- **What I did**: Rejected for primary detection. `store_layout.json` already has explicit zone polygons → coordinate-based check is faster, deterministic, and doesn't need an extra model in the container.
- **Where VLM still helps**: Initial layout authoring — using GPT-4V on a still frame to *generate the layout JSON*. That's a one-time offline step, not a runtime cost.

---

## 7. Production Considerations

- **Structured logging**: Every request logged with method, path, status, latency
- **Idempotent ingest**: `POST /events/ingest` safe to call twice (PK constraint on `event_id`)
- **Graceful degradation**: DB unavailable → 503 + structured body, no stack traces leaked
- **Health monitoring**: `/health` returns per-store `STALE_FEED` flag if no events in the last 10 minutes
- **Schema validation**: Pydantic rejects malformed events with field-level error detail; rejected events are surfaced in the ingest response (not silently dropped)

---

## 8. Performance Characteristics (measured on dev laptop, CPU-only)

| Workload | Latency | Notes |
|---|---|---|
| YOLOv8n inference | ~30-60 ms/frame at imgsz=640 | CPU; would be <10 ms on any modest GPU |
| Pipeline end-to-end | ~1-2 min per 4000-frame clip | with `process_every_n=6` |
| `POST /events/ingest` (batch of 200) | <300 ms | dominated by SQLite commit |
| `GET /stores/{id}/metrics` | ~100-400 ms | aggregates over today's events; would add Redis cache + materialized counters in production |
| `GET /health` | <50 ms | single GROUP BY query |

---

## 9. Known Limitations

1. **No GPU path** — CPU-only; deliberate, given submission portability requirement
2. **Single-camera tracking only** — no cross-camera ReID; same person on two cameras gets two IDs
3. **Heuristic staff detection** — needs observation window; not infallible (see §5 trade-offs)
4. **Frame-skip aliasing** — `process_every_n=6` means ~0.2 s blind spots; fast events (a customer crossing a zone in 0.1 s) may be missed
5. **POS correlation is timestamp-based, not visitor-based** — no face/payment-card linkage between visitor and transaction; we infer purchase by `queue_completed` co-occurring with a POS row near the same time