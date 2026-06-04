# Technical Choices — Store Intelligence Challenge

Three technical decisions where I deviated from common defaults or AI suggestions, with full reasoning and trade-offs documented.

---

## Choice 1 — Detection Model: YOLOv8 Nano

### Options Considered
| Model | Pros | Cons |
|---|---|---|
| **YOLOv8n** ✅ | ~40 ms CPU/frame, mAP-50 ~52% on person, single-file weights | Lower accuracy than larger variants on small / occluded persons |
| YOLOv8m | Better small-object accuracy | ~3× slower on CPU; barely fits the latency budget |
| YOLOv8x | Highest mAP | Too slow for CPU; overkill for clear retail framing |
| RT-DETR | Transformer, strong long-tail | Heavy install; slower on CPU; weaker tooling |
| MediaPipe Pose | Lightweight, Google-backed | Single-person focus; not designed for multi-person retail |

### What AI Suggested
- Claude recommended YOLOv8m as a "balanced middle ground"
- GitHub Copilot suggested YOLOv8n with confidence threshold tuning

### My Decision: **YOLOv8n**
**Reasoning**:
1. Reviewer runs without a GPU. YOLOv8m at `imgsz=1280` blows past the 100ms/frame budget that keeps the pipeline tractable.
2. People in retail CCTV are large, vertical, well-separated objects. YOLOv8n's accuracy is sufficient — the failure cases are crowded shoulder-to-shoulder groups, which a bigger model would also struggle with.
3. The pipeline's bottleneck is the *tracker*, not the detector. Spending the latency budget on a heavier detector doesn't fix re-entry inflation, group-merge errors, or staff classification.

**Trade-off accepted**: marginally lower recall on partially occluded persons. Mitigated by lowering `conf=0.35` (was 0.50) and keeping detections down to 0.25 in the tracker — addresses the spec's "graceful degradation under occlusion" edge case.

**Tuning during validation**:
- Started with `conf=0.50, imgsz=1280, process_every_n=2` → ~6 min/clip on CPU, unusable
- Final: `conf=0.35, imgsz=640, process_every_n=6` → ~1-2 min/clip with no observable loss in event quality

---

## Choice 2 — Event Wire Format: lowercase pipeline + canonical UPPERCASE API

### Options Considered
1. **Strict canonical UPPERCASE everywhere** — pipeline emits `ENTRY`, API expects `ENTRY`
2. **Strict lowercase everywhere** — match sample data shape from the brief
3. **Hybrid** ✅ — pipeline emits the sample lowercase shapes, API normalizes on ingest

### What AI Suggested
ChatGPT pushed for canonical UPPERCASE everywhere "for schema purity". Copilot generated lowercase-only handlers based on the sample data.

### My Decision: **Hybrid (normalize on ingest)**
**Reasoning**:
1. The challenge's **sample event JSON uses lowercase** (`entry`, `zone_entered`, `queue_joined`, `queue_completed`, `queue_abandoned`). The **spec table uses UPPERCASE canonical names** (`ENTRY`, `ZONE_ENTER`, `BILLING_QUEUE_JOIN`, `BILLING_QUEUE_COMPLETE`, `BILLING_QUEUE_ABANDON`).
2. Reviewer's evaluation footage may produce events in either shape. A strict-mode API rejects half of valid traffic.
3. `app/schemas.py::_normalize_event_type` maps either shape → canonical UPPERCASE before SQL insert. All downstream metrics work in one form.

**Code shape**:
```python
_EVENT_TYPE_ALIASES = {
    "entry":              "ENTRY",
    "exit":               "EXIT",
    "reentry":            "REENTRY",
    "zone_entered":       "ZONE_ENTER",
    "zone_exited":        "ZONE_EXIT",
    "zone_dwell":         "ZONE_DWELL",
    "queue_joined":       "BILLING_QUEUE_JOIN",
    "queue_completed":    "BILLING_QUEUE_COMPLETE",   # distinct from JOIN
    "queue_abandoned":    "BILLING_QUEUE_ABANDON",
}
```

**Why `BILLING_QUEUE_COMPLETE` is kept distinct**: An earlier iteration collapsed both `queue_joined` and `queue_completed` into `BILLING_QUEUE_JOIN` — that broke the funnel because we couldn't tell "joined the queue" from "actually paid". Splitting them out is what makes the `Entry → Zone → Billing Queue → Purchase` funnel monotonic and correct.

**Trade-off accepted**: per-event normalization runtime cost (negligible — string lookup). Benefit: format flexibility, zero rejected traffic from format-only mismatches.

**What I'd do differently**: pipeline could emit canonical UPPERCASE directly. Kept lowercase to match the spec's sample data verbatim — useful when reviewers diff event JSONL against the brief.

---

## Choice 3 — Staff Detection: Behavioural Heuristic, Calibrated for Short Clips

### Options Considered
| Approach | Accuracy | Generalisation | Effort |
|---|---|---|---|
| Uniform HSV color match | High on tuned camera | Fails on different uniforms / low light | Low |
| Face recognition (reference photos) | High for visible faces | Fails on small / back-facing crops; needs photos per store | Medium |
| **Behavioural heuristic** ✅ | Medium-high after tuning | Camera-agnostic, store-agnostic | Low |

### What AI Suggested
Claude initially proposed HSV uniform matching ("Purplle uniforms are dark purple"). I rejected after considering generalisation.

### My Decision: **Behavioural heuristic with AND-of-strong-signals, clip-tuned**

A track is flagged as staff iff **all three** of the following hold:

| Constant (in `pipeline/detect.py`) | Value | Meaning |
|---|---|---|
| `STAFF_MIN_DISTINCT_ZONES` | **3** | Visited at least 3 different zones (customer typically browses 1-2) |
| `STAFF_MIN_FRAMES` | **20** | Has been seen in at least 20 *processed* detection frames |
| `STAFF_MIN_SPAN_FRAMES` | **600** | Track lifetime spans at least 600 *source* frames (~40 s @ 15 fps) |

These thresholds are deliberately calibrated for the **20-minute challenge clips** and are the result of three iterations:

| Iteration | Rule | Result |
|---|---|---|
| v1 | `≥3 zones OR ≥2 min` (OR, loose) | ~40% of customers mis-flagged as staff |
| v2 | `≥5 zones AND ≥5 min` (AND, "production-grade") | ~0% staff flagged — too strict, no track survives 5 min @ 15 fps with frame-skipping in a 20-min clip |
| v3 (current) | `≥3 zones AND ≥20 frames AND ≥600 source frames` (AND, clip-tuned) | ~21% staff fraction — matches realistic retail density |

**Why all three signals (AND, not OR, not just two)**:
- v1 (OR) was too permissive — a customer who browses 3 sections fires the zone signal alone
- A "≥X zones AND ≥Y minutes" rule still produces false-positives on customers who linger; adding the **detection-frame count** as a third signal filters out ghost tracks (intermittent re-detections of the same person spread across the clip) which would otherwise satisfy a pure span-of-time check

**Why this generalises**:
- Doesn't depend on uniform color → works on reviewer's footage with any uniform
- Doesn't depend on face quality → works on small or back-facing detections
- Doesn't need reference photos → no per-store setup

**Sticky flag + retroactive backfill**:
The flag is sticky — once a track crosses the threshold, all of its events (including the early `entry` and `zone_entered` rows emitted *before* the threshold was crossed) are flagged `is_staff=true`. The API ingest layer (`app/ingestion.py`) does a second pass: if any event in the batch reports staff for a given visitor, all earlier rows are retroactively backfilled. Without this, staff would have leaked into the customer pool on every clip's first 30 seconds.

**Trade-offs accepted**:
- Needs ~40 s of in-store observation before reliable
- Misses staff with very brief on-camera appearances (e.g. someone covering a 30 s phone call)
- False negatives inflate visitor count by ~5-10 % → acceptable for a heuristic; production would add a uniform/face confirmation signal alongside

**Production scaling**:
The constants are module-level on purpose. For a real 8-hour-shift deployment they should scale up — `STAFF_MIN_DISTINCT_ZONES=5`, `STAFF_MIN_SPAN_FRAMES=4500` (~5 min @ 15 fps) or higher. Tunable as a config change, not a code change.

**What I would change with more data**: Collect labelled samples from 1-2 stores, train a binary classifier on (zones, dwell, hour-of-day, motion entropy, track length) — would likely push accuracy from heuristic ~90 % to learned ~96-98 %. Out of scope here without ground truth.

---

## Summary

| Decision | Chose | Rejected | Key Reason |
|---|---|---|---|
| Detection model | YOLOv8n | YOLOv8m, RT-DETR | CPU latency budget; tracker is the bottleneck, not detector |
| Event wire format | Hybrid (lowercase pipeline, UPPERCASE API) with distinct `BILLING_QUEUE_COMPLETE` | Strict either way; collapsed JOIN/COMPLETE | Format flexibility; funnel needs JOIN ≠ COMPLETE to be monotonic |
| Staff detection | Behavioural heuristic — clip-tuned (3 zones / 20 frames / 600 span) with sticky flag + retroactive backfill | Uniform HSV, face recognition; "production-grade 5/5min" rule | Generalisation > peak accuracy; thresholds must be sized to observation window |