#!/bin/bash
# ============================================================
# Store Intelligence — Detection Pipeline Runner
# Usage: bash pipeline/run.sh
# ============================================================

set -e

STORE_ID="STORE_BLR_002"
CLIP_DIR="data/clips/STORE_BLR_002"
OUTPUT_DIR="data/processed"
LAYOUT="data/store_layout.json"
API_URL="http://localhost:8000"
START_TIME="2026-04-10T10:00:00Z"

echo "=============================================="
echo "🏪 Store Intelligence Detection Pipeline"
echo "=============================================="
echo "Store:    $STORE_ID"
echo "Clips:    $CLIP_DIR"
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

# ── Process each camera clip ──────────────────────
CAMERAS=("CAM_1" "CAM_2" "CAM_3" "CAM_4" "CAM_5")

for CAM in "${CAMERAS[@]}"; do
    # Try multiple file extensions
    CLIP=""
    for ext in mp4 avi mov mkv; do
        if [ -f "$CLIP_DIR/${CAM}.${ext}" ]; then
            CLIP="$CLIP_DIR/${CAM}.${ext}"
            break
        fi
    done

    # Also try original naming like "CAM 1.mp4"
    if [ -z "$CLIP" ]; then
        CAM_SPACE="${CAM//_/ }"  # CAM_1 -> CAM 1
        for ext in mp4 avi mov mkv; do
            if [ -f "$CLIP_DIR/${CAM_SPACE}.${ext}" ]; then
                CLIP="$CLIP_DIR/${CAM_SPACE}.${ext}"
                break
            fi
        done
    fi

    if [ -z "$CLIP" ]; then
        echo "⚠️  Skipping $CAM — clip not found in $CLIP_DIR"
        continue
    fi

    echo "🎬 Processing $CAM → $CLIP"

    python pipeline/detect.py \
        --clip "$CLIP" \
        --camera_id "$CAM" \
        --store_id "$STORE_ID" \
        --layout "$LAYOUT" \
        --output "$OUTPUT_DIR/${CAM}_events.jsonl" \
        --start_time "$START_TIME"

    echo ""
done

# ── Emit all events to API ────────────────────────
echo "=============================================="
echo "📤 Ingesting all events into API..."
echo "=============================================="

for CAM in "${CAMERAS[@]}"; do
    EVENTS_FILE="$OUTPUT_DIR/${CAM}_events.jsonl"
    if [ -f "$EVENTS_FILE" ]; then
        echo ""
        echo "📂 Emitting $CAM events..."
        python pipeline/emit.py --input "$EVENTS_FILE" --api_url "$API_URL"
    fi
done

# ── Final verification ────────────────────────────
echo ""
echo "=============================================="
echo "✅ Pipeline Complete!"
echo "=============================================="
echo ""
echo "🔍 Verifying metrics..."
curl -s "$API_URL/stores/$STORE_ID/metrics" | python -m json.tool
echo ""
echo "🔍 Checking health..."
curl -s "$API_URL/health" | python -m json.tool
echo ""
echo "📊 Dashboard: $API_URL/dashboard"
echo "=============================================="