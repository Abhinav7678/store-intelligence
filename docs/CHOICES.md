# Technical Choices — Store Intelligence Challenge

## Choice 1: Detection Model — YOLOv8 Nano

### Options Considered
| Model | Pros | Cons |
|-------|------|------|
| YOLOv8n | Fast (40ms CPU), good accuracy, ultralytics ecosystem | Lower accuracy than larger variants |
| YOLOv8x | Highest accuracy | Too slow for CPU (200ms+), overkill for person detection |
| RT-DETR | Transformer-based, good accuracy | Heavy dependency, slower on CPU |
| MediaPipe | Lightweight, Google-backed | Not designed for multi-person retail scenarios |

### What AI Suggested
Claude recommended YOLOv8m as a "middle ground" between speed and accuracy. GitHub Copilot suggested YOLOv8n with confidence threshold tuning.

### My Decision: YOLOv8n
**Reasoning**: For 1080p 15fps retail footage, person detection is not a hard problem — people are large objects in frame. YOLOv8n achieves >85% mAP on person class which is sufficient. The key challenge is tracking and zone classification, not detection itself. By choosing the fastest model, we preserve compute budget for tracking logic.

**Trade-off**: We accept slightly lower detection confidence on partially occluded persons, but compensate by NOT suppressing low-confidence events (they are emitted with their actual confidence score, as required by the challenge).

---

## Choice 2: Event Schema Design

### Options Considered
1. **Flat schema** — all fields at top level, nullable fields for type-specific data
2. **Polymorphic schema** — different schemas per event type
3. **Hybrid schema** — common fields + metadata object for type-specific data

### What AI Suggested
ChatGPT suggested polymorphic schemas (different Pydantic models per event type) for "type safety". Claude suggested the hybrid approach with a metadata object.

### My Decision: Hybrid Schema (matching challenge specification)
**Reasoning**: The challenge specifies an exact schema with a `metadata` object. Following it exactly ensures schema compliance scoring. The metadata object cleanly separates universal fields (event_id, store_id, timestamp) from type-specific data (queue_depth, sku_zone). This also makes the API simpler — one POST endpoint handles all 8 event types.

**Trade-off**: Nullable fields like `zone_id` (null for ENTRY/EXIT) could confuse consumers, but the event_type field disambiguates clearly. We chose developer simplicity over strict typing.

---

## Choice 3: API Architecture — FastAPI + SQLite

### Options Considered
| Stack | Pros | Cons |
|-------|------|------|
| FastAPI + SQLite | Simple, fast, portable, single file DB | Not scalable to 40 stores |
| FastAPI + PostgreSQL | Scalable, concurrent | Requires separate container, complex setup |
| Flask + SQLite | Familiar, lightweight | No async, no auto-docs |
| Node.js + Express | Event-driven | Less ML ecosystem integration |

### What AI Suggested
GitHub Copilot suggested FastAPI + PostgreSQL. Claude suggested FastAPI + SQLite for "submission portability".

### My Decision: FastAPI + SQLite
**Reasoning**:
1. **Acceptance gate requires `docker compose up` only** — SQLite means no database container needed
2. **Scoring harness tests FastAPI coverage** — Python + FastAPI is explicitly recommended
3. **Portability** — reviewer can `git clone` and run immediately without DB setup
4. **Sufficient for 5-store dataset** — SQLite handles thousands of events without issue

**What I would change in production**: PostgreSQL with connection pooling (pgbouncer), Redis for caching metrics computation, and horizontal API scaling behind a load balancer. The current architecture is optimised for correctness verification, not production scale.

### Scaling Notes
At 40 live stores sending events in real-time:
- SQLite would hit write-lock contention
- Need PostgreSQL + async writes
- Metrics computation would need caching layer
- Consider event streaming (Kafka) between pipeline and API

---

## Summary

| Decision | Chose | Rejected | Key Reason |
|----------|-------|----------|------------|
| Detection Model | YOLOv8n | YOLOv8x, RT-DETR | Speed on CPU, sufficient accuracy for people |
| Event Schema | Hybrid (spec-compliant) | Polymorphic | Schema compliance scoring |
| API Stack | FastAPI + SQLite | PostgreSQL | Submission portability, acceptance gate |