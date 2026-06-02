
# Store Intelligence API — Design Document

## 1. System Architecture

The Store Intelligence system is a complete end-to-end pipeline that transforms raw CCTV footage into actionable retail analytics. The architecture consists of four primary components:

```
CCTV Footage → Detection Pipeline → Event Stream → Intelligence API → Live Dashboard
```

### 1.1 Detection Pipeline (Stage 1)
- **Model**: YOLOv8 Nano (yolov8n.pt) for person detection
- **Tracker**: Custom distance-based tracker with Re-ID capability
- **Event Emitter**: Converts tracking data into structured events (8 event types)
- **Output**: JSONL file with all detected behavioural events

### 1.2 Intelligence API (Stage 2)
- **Framework**: FastAPI (Python)
- **Database**: SQLite for simplicity and portability
- **Endpoints**: 6 RESTful endpoints for metrics, funnel, heatmap, anomalies, and health
- **Design**: Stateless request handling with structured logging

### 1.3 Containerisation (Stage 3)
- **Docker Compose**: Single `docker compose up` starts entire stack
- **Services**: API service + Dashboard service
- **Data**: Mounted as volumes for persistence

## 2. Data Flow

```
Video Frame → YOLOv8 Detection → Bounding Boxes
    → Distance-based Tracking → Visitor Assignment
    → Zone Classification → Event Emission
    → POST /events/ingest → SQLite Storage
    → GET /stores/{id}/metrics → Real-time Analytics
```

## 3. Key Design Decisions

### 3.1 Why YOLOv8 Nano?
We chose YOLOv8n for its balance of speed and accuracy on retail footage. The 1080p 15fps clips require fast inference without GPU dependency. YOLOv8n achieves ~40ms/frame on CPU, sufficient for batch processing.

### 3.2 Why SQLite over PostgreSQL?
For a single-store proof-of-concept, SQLite eliminates infrastructure complexity. The entire database is a single file, making the submission portable. For 40 stores in production, we would migrate to PostgreSQL with connection pooling.

### 3.3 Why Distance-Based Tracking over DeepSORT?
DeepSORT requires a separate appearance model (adds ~200ms/frame). For fixed retail cameras with predictable movement patterns, distance-based matching with trajectory analysis achieves sufficient accuracy while maintaining processing speed.

## 4. Edge Case Handling

| Edge Case | Solution |
|-----------|----------|
| Group entry | Each bounding box = separate track. Distance threshold prevents merging |
| Staff movement | Position heuristic + zone frequency analysis flags staff |
| Re-entry | Exited visitor positions cached, matched on re-appearance |
| Partial occlusion | Confidence preserved, not suppressed — events still emitted |
| Camera overlap | Cross-camera deduplication via visitor_id consistency |
| Empty store | Zero-traffic returns empty metrics, no crashes |
| Billing queue | Queue depth tracked via zone entry/exit sequence |

## 5. AI-Assisted Decisions

### Decision 1: Event Schema Design
**AI Suggestion**: Claude suggested adding a `direction` field to every event.
**My Decision**: Rejected — direction is only relevant for ENTRY/EXIT events. Adding it everywhere adds noise. Instead, direction is used internally by the tracker to determine ENTRY vs EXIT event type.

### Decision 2: Anomaly Detection Thresholds
**AI Suggestion**: GitHub Copilot suggested using statistical z-score for anomaly detection.
**My Decision**: Partially adopted — for queue spike we use absolute threshold (>5 = WARN, >10 = CRITICAL) because in retail context, any queue above 5 is operationally significant regardless of historical average. For conversion drop, we use relative comparison.

### Decision 3: Zone Classification Approach
**AI Suggestion**: GPT-4V suggested using a VLM to classify zones from camera frames.
**My Decision**: Rejected for primary zone detection — store_layout.json provides exact zone boundaries. Used coordinate-based classification instead (faster, deterministic). However, VLM could be valuable for validating zone boundaries during initial setup.

## 6. Production Considerations

- **Structured Logging**: Every request logged with trace_id, store_id, endpoint, latency_ms, status_code
- **Idempotency**: POST /events/ingest is safe to call twice — duplicates detected by event_id
- **Graceful Degradation**: Database unavailable → HTTP 503 with structured body, no stack traces
- **Health Monitoring**: /health endpoint reports STALE_FEED if >10 min since last event

## 7. Performance Characteristics

- Video processing: ~40ms/frame (YOLOv8n on CPU)
- API response time: <50ms for metrics queries
- Event ingestion: ~500 events/batch in <200ms
- Database: SQLite handles up to ~10K events efficiently
