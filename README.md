# Store Intelligence — AI-Powered Retail Analytics

## 🚀 Quick Start

```bash
git clone https://github.com/Abhinav7678/store-intelligence.git
cd store-intelligence
docker compose up --build
```

- **API Docs:** http://localhost:8000/docs
- **Live Dashboard:** http://localhost:8000/ (real-time metrics via WebSocket)

## 📋 Overview

An end-to-end AI-powered Store Intelligence System that processes raw CCTV footage from retail stores, detects and tracks customer behaviour, and exposes real-time analytics through production-ready REST APIs.

**Key Capabilities:**
- Person detection & tracking from CCTV footage
- 8 event types: ENTRY, EXIT, ZONE_ENTER, ZONE_EXIT, ZONE_DWELL, BILLING_QUEUE_JOIN, BILLING_QUEUE_ABANDON, REENTRY
- Real-time metrics, funnel analysis, heatmaps, and anomaly detection
- Staff exclusion from visitor metrics
- Idempotent event ingestion with deduplication
- Graceful degradation (503 responses, never crashes)
- Live Web Dashboard with real-time WebSocket updates

## 🏗️ Architecture

```
CCTV Footage (5 stores × 3 cameras)
    │
    ▼
┌─────────────────────────┐
│  Detection Pipeline      │
│  YOLOv8n → Tracker →    │
│  Event Emitter           │
└───────────┬─────────────┘
            │ JSONL events
            ▼
┌─────────────────────────┐
│  POST /events/ingest     │
│  (batch, idempotent)     │
└───────────┬─────────────┘
            │
            ▼
┌─────────────────────────┐
│  SQLite Database         │
└───────────┬─────────────┘
            │
    ┌───────┴────────┐
    ▼                ▼
┌──────────┐  ┌──────────────┐
│ REST API │  │ WebSocket    │
│ Endpoints│  │ Live Dashboard│
└──────────┘  └──────────────┘
```

## 📁 Project Structure

```
store-intelligence/
├── app/
│   ├── __init__.py        # App package init
│   ├── main.py            # FastAPI entrypoint
│   ├── models.py          # Database models
│   ├── schemas.py         # Pydantic request/response schemas
│   ├── ingestion.py       # Ingest + dedup logic
│   ├── metrics.py         # Real-time metric computation
│   ├── funnel.py          # Funnel + session logic
│   ├── heatmap.py         # Zone heatmap generation
│   ├── anomalies.py       # Anomaly detection
│   ├── health.py          # Health + stale feed detection
│   ├── sessions.py        # Session management
│   └── ws.py              # WebSocket for live dashboard
├── pipeline/
│   ├── __init__.py        # Pipeline package init
│   ├── detect.py          # YOLOv8 person detection
│   ├── tracker.py         # Re-ID / tracking logic
│   ├── emit.py            # Event schema + emission
│   ├── load_pos.py        # POS transaction loading + correlation
│   └── run.sh             # One command to process all clips
├── scripts/
│   ├── migrate.py         # Database migration script
│   ├── run_acceptance.sh  # Acceptance test runner (Linux/Mac)
│   ├── run_acceptance.ps1 # Acceptance test runner (Windows)
│   ├── run_tests.sh       # Test runner (Linux/Mac)
│   ├── run_tests.ps1      # Test runner (Windows)
│   ├── validate_events.py # Event schema validation
│   └── verify_all.ps1     # Full verification script
├── tests/
│   ├── __init__.py
│   ├── conftest.py              # Shared test fixtures
│   ├── test_anomalies.py       # Anomaly scenario tests
│   ├── test_endpoints.py       # API endpoint integration tests
│   ├── test_ingestion_idempotency.py  # Idempotency verification
│   ├── test_metrics.py         # Metrics computation tests
│   ├── test_pipeline.py        # Detection pipeline tests
│   ├── test_sessions_and_schema.py    # Session + schema tests
│   └── test_ws_publish.py      # WebSocket publish tests
├── docs/
│   ├── DESIGN.md          # Architecture + AI-assisted decisions
│   └── CHOICES.md         # 3 decisions with full reasoning
├── data/                  # Runtime data (SQLite DB, store layouts)
├── index.html             # Live Web Dashboard UI
├── sample_events_acceptance.json  # Acceptance test events
├── check_db.py            # Database verification utility
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── requirements-cv.txt    # CV/detection pipeline dependencies
├── .flake8
├── .gitignore
└── README.md
```

## 🔌 API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/events/ingest` | POST | Batch ingest up to 500 events (idempotent) |
| `/stores/{store_id}/metrics` | GET | Visitors, conversion rate, avg dwell, queue depth |
| `/stores/{store_id}/funnel` | GET | ENTRY → ZONE → BILLING → PURCHASE with dropoff % |
| `/stores/{store_id}/heatmap` | GET | Zone scores (0-100) based on visits + dwell |
| `/stores/{store_id}/anomalies` | GET | Queue spike, dead zone, conversion drop alerts |
| `/health` | GET | Service health + STALE_FEED detection per store |
| `/ws` | WebSocket | Real-time event stream for live dashboard |

## 🎥 Running Detection Pipeline

```bash
# Copy CCTV clips to data/cctv/
mkdir -p data/cctv
cp /path/to/clips/*.mp4 data/cctv/

# Process all clips and emit events
bash pipeline/run.sh
```

Or process directly:
```bash
python -m pipeline.detect
```

Events are emitted as JSONL and automatically ingested into the API.

## 📊 Live Dashboard (Part E)

After starting the API with `docker compose up --build`, open the live dashboard:

```
http://localhost:8000/
```

The dashboard displays real-time metrics via WebSocket:
- **Unique Visitors** (excluding staff)
- **Conversion Rate** (visitors → purchase)
- **Avg Dwell Time** across zones
- **Queue Depth** at billing
- **Funnel Analysis** with drop-off percentages

Metrics update live as events flow in from the detection pipeline.

## 🧪 Running Tests

```bash
# Run all tests with coverage
pytest tests/ -v --cov=app --cov-report=term-missing

# Run acceptance tests
bash scripts/run_acceptance.sh

# Validate event schema
python scripts/validate_events.py
```

## 🐳 Docker

```bash
# Start
docker compose up --build

# Stop
docker compose down
```

## 📊 Sample API Usage

### Ingest Events
```bash
curl -X POST http://localhost:8000/events/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "events": [{
      "event_id": "evt_001",
      "store_id": "STORE_BLR_002",
      "camera_id": "CAM_1",
      "visitor_id": "VIS_000001",
      "event_type": "ENTRY",
      "timestamp": "2026-04-10T14:00:00Z",
      "zone_id": null,
      "dwell_ms": 0,
      "is_staff": false,
      "confidence": 0.92,
      "metadata": {"queue_depth": null, "sku_zone": null, "session_seq": 1}
    }]
  }'
```

### Get Metrics
```bash
curl http://localhost:8000/stores/STORE_BLR_002/metrics
```

### Get Funnel
```bash
curl http://localhost:8000/stores/STORE_BLR_002/funnel
```

### Get Anomalies
```bash
curl http://localhost:8000/stores/STORE_BLR_002/anomalies
```

## 🔧 Tech Stack

| Component | Choice | Reasoning |
|-----------|--------|-----------|
| Detection | YOLOv8 Nano | Fast CPU inference, sufficient for person detection |
| Tracking | Custom distance-based | Lighter than DeepSORT, works for fixed cameras |
| API | FastAPI | Auto-docs, async, type-safe, Python ecosystem |
| Database | SQLite | Portable, no setup, sufficient for single store |
| Dashboard | HTML + WebSocket | Real-time updates, no extra framework needed |
| Container | Docker Compose | Single command startup requirement |
| Testing | pytest | Industry standard, good coverage reporting |

## 📝 Documentation

- **[DESIGN.md](docs/DESIGN.md)** — Full architecture, data flow, edge cases, and AI-assisted decisions
- **[CHOICES.md](docs/CHOICES.md)** — 3 key technical decisions with alternatives considered and trade-offs

## ⚡ Edge Cases Handled

- **Group entry**: Each person tracked individually
- **Staff exclusion**: Staff filtered from all visitor metrics
- **Re-entry**: Same visitor gets same ID on return
- **Partial occlusion**: Low-confidence detections flagged, not dropped
- **Empty store**: Returns zero metrics, no crashes
- **Duplicate events**: Idempotent ingestion by event_id
- **Database failure**: Returns HTTP 503 with structured error
- **Stale feed**: Health endpoint reports lag > 10 minutes
- **Camera overlap**: Cross-camera deduplication prevents double-counting

## 🤖 AI Tools Used

- GitHub Copilot — Code generation and boilerplate
- Claude — Architecture decisions and edge case analysis
- ChatGPT — Documentation and test scenario generation

All AI-assisted decisions are documented with prompts and changes made (see prompt block headers in test files and `docs/DESIGN.md`).
