# Internet Uptime Monitor

**Version 20260518a**

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

On first launch `config.json` is read and `uptime_monitor.db` is created in the same
directory. Both files persist between sessions.

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
├── uptime_monitor.db     SQLite database — created on first run, grows over time.
├── requirements.txt      pip dependency list.
│
└── gui/
    ├── __init__.py
    ├── main_window.py    Main application window, polling engine, event log.
    ├── setup_dialog.py   Modal "Configure…" dialog (three tabs).
    └── graph_panel.py    matplotlib graph widget with view/range controls.
```

---

## Configuration (`config.json`)

```json
{
  "polling_interval_seconds": 60,
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

| Key | Type | Description |
|---|---|---|
| `polling_interval_seconds` | integer | Seconds between poll cycles. Range: 10–3600. |
| `dns_providers` | array | List of `{"name": "…", "server": "…"}` objects. |
| `dns_providers[].name` | string | Display name shown in graphs and the Setup dialog. |
| `dns_providers[].server` | string | IPv4 address of the DNS resolver. |
| `domains` | array | Domain names to resolve each cycle. |

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
                                                 │  insert_ip_log (if IP changed)
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

Tries `https://ipinfo.io/json` first, then `https://ipapi.co/json/` as a
fallback. Both return a JSON body containing the public IP and an `org` field
with the format `"AS12345 ISP Name"`. The ASN prefix is stripped so only the
human-readable ISP name is displayed. If both services are unreachable the
function returns `None` and the status bar retains its previous value.

The IP log is append-only and a new row is inserted **only when the IP
changes**. This makes it easy to see exactly when your ISP switched you to a
different address or when a failover to your backup ISP occurred.

### Storage (`database.py`)

SQLite via Python's built-in `sqlite3`. The database file is created next to the
script on first run and is never deleted by the app.

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
| `timestamp` | REAL | Unix epoch when IP was first seen. |
| `public_ip` | TEXT | Dotted-decimal IPv4 string. |
| `isp_name` | TEXT | Human-readable ISP name. |
| `org` | TEXT | Raw `org` field, e.g. `"AS7922 Comcast"`. |

Indexed on `timestamp`.

#### Data retention

Both tables are pruned automatically. On startup, and then once every 24 hours,
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
blue = informational (IP change, config update, start/stop).

#### `SetupDialog` (setup_dialog.py)

A modal `Toplevel` window with three notebook tabs:

- **DNS Providers** — listbox of current providers; select one to populate the
  name/server fields below; Add / Update / Remove buttons.
- **Domains** — listbox of domains; Enter key or Add button appends; Remove
  deletes the selected entry.
- **Settings** — `Spinbox` for the polling interval (10–3600 s).

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
`uptime_log_YYYYMMDD_HHMMSS.txt`) and writes all database records to a
human-readable text file with two sections: an IP/ISP log and a full DNS
results table. A confirmation entry is added to the Event Log on success.

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
switchover occurs. The status bar always shows the current public IP and ISP name.
The `ip_log` table gives you a complete switchover history with timestamps, which
you can query directly:

```sql
SELECT datetime(timestamp, 'unixepoch', 'localtime') AS time,
       public_ip, isp_name
FROM   ip_log
ORDER  BY timestamp;
```

---

## Querying the Database Directly

The SQLite database at `uptime_monitor.db` can be opened with any SQLite browser
(e.g. [DB Browser for SQLite](https://sqlitebrowser.org/)) or queried from the
command line:

```bash
sqlite3 uptime_monitor.db
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

-- All IP changes with human-readable timestamps
SELECT datetime(timestamp, 'unixepoch', 'localtime') AS time,
       public_ip, isp_name
FROM   ip_log
ORDER  BY timestamp;

-- Worst 10 individual DNS lookups
SELECT datetime(timestamp, 'unixepoch', 'localtime') AS time,
       dns_provider, domain, response_time_ms
FROM   dns_results
WHERE  success = 1
ORDER  BY response_time_ms DESC
LIMIT  10;
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
check that the selected time range matches when data was collected.

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
