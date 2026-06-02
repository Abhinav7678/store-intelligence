"""
Event emitter: reads JSONL event files and POSTs them to the Store Intelligence API.
Also provides emit_event_http() for programmatic use and local WebSocket publishing.

PROMPT: Generate event emitter that reads JSONL files and POSTs events to API in batches
with retry logic, progress logging, and health check before sending.
CHANGES MADE: Added CLI with argparse, batch size control, retry logic with 2s delay,
progress logging per batch, API health check before sending, local WebSocket publish
for real-time dashboard, and final summary with accepted/duplicated/rejected counts.
"""

import json
import os
import sys
import time
import argparse
import asyncio
from typing import List, Dict, Any

try:
    import requests
except ImportError:
    print("Error: requests not installed. Install with: pip install requests")
    sys.exit(1)

BATCH_SIZE = 100  # max 500 per spec, 100 is safe default


# ── HTTP event emitter ─────────────────────────────────────────────────────────

def emit_event_http(events: List[Dict[str, Any]], api_url: str = None) -> dict:
    """POST a batch of events to the Store Intelligence API ingest endpoint.

    Args:
        events: list of dicts matching the Event schema
        api_url: base URL, defaults to http://localhost:8000 or SI_API_URL env

    Returns:
        dict with status, accepted, duplicates_ignored, rejected fields
        or None on failure
    """
    api_url = api_url or os.getenv("SI_API_URL", "http://localhost:8000")
    url = f"{api_url.rstrip('/')}/events/ingest"

    try:
        resp = requests.post(url, json={"events": events}, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.ConnectionError:
        print(f"  ❌ Connection refused — is API running at {api_url}?")
        return None
    except requests.exceptions.Timeout:
        print(f"  ❌ Request timed out after 30s")
        return None
    except requests.exceptions.HTTPError as exc:
        print(f"  ❌ HTTP error: {exc.response.status_code} — {exc.response.text[:200]}")
        return None
    except Exception as exc:
        print(f"  ❌ emit_event_http error: {exc}")
        return None


# ── Local WebSocket publisher ──────────────────────────────────────────────────

async def _call_local_publish(event: Dict[str, Any]):
    """Try importing the local WebSocket publisher (works when pipeline runs
    inside the same Python process as the FastAPI app)."""
    try:
        from app.ws import publish_event
        await publish_event(event)
    except ImportError:
        pass  # not running in same process — expected
    except Exception as exc:
        print(f"  ⚠️ local ws publish failed: {exc}")


def emit_event_local(event: Dict[str, Any]):
    """Publish a single event via local WebSocket for real-time dashboard.

    This is useful when the detection pipeline runs inside the same Python
    process as the FastAPI app (for demos / simulated real-time).
    It is a no-op if the app module is not importable.
    """
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        asyncio.create_task(_call_local_publish(event))
    else:
        new_loop = asyncio.new_event_loop()
        try:
            new_loop.run_until_complete(_call_local_publish(event))
        finally:
            new_loop.close()


# ── File-based batch emitter ───────────────────────────────────────────────────

def emit_from_file(input_path: str, api_url: str = "http://localhost:8000",
                   batch_size: int = BATCH_SIZE):
    """Read a JSONL file and POST events to the API in batches.

    Args:
        input_path: path to .jsonl file with one event JSON per line
        api_url: base URL of the Store Intelligence API
        batch_size: number of events per batch (default 100, max 500)
    """
    print(f"\n📂 Reading events from: {input_path}")

    # Validate file exists
    if not os.path.exists(input_path):
        print(f"  ❌ File not found: {input_path}")
        return

    # Read events
    events = []
    skipped_lines = 0
    with open(input_path) as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
                events.append(event)
            except json.JSONDecodeError:
                skipped_lines += 1
                if skipped_lines <= 3:
                    print(f"  ⚠️ Skipped malformed JSON on line {line_num}")

    if not events:
        print("  ⚠️ No valid events found in file")
        return

    if skipped_lines > 3:
        print(f"  ⚠️ Skipped {skipped_lines} total malformed lines")

    total_batches = (len(events) + batch_size - 1) // batch_size
    print(f"  📤 Sending {len(events)} events in {total_batches} batches of {batch_size}...")
    print()

    total_accepted = 0
    total_duplicates = 0
    total_rejected = 0
    failed_batches = 0
    start_time = time.time()

    for i in range(0, len(events), batch_size):
        batch = events[i:i + batch_size]
        batch_num = i // batch_size + 1

        result = emit_event_http(batch, api_url)

        if result:
            accepted = result.get("accepted", 0)
            duplicates = result.get("duplicates_ignored", 0)
            rejected_list = result.get("rejected", [])
            rejected = len(rejected_list) if isinstance(rejected_list, list) else 0

            total_accepted += accepted
            total_duplicates += duplicates
            total_rejected += rejected

            status_icon = "✅" if accepted > 0 else "🔁"
            print(f"  {status_icon} Batch {batch_num}/{total_batches}: "
                  f"accepted={accepted} dupes={duplicates} rejected={rejected}")

            # Log rejected event details (first 3)
            if rejected_list and isinstance(rejected_list, list):
                for rej in rejected_list[:3]:
                    if isinstance(rej, dict):
                        print(f"     ⚠️ Rejected: {rej.get('event_id', '?')} — "
                              f"{rej.get('reason', 'unknown')}")

        else:
            # First attempt failed — retry once after delay
            failed_batches += 1
            print(f"  ❌ Batch {batch_num}/{total_batches}: FAILED — retrying in 2s...")
            time.sleep(2)

            result = emit_event_http(batch, api_url)
            if result:
                accepted = result.get("accepted", 0)
                duplicates = result.get("duplicates_ignored", 0)
                total_accepted += accepted
                total_duplicates += duplicates
                failed_batches -= 1  # retry succeeded
                print(f"  ✅ Batch {batch_num}/{total_batches}: retry succeeded — "
                      f"accepted={accepted}")
            else:
                print(f"  ❌ Batch {batch_num}/{total_batches}: retry FAILED — skipping batch")

    elapsed = round(time.time() - start_time, 2)

    # Final summary
    print()
    print("=" * 50)
    print(f"📊 Emit Summary")
    print(f"=" * 50)
    print(f"   File:       {input_path}")
    print(f"   Total:      {len(events)} events")
    print(f"   Accepted:   {total_accepted}")
    print(f"   Duplicates: {total_duplicates}")
    print(f"   Rejected:   {total_rejected}")
    print(f"   Failed:     {failed_batches} batches")
    print(f"   Time:       {elapsed}s")
    print(f"=" * 50)


# ── CLI entry point ────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Emit events from JSONL file to Store Intelligence API"
    )
    parser.add_argument("--input", required=True,
                        help="Path to JSONL events file")
    parser.add_argument("--api_url", default="http://localhost:8000",
                        help="API base URL (default: http://localhost:8000)")
    parser.add_argument("--batch_size", type=int, default=BATCH_SIZE,
                        help=f"Events per batch (default: {BATCH_SIZE}, max: 500)")
    parser.add_argument("--no-health-check", action="store_true",
                        help="Skip API health check before sending")
    args = parser.parse_args()

    # Clamp batch size
    if args.batch_size > 500:
        print(f"  ⚠️ batch_size capped at 500 (was {args.batch_size})")
        args.batch_size = 500

    # Check API is running
    if not args.no_health_check:
        print(f"🔍 Checking API at {args.api_url}...")
        try:
            resp = requests.get(f"{args.api_url}/health", timeout=5)
            health = resp.json()
            status = health.get("status", "unknown")
            stores = list(health.get("stores", {}).keys())
            print(f"  ✅ API status: {status}")
            if stores:
                print(f"  📍 Active stores: {', '.join(stores)}")
        except requests.exceptions.ConnectionError:
            print(f"  ❌ API not reachable at {args.api_url}")
            print(f"     Start it with: docker compose up")
            sys.exit(1)
        except Exception as e:
            print(f"  ⚠️ Health check failed: {e} — continuing anyway...")

    # Emit events
    emit_from_file(args.input, args.api_url, args.batch_size)


if __name__ == "__main__":
    main()