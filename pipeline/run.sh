#!/bin/bash
# ============================================================
# Store Intelligence — Detection Pipeline Runner
# Usage: bash pipeline/run.sh
# ============================================================

set -e

OUTPUT_DIR="data/processed"
LAYOUT="data/store_layout.json"
API_URL="http://localhost:8000"
START_TIME="2026-03-08T18:00:00Z"

echo "=============================================="
echo "🏪 Purplle Store Intelligence Detection Pipeline"
echo "=============================================="
echo "Layout:   $LAYOUT"
echo "API:      $API_URL"
echo ""

# ── Check prerequisites ────────────────────────────
if ! command -v python &> /dev/null; then
    echo "❌ Python not found"
    exit 1
fi

if [ ! -f "$LAYOUT" ]; then
    echo "❌ Store layout not found: $LAYOUT"
    exit 1
fi

mkdir -p "$OUTPUT_DIR"

# ── Load POS data ─────────────────────────────────
echo "📦 Loading POS transactions..."
POS_FILE=$(find data/ -maxdepth 1 -name "POS*" -o -name "pos*" | head -1)
if [ -n "$POS_FILE" ]; then
    python pipeline/load_pos.py "$POS_FILE"
else
    echo "  ⚠️ No POS CSV found in data/ — skipping"
fi
echo ""

# ── Check API is running ──────────────────────────
echo "🔍 Checking API..."
if curl -s "$API_URL/health" > /dev/null 2>&1; then
    echo "  ✅ API is running"
else
    echo "  ❌ API not reachable at $API_URL"
    echo "     Start it with: docker compose up -d"
    exit 1
fi
echo ""

# ── Store 1: ST1076 ───────────────────────────────
STORE1="ST1076"
STORE1_DIR="data/CCTV Footage/Store 1"
echo "=============================================="
echo "🏪 Processing Store: $STORE1"
echo "=============================================="

declare -A STORE1_CAMS=(
    ["CAM_1_ZONE"]="$STORE1_DIR/CAM 1 - zone.mp4"
    ["CAM_2_ZONE"]="$STORE1_DIR/CAM 2 - zone.mp4"
    ["CAM_3_ENTRY"]="$STORE1_DIR/CAM 3 - entry.mp4"
    ["CAM_5_BILLING"]="$STORE1_DIR/CAM 5 - billing.mp4"
)

for CAM_ID in "${!STORE1_CAMS[@]}"; do
    CLIP="${STORE1_CAMS[$CAM_ID]}"
    if [ -f "$CLIP" ]; then
        echo "🎬 Processing $CAM_ID → $CLIP"
        python pipeline/detect.py \
            --clip "$CLIP" \
            --camera_id "$CAM_ID" \
            --store_id "$STORE1" \
            --layout "$LAYOUT" \
            --output "$OUTPUT_DIR/${STORE1}_${CAM_ID}_events.jsonl" \
            --start_time "$START_TIME"
        echo ""
    else
        echo "⚠️  Skipping $CAM_ID — $CLIP not found"
    fi
done

# ── Store 2: ST1008 ───────────────────────────────
STORE2="ST1008"
STORE2_DIR="data/CCTV Footage/Store 2"
echo "=============================================="
echo "🏪 Processing Store: $STORE2"
echo "=============================================="

declare -A STORE2_CAMS=(
    ["CAM_BILLING"]="$STORE2_DIR/billing_area.mp4"
    ["CAM_ENTRY1"]="$STORE2_DIR/entry 1.mp4"
    ["CAM_ENTRY2"]="$STORE2_DIR/entry 2.mp4"
    ["CAM_ZONE"]="$STORE2_DIR/zone.mp4"
)

for CAM_ID in "${!STORE2_CAMS[@]}"; do
    CLIP="${STORE2_CAMS[$CAM_ID]}"
    if [ -f "$CLIP" ]; then
        echo "🎬 Processing $CAM_ID → $CLIP"
        python pipeline/detect.py \
            --clip "$CLIP" \
            --camera_id "$CAM_ID" \
            --store_id "$STORE2" \
            --layout "$LAYOUT" \
            --output "$OUTPUT_DIR/${STORE2}_${CAM_ID}_events.jsonl" \
            --start_time "$START_TIME"
        echo ""
    else
        echo "⚠️  Skipping $CAM_ID — $CLIP not found"
    fi
done

# ── Emit all events to API ────────────────────────
echo "=============================================="
echo "📤 Ingesting all events into API..."
echo "=============================================="

for EVENTS_FILE in "$OUTPUT_DIR"/*_events.jsonl; do
    if [ -f "$EVENTS_FILE" ]; then
        echo ""
        echo "📂 Emitting $(basename $EVENTS_FILE)..."
        python pipeline/emit.py --input "$EVENTS_FILE" --api_url "$API_URL"
    fi
done

# ── Also ingest sample events if present ──────────
if [ -f "data/sample_events.jsonl" ]; then
    echo ""
    echo "📂 Emitting sample_events.jsonl..."
    python pipeline/emit.py --input "data/sample_events.jsonl" --api_url "$API_URL"
fi

# ── Final verification ────────────────────────────
echo ""
echo "=============================================="
echo "✅ Pipeline Complete!"
echo "=============================================="
echo ""
echo "🔍 Store ST1076 metrics:"
curl -s "$API_URL/stores/ST1076/metrics" | python -m json.tool 2>/dev/null || echo "  No data yet"
echo ""
echo "🔍 Store ST1008 metrics:"
curl -s "$API_URL/stores/ST1008/metrics" | python -m json.tool 2>/dev/null || echo "  No data yet"
echo ""
echo "🔍 Health:"
curl -s "$API_URL/health" | python -m json.tool
echo ""
echo "📊 Dashboard: $API_URL/"
echo "=============================================="