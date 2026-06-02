import pytest
import sqlite3
import os
import gc


@pytest.fixture(autouse=True)
def cleanup_db():
    """Auto cleanup to close any lingering DB connections between tests."""
    gc.collect()
    yield
    gc.collect()
    # Close any open sqlite connections
    for obj in gc.get_objects():
        if isinstance(obj, sqlite3.Connection):
            try:
                obj.close()
            except Exception:
                pass
