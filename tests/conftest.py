"""Redirect database to a temporary directory so tests never touch the real DB."""
import os
import tempfile
import pytest


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    """Point APP_DIR and DB_FILE at a temporary directory for every test."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    # Re-import so module-level constants pick up the patched APPDATA.
    import importlib
    import database
    importlib.reload(database)
    yield
    importlib.reload(database)  # restore original state after the test
