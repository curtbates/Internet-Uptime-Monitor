import sqlite3
from pathlib import Path
import os

# Store the database in %APPDATA% so it lives outside of any cloud-sync folder
# (e.g. Google Drive, OneDrive). Syncing a SQLite file that is written to every
# few seconds causes Google Drive to create conflict copies and can silently
# replace the local database with an older cloud version, wiping accumulated data.
_APP_DIR = Path(os.environ.get("APPDATA", Path.home())) / "InternetUptimeMonitor"
_APP_DIR.mkdir(parents=True, exist_ok=True)
DB_FILE = _APP_DIR / "uptime_monitor.db"


def _connect():
    # Open a new connection each call. SQLite connections are cheap and this
    # avoids sharing a single connection across threads.
    conn = sqlite3.connect(str(DB_FILE))
    # Row factory makes each row behave like a dict, so callers can use
    # row["column_name"] instead of positional indices.
    conn.row_factory = sqlite3.Row
    return conn


def initialize_db():
    # executescript runs multiple statements in one call and wraps them in a
    # transaction automatically. "IF NOT EXISTS" makes this safe to call on
    # every startup without wiping existing data.
    with _connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS dns_results (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp        REAL    NOT NULL,   -- Unix epoch seconds (float)
                dns_provider     TEXT    NOT NULL,   -- e.g. "Google"
                domain           TEXT    NOT NULL,   -- e.g. "google.com"
                response_time_ms REAL,               -- NULL when the query failed
                success          INTEGER NOT NULL    -- 1 = resolved OK, 0 = failed
            );
            -- Index on timestamp so time-range queries (WHERE timestamp >= ?) are fast
            -- even when the table grows to millions of rows.
            CREATE INDEX IF NOT EXISTS idx_dns_ts ON dns_results(timestamp);

            CREATE TABLE IF NOT EXISTS ip_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp   REAL NOT NULL,   -- when this IP was first observed
                public_ip   TEXT NOT NULL,   -- dotted-decimal IPv4 string
                isp_name    TEXT,            -- human-readable ISP name (may be NULL)
                org         TEXT,            -- raw "AS12345 ISP Name" from the API
                public_ipv6 TEXT             -- IPv6 address, NULL when not available
            );
            CREATE INDEX IF NOT EXISTS idx_ip_ts ON ip_log(timestamp);
        """)
        # Migrate databases created before the public_ipv6 column was added.
        existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(ip_log)")}
        if "public_ipv6" not in existing_cols:
            conn.execute("ALTER TABLE ip_log ADD COLUMN public_ipv6 TEXT")


def insert_dns_result(timestamp, dns_provider, domain, response_time_ms, success):
    # Use parameterised queries (?) throughout to prevent SQL injection from
    # any user-supplied provider names or domain strings.
    with _connect() as conn:
        conn.execute(
            "INSERT INTO dns_results (timestamp, dns_provider, domain, response_time_ms, success) "
            "VALUES (?, ?, ?, ?, ?)",
            (timestamp, dns_provider, domain, response_time_ms, int(success)),
        )


def insert_ip_log(timestamp, public_ip, isp_name=None, org=None, public_ipv6=None):
    with _connect() as conn:
        conn.execute(
            "INSERT INTO ip_log (timestamp, public_ip, isp_name, org, public_ipv6) VALUES (?, ?, ?, ?, ?)",
            (timestamp, public_ip, isp_name, org, public_ipv6),
        )


def get_dns_results(since_timestamp):
    # Fetch all rows newer than the given Unix timestamp, ordered oldest-first
    # so the graph plots left-to-right in chronological order.
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM dns_results WHERE timestamp >= ? ORDER BY timestamp",
            (since_timestamp,),
        ).fetchall()
    # Convert sqlite3.Row objects to plain dicts so callers don't need to
    # import sqlite3 just to access results.
    return [dict(r) for r in rows]


def get_ip_log(since_timestamp=0):
    # Default of 0 means "all records ever" when called without an argument.
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM ip_log WHERE timestamp >= ? ORDER BY timestamp",
            (since_timestamp,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_distinct_isps():
    with _connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT isp_name FROM ip_log WHERE isp_name IS NOT NULL ORDER BY isp_name"
        ).fetchall()
    return [r["isp_name"] for r in rows]


def get_latest_ip():
    # Used on startup to pre-populate the status bar before the first live poll
    # completes, so the user sees their last-known IP immediately.
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM ip_log ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def purge_old_records(max_age_days=10):
    import time
    cutoff = time.time() - max_age_days * 86400
    with _connect() as conn:
        conn.execute("DELETE FROM dns_results WHERE timestamp < ?", (cutoff,))
        conn.execute("DELETE FROM ip_log WHERE timestamp < ?", (cutoff,))
