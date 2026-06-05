# Internet Uptime Monitor

**Version 20260605a**

A desktop application for monitoring your ISP's reliability by measuring DNS lookup
performance across multiple DNS providers, tracking your public IP address, and
graphing historical data over time.

---

## What It Does

Most home internet problems show up as slow or failed DNS lookups before anything
else. This tool exploits that fact: on a configurable interval it sends DNS A-record
queries for your chosen domain names to each of your chosen DNS resolvers, records
whether each query succeeded and how long it took, and logs your public IP address
any time it changes. Everything is stored in a local SQLite database and rendered as
an interactive time-series graph inside the GUI.

**Primary use cases:**

- Catching intermittent outages your ISP would otherwise deny
- Building a timestamped log of failures to share with support
- Verifying that a failover to a backup ISP actually happened (the public IP changes)
- Comparing the responsiveness of different DNS resolvers over time

The app polls while it is open or minimized to the system tray. It produces no
background processes or scheduled tasks — closing it from the tray menu fully stops
polling.

---

## Requirements

| Requirement | Version |
|---|---|
| Python | 3.10 or newer |
| dnspython | >= 2.3.0 |
| requests | >= 2.28.0 |
| matplotlib | >= 3.6.0 |
| pystray | >= 0.19.0 |
| Pillow | >= 9.0.0 |

Python's built-in `tkinter` is used for the GUI and `sqlite3` for storage — no extra
packages are required for either.

> **Windows note:** tkinter ships with the standard Python installer from python.org.
> If you installed Python via the Microsoft Store, tkinter may be missing; use the
> python.org installer instead.

> **Linux note:** Install tkinter via your package manager if it is not present:
> `sudo apt install python3-tk` (Debian/Ubuntu) or `sudo dnf install python3-tkinter`
> (Fedora). The system tray also requires an AppIndicator library:
> `sudo apt install libayatana-appindicator3-1` (Debian/Ubuntu).

---

## Installation

```
# 1. Clone or download the project folder
cd "Internet Uptime Monitor"

# 2. (Optional but recommended) create a virtual environment
python -m venv .venv

# Windows
.venv\Scripts\activate

# Linux / macOS
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt
```

---

## Running the App

**With a terminal (shows log output):**
```
python main.py
```

**Without a terminal (double-click or shortcut):**
```
pythonw main.pyw
```

`main.pyw` is identical in behaviour to `main.py`. On Windows, `pythonw` (used
automatically for `.pyw` files) launches the app without opening a console window.
If a required package is missing, a messagebox is shown instead of a console error.

On first launch `config.json` is read and `uptime_monitor.db` is created in
`%APPDATA%\InternetUptimeMonitor\` (Windows) or `~/.InternetUptimeMonitor/` (Linux/macOS).
Both files persist between sessions.

---

## Quick Start

1. Launch the app with `python main.py`.
2. Optionally open **Setup → Configure…** to change DNS providers, domains, or the
   polling interval.
3. Click **▶ Start Monitoring**. The first poll runs immediately; subsequent polls run
   after the configured interval.
4. Watch the Event Log for per-poll results and the graph for historical trends.
5. Click **■ Stop Monitoring** to pause polling, or minimize/close the window to send
   the app to the system tray (polling continues). Right-click the tray icon to
   restore, toggle monitoring, or exit completely.

---

## File Layout

```
Internet Uptime Monitor/
│
├── main.py               Entry point (terminal). Checks dependencies, starts tkinter event loop.
├── main.pyw              Entry point (no terminal). Same as main.py; errors shown via messagebox.
├── config_manager.py     Loads and saves config.json.
├── database.py           All SQLite operations (schema, inserts, queries, cleanup).
├── dns_checker.py        Sends a single DNS A-record query to a specific server.
├── ip_tracker.py         Fetches public IP and ISP name from external APIs.
│
├── config.json           User configuration (edited via Setup dialog or directly).
├── requirements.txt      pip dependency list.
│
└── gui/
    ├── __init__.py
    ├── main_window.py    Main application window, polling engine, event log.
    ├── setup_dialog.py   Modal "Configure…" dialog (four tabs).
    └── graph_panel.py    matplotlib graph widget with view/range controls.

The SQLite database and optional event log are stored outside the project folder to
avoid cloud-sync conflicts:

  Windows   %APPDATA%\InternetUptimeMonitor\uptime_monitor.db
            %APPDATA%\InternetUptimeMonitor\event.log   (only when "Save Event Log" is enabled)
  Linux     ~/.InternetUptimeMonitor/uptime_monitor.db
            ~/.InternetUptimeMonitor/event.log          (only when "Save Event Log" is enabled)
```

---

## Configuration (`config.json`)

```json
{
  "polling_interval_seconds": 60,
  "log_only_incomplete_dns": true,
  "save_event_log": false,
  "log_ip_success": true,
  "dns_providers": [
    {"name": "Google",     "server": "8.8.8.8"},
    {"name": "Cloudflare", "server": "1.1.1.1"},
    {"name": "OpenDNS",    "server": "208.67.222.222"},
    {"name": "Quad9",      "server": "9.9.9.9"},
    {"name": "Comodo",     "server": "8.26.56.26"}
  ],
  "domains": ["google.com", "amazon.com", "cloudflare.com", "microsoft.com", "github.com"]
}
```

| Key | Type | Default | Description |
|---|---|---|---|
| `polling_interval_seconds` | integer | 60 | Seconds between poll cycles. Range: 10–3600. |
| `log_only_incomplete_dns` | boolean | true | When true, the DNS poll summary line is only written to the Event Log when at least one query failed. When false, every poll result is logged. |
| `save_event_log` | boolean | false | When true, every Event Log message is appended to `event.log` in the app data directory. When false, no file is written and any existing file is deleted. |
| `log_ip_success` | boolean | true | When true, IP address change and detection messages are written to the Event Log. When false, only IP check failure messages are logged. |
| `dns_providers` | array | (5 providers) | List of `{"name": "…", "server": "…"}` objects. |
| `dns_providers[].name` | string | — | Display name shown in graphs and the Setup dialog. |
| `dns_providers[].server` | string | — | IPv4 address of the DNS resolver. |
| `domains` | array | (5 domains) | Domain names to resolve each cycle. |

You can edit `config.json` directly with a text editor, or use **Setup → Configure…**
in the app. Changes made in the dialog take effect immediately without restarting.

**Per-cycle query count:** `len(dns_providers) × len(domains)`. With the default
5 providers and 5 domains that is 25 queries per poll.

---

## How the Code Works

### Startup sequence (`main.py` → `MainWindow.__init__`)

1. `main.py` imports each dependency and exits with a readable error if any is
   missing, then creates the root `tk.Tk` window and hands it to `MainWindow`.
2. `MainWindow.__init__` calls `initialize_db()` (creates tables if absent),
   loads `config.json`, builds all GUI widgets, starts the 200 ms queue-drain
   loop (`_queue_check`), and starts the daily database purge timer
   (`_schedule_daily_purge`).
3. The last known public IP is read from the database and pre-populated in the
   status bar so it is visible before the first poll completes.

### Polling cycle

```
tkinter after()  →  _fire_poll()  →  Thread: _poll_worker()
                                            │
                         ┌──────────────────┘
                         │  for each provider × domain:
                         │      dns_checker.check_dns(server, domain)
                         │  ip_tracker.get_public_ip_info()
                         └──────────────────────────────────────────►  queue.put(result)

tkinter after(200ms)  →  _queue_check()  →  _handle()
                                                 │  insert_dns_result × N
                                                 │  insert_ip_log (if IPv4 or IPv6 changed)
                                                 │  insert_ip_failure (if IP check failed)
                                                 │  update status bar
                                                 │  graph_panel.refresh()
                                                 └──►  schedule next poll via after()
```

DNS queries and the IP lookup run in a **daemon thread** so the GUI never freezes.
The thread puts one tuple into a `queue.Queue` when done. The main thread drains
the queue every 200 ms via `root.after()` — the only thread-safe way to update
tkinter widgets.

### DNS checking (`dns_checker.py`)

Uses **dnspython** with `configure=False` so the system resolver is bypassed
entirely. Each call creates a fresh `Resolver`, sets `nameservers` to the target
IP, and sets both `timeout` and `lifetime` to 5 seconds. Timing uses
`time.perf_counter()` for sub-millisecond precision. Any exception (timeout,
NXDOMAIN, network error, etc.) returns `(False, None)`; a successful A-record
response returns `(True, response_time_ms)`.

### IP and ISP detection (`ip_tracker.py`)

**IPv4** is fetched by trying `https://ipinfo.io/json` first, then
`https://ipapi.co/json/` as a fallback. Requests to those services are forced
over IPv4 via a custom `HTTPAdapter` (`_ForceIPv4Adapter`) that pins
`urllib3`'s address-family preference to `AF_INET` for the duration of the
call. This ensures the service always echoes back the machine's IPv4 address
rather than its IPv6 address on dual-stack connections.

**IPv6** is fetched separately by hitting `https://api6.ipify.org?format=json`,
an endpoint that has only AAAA DNS records and therefore only responds over
IPv6. If the machine has no IPv6 connectivity the connection fails silently and
`None` is returned for the IPv6 field.

Both services return an `org` field in the format `"AS12345 ISP Name"`. The ASN
prefix is stripped so only the human-readable ISP name is stored and displayed.
If all services are unreachable `get_public_ip_info()` returns `None` and the
status bar retains its previous value.

IPv4 and IPv6 are tracked **independently** — a new `ip_log` row is inserted
whenever either address changes. This captures ISP failovers (IPv4 change) and
IPv6 prefix rotations (IPv6-only change) as separate, timestamped events in the
event log and database. When only IPv6 changes, the current IPv4 address is also
written to the event log for context so both addresses are always visible together.

### Storage (`database.py`)

SQLite via Python's built-in `sqlite3`. The database is stored in a platform-specific
application data directory (`%APPDATA%\InternetUptimeMonitor\` on Windows) rather than
next to the script, so it is not affected by cloud-sync tools (Google Drive, OneDrive,
etc.) that sit in the project folder. The file is never deleted by the app.

#### `dns_results` table

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | Auto-increment. |
| `timestamp` | REAL | Unix epoch (seconds, float). |
| `dns_provider` | TEXT | Provider name from config. |
| `domain` | TEXT | Domain that was queried. |
| `response_time_ms` | REAL | NULL when `success = 0`. |
| `success` | INTEGER | 1 = resolved, 0 = failed. |

Indexed on `timestamp` for fast range queries.

#### `ip_log` table

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | Auto-increment. |
| `timestamp` | REAL | Unix epoch when this address state was first observed. |
| `public_ip` | TEXT | Dotted-decimal IPv4 string. |
| `isp_name` | TEXT | Human-readable ISP name. |
| `org` | TEXT | Raw `org` field, e.g. `"AS7922 Comcast"`. |
| `public_ipv6` | TEXT | IPv6 address string; NULL when the host has no IPv6. |

A new row is written whenever **either** `public_ip` or `public_ipv6` changes.
Indexed on `timestamp`.

#### `ip_failures` table

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | Auto-increment. |
| `timestamp` | REAL | Unix epoch when the failure was first detected. |

One row is written each time a previously-working IP check transitions to failure
(i.e. the first poll that returns `None` after a successful poll). Subsequent
back-to-back failures within the same outage do not add rows. Recovery is recorded
implicitly via the next `ip_log` row when the address is re-detected.
Indexed on `timestamp`.

#### Data retention

All three tables are pruned automatically. On startup, and then once every 24 hours,
`purge_old_records()` deletes all rows whose `timestamp` is older than 10 days.
The database file itself is never deleted by the app.

### Configuration management (`config_manager.py`)

`load_config()` reads `config.json` from the script directory. If the file is
absent or unparseable, `DEFAULT_CONFIG` is returned (the same defaults shown in
the *Configuration* section above). `save_config()` writes the dict back as
pretty-printed JSON.

### GUI structure (`gui/`)

#### `MainWindow` (main_window.py)

The top-level window is assembled from four regions packed into the root window:

```
┌─────────────────────────────────────────────────────┐
│ Menu bar  [File | Setup | Help]                      │
├─────────────────────────────────────────────────────┤
│ Status bar  IP │ ISP │ ● Status │ Last check │ Next  │
├─────────────────────────────────────────────────────┤
│                                                     │
│   Graph panel  (view controls + matplotlib canvas)  │
│                                                     │
├─────────────────────────────────────────────────────┤
│ Event Log  (scrollable, colour-coded text)          │
├─────────────────────────────────────────────────────┤
│ ▶ Start Monitoring          Interval: 60 s          │
└─────────────────────────────────────────────────────┘
```

Event log colours: green = all queries succeeded, red = all failed,
blue = informational (IPv4 change, IPv6 change, config update, start/stop).

#### `SetupDialog` (setup_dialog.py)

A modal `Toplevel` window with four notebook tabs:

- **DNS Providers** — listbox of current providers; select one to populate the
  name/server fields below; Add / Update / Remove buttons.
- **Domains** — listbox of domains; Enter key or Add button appends; Remove
  deletes the selected entry.
- **Settings** — `Spinbox` for the polling interval (10–3600 s).
- **Event Log** — three checkboxes that control what is written to the on-screen
  Event Log and the optional log file:
  - *Log only incomplete DNS response result messages* — when checked (default),
    the per-poll DNS summary line is suppressed if every query succeeded; it is
    always shown when any query fails.
  - *Log IP successful detection messages* — when checked (default), IP address
    change and detection events appear in the Event Log; when unchecked, only IP
    check failure messages are shown.
  - *Save Event Log* — when checked, every Event Log message is appended to
    `event.log` in the app data directory (`%APPDATA%\InternetUptimeMonitor\` on
    Windows). Unchecking immediately deletes the file.

The dialog operates on a deep copy of the config dict; changes are only applied
to the live config when **Save** is clicked. **Cancel** discards all edits.

#### `GraphPanel` (graph_panel.py)

Embeds a `matplotlib` `Figure` inside the tkinter window using
`FigureCanvasTkAgg`. The matplotlib `NavigationToolbar2Tk` is shown below the
canvas, providing pan, zoom, and save-image controls.

The controls bar contains three groups:

| Control | Purpose |
|---|---|
| View radio buttons | Switch between By Provider, By Domain, and Summary Score |
| Range combobox | Select the time window (1 h – 7 d) |
| ISP combobox | Filter all graph views to a single ISP, or show All ISPs |

The ISP list is populated from the `ip_log` table and refreshed on every
`refresh()` call, so newly detected ISPs appear automatically. When a specific
ISP is selected, each DNS result is matched against the `ip_log` transition
history to determine which ISP was active at that timestamp; only matching
results are plotted.

`refresh()` is called automatically after every poll and whenever any control
changes.

#### System tray (`main_window.py` — tray methods)

When `pystray` and `Pillow` are installed the app gains system-tray support.
Minimizing or clicking the window's X button calls `_hide_to_tray()`, which
withdraws the window from the taskbar, constructs a `pystray.Icon`, and starts
it in a daemon thread. A Pillow-drawn 64×64 circle is used as the icon image;
its colour reflects the current monitoring state and connection quality:

| Colour | Meaning |
|---|---|
| Blue | Not monitoring |
| Green | Monitoring — summary score > 80 (good) |
| Yellow | Monitoring — summary score 50–80 (degraded) |
| Red | Monitoring — summary score < 50 (poor) |

The score used for the icon is the same weighted formula as the Summary Score
graph view and is recomputed after every poll.

```
minimize / close  →  _on_unmap() / _on_close()
                          │
                          └──►  _hide_to_tray()
                                    │  root.withdraw()           ← removes taskbar entry
                                    │  pystray.Icon.run()        ← daemon thread
                                    └──────────────────────────────────────────────────►
                                                        tray icon visible

double-click / "Show"  →  _tray_restore()  →  root.after(0, _restore_from_tray())
                                                    │  icon.stop()
                                                    │  root.deiconify()
                                                    └──►  window back on screen
```

All pystray callbacks run in the pystray thread; any call that touches tkinter
widgets is marshalled back to the main thread with `root.after(0, fn)`.

The tray icon's tooltip and image are refreshed after every poll and every
start/stop toggle via `_update_tray()`, so the tray always reflects the current
monitoring state and last-check time.

The tray right-click menu provides:

| Item | Action |
|---|---|
| **Show** (default, double-click) | Restores the window |
| **Start / Stop Monitoring** | Toggles polling without opening the window |
| **Exit** | Stops polling and quits the process |

**File → Export to Log File…** opens a save dialog (default filename
`uptime_log_YYYYMMDD_HHMMSS.txt`) and writes a human-readable text file with
three sections:

- **DNS FAILURES** — the subset of DNS results where the query failed, with
  timestamp, provider, domain, and the IPv4, IPv6, and ISP active at that moment.
- **IP CHECK FAILURES** — each moment the public IP lookup failed (transitions
  from working to `None`), with the timestamp of when the failure was first
  detected.
- **DNS RESULTS** — every DNS query with timestamp, provider, domain, response
  time, success flag, and the IPv4, IPv6, and ISP active at that moment (resolved
  from the `ip_log` transition history).

A confirmation entry is added to the Event Log on success.

**File → Exit** also fully quits (bypasses the tray). If `pystray` is not
installed the window falls back to normal minimize/close behaviour.

---

## Graph Views

### By Provider

One line per DNS provider. Each data point is the **average response time (ms)**
of all successful queries to that provider within a time bucket. Failed queries
are excluded from the average; if a provider had no successes in a bucket it
simply has no data point there (gap in the line).

### By Domain

One line per domain. Each data point is the **average response time (ms)** across
all providers that successfully resolved that domain within a time bucket.

### Summary Score

A single 0–100 composite score computed per time bucket:

```
success_rate  =  successful_queries / total_queries

rt_score      =  clamp(1 − (avg_response_ms − 20) / 480, 0, 1)
               # 20 ms → 1.0 (perfect),  500 ms → 0.0 (worst)

score         =  (success_rate × 0.70  +  rt_score × 0.30) × 100
```

Reference lines: **orange dashed at 80** (Good), **red dashed at 50** (Poor).
The area under the score line is shaded blue.

### Time Ranges and Bucket Sizes

| Range | Bucket size | Typical use |
|---|---|---|
| Last 1 h | 1 minute | Real-time troubleshooting |
| Last 6 h | 5 minutes | Same-day overview |
| Last 24 h | 15 minutes | Daily pattern |
| Last 7 d | 1 hour | Weekly trend |

The x-axis uses matplotlib's `ConciseDateFormatter` which automatically selects
the most readable label format for the selected range.

---

## Dual-ISP Tracking

If you have a primary and a backup ISP configured with automatic failover, you
will see the public IP change in the Event Log and `ip_log` table every time a
switchover occurs. The status bar always shows the current public IPv4 and ISP name.
IPv4 and IPv6 are tracked independently — a rotation of either address generates
its own timestamped event log entry and `ip_log` row.
The `ip_log` table gives you a complete switchover history with timestamps, which
you can query directly:

```sql
SELECT datetime(timestamp, 'unixepoch', 'localtime') AS time,
       public_ip, public_ipv6, isp_name
FROM   ip_log
ORDER  BY timestamp;
```

---

## Querying the Database Directly

The SQLite database can be opened with any SQLite browser
(e.g. [DB Browser for SQLite](https://sqlitebrowser.org/)) or queried from the
command line:

```bash
# Windows (PowerShell)
sqlite3 "$env:APPDATA\InternetUptimeMonitor\uptime_monitor.db"

# Linux / macOS
sqlite3 ~/.InternetUptimeMonitor/uptime_monitor.db
```

Useful queries:

```sql
-- Failure rate per provider over the last 24 hours
SELECT dns_provider,
       COUNT(*) AS total,
       SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) AS failures,
       ROUND(AVG(response_time_ms), 1) AS avg_ms
FROM   dns_results
WHERE  timestamp > unixepoch('now') - 86400
GROUP  BY dns_provider;

-- All IP / IPv6 changes with human-readable timestamps
SELECT datetime(timestamp, 'unixepoch', 'localtime') AS time,
       public_ip, public_ipv6, isp_name
FROM   ip_log
ORDER  BY timestamp;

-- Worst 10 individual DNS lookups
SELECT datetime(timestamp, 'unixepoch', 'localtime') AS time,
       dns_provider, domain, response_time_ms
FROM   dns_results
WHERE  success = 1
ORDER  BY response_time_ms DESC
LIMIT  10;

-- All IP check failure events (outage start times)
SELECT datetime(timestamp, 'unixepoch', 'localtime') AS failure_detected
FROM   ip_failures
ORDER  BY timestamp;
```

---

## Troubleshooting

**"Missing dependencies" on launch**
Run `pip install -r requirements.txt`. If you use multiple Python installs,
make sure you are running the same `python` that has the packages installed.
When launching via `main.pyw` the error appears as a messagebox rather than
in a terminal.

**Graph shows "No data available"**
Click **▶ Start Monitoring** and wait for at least one poll to complete. Also
check that the selected time range matches when data was collected (the database
keeps only the last 10 days).

**History disappears after running for several days (cloud sync conflict)**
If the project folder lives inside Google Drive, OneDrive, or a similar sync
service, the sync tool may silently replace `uptime_monitor.db` with a stale
cloud copy, wiping accumulated data. The app stores its database in
`%APPDATA%\InternetUptimeMonitor\` (outside the project folder) specifically to
avoid this. If you cloned or copied the project into a sync folder and are still
seeing conflict copies (files named `uptime_monitor (1).db`, etc.), delete those
files — they are harmless leftovers and the real database is no longer stored
there.

**All DNS queries fail**
Check that the server IPs in your config are reachable — some corporate or
home networks block outbound UDP/TCP port 53 to third-party resolvers. Try
changing one provider to your router's IP (e.g. `192.168.1.1`).

**IP always shows "Unknown" or never updates**
The app queries `ipinfo.io` and `ipapi.co`; both require outbound HTTPS access.
If your network blocks them the IP display will not update, but DNS monitoring
continues normally.

**High response times to all providers**
This is expected if you are measuring from a busy or distant machine. The
absolute numbers matter less than the trend — a sudden spike across all
providers indicates a network event.

**tkinter not found (Linux)**
```
sudo apt install python3-tk          # Debian / Ubuntu
sudo dnf install python3-tkinter     # Fedora / RHEL
```

**System tray icon does not appear (Linux)**
pystray requires an AppIndicator backend on Linux. Install it with:
```
sudo apt install libayatana-appindicator3-1   # Debian / Ubuntu
```
If the library is unavailable the app still runs normally; minimizing just
iconifies the window in the taskbar instead of going to the tray.

**Tray icon stays after the app crashes**
On Windows the ghost icon disappears as soon as you hover over it. On Linux,
kill any lingering process with `pkill -f main.py`.
