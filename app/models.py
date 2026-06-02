"""
# PROMPT: Generate database initialization for SQLite event storage
# CHANGES MADE: Simplified to match raw SQLite schema used across all endpoints,
# extra event fields stored in payload JSON column.
"""

import sqlite3
import os

DB_PATH = os.path.join("data", "events.db")


def init_db():
    os.makedirs("data", exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS events (
            event_id TEXT PRIMARY KEY,
            store_id TEXT,
            camera_id TEXT,
            visitor_id TEXT,
            event_type TEXT,
            timestamp TEXT,
            payload TEXT
        )
    """)
    conn.commit()
    conn.close()