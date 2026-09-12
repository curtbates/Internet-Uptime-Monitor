import queue
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, ttk

from config_manager import load_config, save_config
from database import (
    APP_DIR, initialize_db, purge_old_records,
    get_latest_ip, get_dns_results, get_ip_log, get_ip_failures,
)
from gui._poll_mixin import _PollMixin
from gui._tray_mixin import _TrayMixin, TRAY_AVAILABLE
from gui.setup_dialog import SetupDialog
from gui.graph_panel import GraphPanel
from version import __version__


class MainWindow(_PollMixin, _TrayMixin):
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(f"Internet Uptime Monitor - by Curt Bates - v{__version__}")
        self.root.geometry("1050x720")
        self.root.minsize(800, 580)

        # Intercept the window-close (X) button so we can send the app to the
        # tray instead of quitting immediately.
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        initialize_db()             # create tables on first run; no-op thereafter
        self.config = load_config() # read config.json, fall back to defaults if missing

        self._monitoring = False    # True while poll cycles are scheduled
        self._poll_id = None        # tkinter after() handle, kept so we can cancel it

        # Thread-safe bridge: the poll worker thread puts results here; the main
        # thread drains it every 200 ms via root.after(). This is necessary because
        # tkinter widgets must only be touched from the thread that created them.
        self._queue: queue.Queue = queue.Queue()

        self._last_ip:    str | None   = None  # last seen IPv4
        self._last_ipv6:  str | None   = None  # last seen IPv6
        self._last_isp:   str | None   = None  # last seen ISP name
        self._last_score: float | None = None  # most recent summary score (0–100)

        # Tray state — both set together to prevent re-entrancy (see _on_unmap).
        self._tray: "pystray.Icon | None" = None
        self._in_tray = False   # True while the window is hidden and tray is shown

        self._build_ui()
        self._queue_check()         # start the 200 ms queue-drain loop
        self._schedule_daily_purge()

        # Bind minimize events only when the tray feature is available.
        # <Unmap> fires when the window is iconified (minimized) on all platforms.
        if TRAY_AVAILABLE:
            self.root.bind("<Unmap>", self._on_unmap)

        # Pre-populate the status bar with the last IP seen in a previous session
        # so the user isn't staring at dashes until the first poll finishes.
        latest = get_latest_ip()
        if latest:
            self._last_ip   = latest["public_ip"]
            self._last_ipv6 = latest.get("public_ipv6")
            self._last_isp  = latest.get("isp_name")
            self._set_ip(
                latest["public_ip"],
                latest.get("isp_name") or "Unknown",
                latest.get("public_ipv6"),
            )

        self._start()

    # ------------------------------------------------------------------ UI build

    def _build_ui(self) -> None:
        # Build order matters for tkinter's pack geometry manager: widgets packed
        # first claim space before later ones, so we build fixed-height regions
        # (menu, status bar, bottom bar) first, then fill the remainder with the
        # expandable content area.
        self._build_menu()
        self._build_status_bar()
        self._build_bottom_bar()
        self._build_content()

    def _build_menu(self) -> None:
        mb = tk.Menu(self.root)
        self.root.config(menu=mb)

        file_m = tk.Menu(mb, tearoff=0)    # tearoff=0 disables the dashed tear-off line
        mb.add_cascade(label="File", menu=file_m)
        file_m.add_command(label="Export to Log File…", command=self._export_log)
        file_m.add_separator()
        # File > Exit calls _quit (full shutdown), not _on_close (which goes to tray).
        file_m.add_command(label="Exit", command=self._quit)

        setup_m = tk.Menu(mb, tearoff=0)
        mb.add_cascade(label="Setup", menu=setup_m)
        setup_m.add_command(label="Configure…", command=self._open_setup)

        help_m = tk.Menu(mb, tearoff=0)
        mb.add_cascade(label="Help", menu=help_m)
        help_m.add_command(label="About", command=self._show_about)

    def _build_status_bar(self) -> None:
        # Sunken relief gives the bar a subtle inset appearance common in status bars.
        bar = ttk.Frame(self.root, relief=tk.SUNKEN, padding="4 3")
        bar.pack(side=tk.TOP, fill=tk.X)

        # Each piece of information is separated by a vertical line for readability.
        self._ip_lbl = ttk.Label(bar, text="IPv4: —")
        self._ip_lbl.pack(side=tk.LEFT, padx=6)
        ttk.Separator(bar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=4)

        self._ipv6_lbl = ttk.Label(bar, text="IPv6: —")
        self._ipv6_lbl.pack(side=tk.LEFT, padx=6)
        ttk.Separator(bar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=4)

        self._isp_lbl = ttk.Label(bar, text="ISP: —")
        self._isp_lbl.pack(side=tk.LEFT, padx=6)
        ttk.Separator(bar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=4)

        # Colour-coded bullet indicates monitoring state at a glance.
        self._status_lbl = ttk.Label(bar, text="● Stopped", foreground="#c0392b")
        self._status_lbl.pack(side=tk.LEFT, padx=6)
        ttk.Separator(bar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=4)

        self._last_lbl = ttk.Label(bar, text="Last check: never")
        self._last_lbl.pack(side=tk.LEFT, padx=6)

        # Next-check time is right-aligned so it doesn't shift other labels when
        # the timestamp text changes width.
        self._next_lbl = ttk.Label(bar, text="")
        self._next_lbl.pack(side=tk.RIGHT, padx=6)

    def _build_bottom_bar(self) -> None:
        # Pack to BOTTOM before the content frame so tkinter reserves this space
        # first; the content frame then fills everything in between.
        bar = ttk.Frame(self.root, padding="6 4")
        bar.pack(side=tk.BOTTOM, fill=tk.X)

        self._toggle_btn = ttk.Button(
            bar, text="▶  Start Monitoring", command=self._toggle
        )
        self._toggle_btn.pack(side=tk.LEFT, padx=4)

        # Shows the configured interval so the user knows how often polls run
        # without opening the Setup dialog.
        self._interval_lbl = ttk.Label(bar, text="", foreground="gray")
        self._interval_lbl.pack(side=tk.LEFT, padx=12)
        self._refresh_interval_label()

    def _build_content(self) -> None:
        content = ttk.Frame(self.root)
        content.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

        # Pack the log frame to BOTTOM *before* the graph panel. tkinter's pack
        # manager works by claiming space from the edges inward, so packing the
        # log to the bottom first causes it to anchor there while the graph
        # panel gets all remaining space in the middle.
        log_lf = ttk.LabelFrame(content, text="Event Log", padding="4 2")
        log_lf.pack(side=tk.BOTTOM, fill=tk.X, pady=(4, 0))

        # state=DISABLED prevents the user from typing in the log; we re-enable
        # it briefly only while inserting programmatic text (see _log_event).
        self._log = tk.Text(
            log_lf, height=6, state=tk.DISABLED, wrap=tk.WORD,
            font=("Courier New", 9),
        )

        # Named tags colour-code log lines without managing state manually.
        self._log.tag_config("ok",   foreground="#27ae60")  # green — all lookups passed
        self._log.tag_config("fail", foreground="#c0392b")  # red   — all lookups failed
        self._log.tag_config("info", foreground="#2980b9")  # blue  — informational

        sb = ttk.Scrollbar(log_lf, orient=tk.VERTICAL, command=self._log.yview)
        self._log.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self._log.pack(fill=tk.X, expand=True)

        # Graph panel takes the remaining space above the log.
        self._graph = GraphPanel(content)
        self._graph.frame.pack(fill=tk.BOTH, expand=True)

    # ------------------------------------------------------------------ logging

    def _log_event(self, msg: str, tag: str = "") -> None:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # Must briefly enable the widget to insert text, then disable again to
        # keep it read-only for the user.
        self._log.configure(state=tk.NORMAL)
        self._log.insert(tk.END, f"[{ts}] {msg}\n", tag)
        self._log.see(tk.END)           # auto-scroll to the newest entry
        self._log.configure(state=tk.DISABLED)
        if self.config.get("save_event_log", False):
            try:
                with open(APP_DIR / "event.log", "a", encoding="utf-8") as fh:
                    fh.write(f"[{ts}] {msg}\n")
            except OSError:
                pass

    # ------------------------------------------------------------------ control

    def _toggle(self) -> None:
        if self._monitoring:
            self._stop()
        else:
            self._start()

    def _start(self) -> None:
        self._monitoring = True
        self._toggle_btn.configure(text="■  Stop Monitoring")
        self._status_lbl.configure(text="● Monitoring", foreground="#27ae60")
        self._log_event("Monitoring started.", "info")
        self._update_tray()         # refresh tray icon colour and tooltip
        self._schedule_poll(0)      # delay=0 means run the first poll immediately

    def _stop(self) -> None:
        self._monitoring = False
        if self._poll_id:
            # Cancel any pending after() call so we don't fire one more poll
            # after the user clicked Stop.
            self.root.after_cancel(self._poll_id)
            self._poll_id = None
        self._toggle_btn.configure(text="▶  Start Monitoring")
        self._status_lbl.configure(text="● Stopped", foreground="#c0392b")
        self._next_lbl.configure(text="")
        self._log_event("Monitoring stopped.", "info")
        self._update_tray()

    # ------------------------------------------------------------------ helpers

    def _export_log(self) -> None:
        default_name = f"uptime_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        path = filedialog.asksaveasfilename(
            parent=self.root,
            title="Export to Log File",
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            initialfile=default_name,
        )
        if not path:
            return

        try:
            ip_rows      = get_ip_log()       # sorted ascending by timestamp
            dns_rows     = get_dns_results(0) # 0 = all records
            fail_rows    = get_ip_failures()  # IP check failure events
            dns_failures = [r for r in dns_rows if not r["success"]]

            # Build a lookup that maps each DNS result to the IP/ISP that was
            # active at that moment. ip_log is already sorted ascending, so we
            # walk it once and for each DNS row find the last transition whose
            # timestamp is <= the row's timestamp.
            transitions = [
                (
                    r["timestamp"],
                    r["public_ip"],
                    r.get("public_ipv6") or "—",
                    r.get("isp_name") or r.get("org") or "Unknown",
                )
                for r in ip_rows
            ]

            def active_ip_at(ts: float) -> tuple[str, str, str]:
                ipv4, ipv6, isp = "—", "—", "—"
                for t, v4, v6, isp_name in transitions:
                    if t <= ts:
                        ipv4, ipv6, isp = v4, v6, isp_name
                    else:
                        break
                return ipv4, ipv6, isp

            with open(path, "w", encoding="utf-8") as f:
                f.write("Internet Uptime Monitor — Export\n")
                f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write("=" * 72 + "\n\n")

                f.write("DNS FAILURES\n")
                f.write("-" * 72 + "\n")
                f.write(
                    f"{'Timestamp':<22}  {'Provider':<10}  {'Domain':<22}  "
                    f"{'IPv4':<17}  {'IPv6':<39}  {'ISP'}\n"
                )
                f.write(
                    f"{'─'*22}  {'─'*10}  {'─'*22}  "
                    f"{'─'*17}  {'─'*39}  {'─'*28}\n"
                )
                for r in dns_failures:
                    ts_str          = datetime.fromtimestamp(
                        r["timestamp"]
                    ).strftime("%Y-%m-%d %H:%M:%S")
                    ipv4, ipv6, isp = active_ip_at(r["timestamp"])
                    f.write(
                        f"{ts_str:<22}  {r['dns_provider']:<10}  {r['domain']:<22}  "
                        f"{ipv4:<17}  {ipv6:<39}  {isp}\n"
                    )
                if not dns_failures:
                    f.write("  (no records)\n")

                f.write("\n\nIP CHECK FAILURES\n")
                f.write("-" * 72 + "\n")
                f.write(f"{'Timestamp':<22}\n")
                f.write(f"{'─'*22}\n")
                for r in fail_rows:
                    ts_str = datetime.fromtimestamp(
                        r["timestamp"]
                    ).strftime("%Y-%m-%d %H:%M:%S")
                    f.write(f"{ts_str}\n")
                if not fail_rows:
                    f.write("  (no records)\n")

                f.write("\n\nDNS RESULTS\n")
                f.write("-" * 72 + "\n")
                f.write(
                    f"{'Timestamp':<22}  {'Provider':<10}  {'Domain':<22}  "
                    f"{'RT (ms)':>8}  {'OK?':<3}  {'IPv4':<17}  {'IPv6':<39}  {'ISP'}\n"
                )
                f.write(
                    f"{'─'*22}  {'─'*10}  {'─'*22}  "
                    f"{'─'*8}  {'─'*3}  {'─'*17}  {'─'*39}  {'─'*28}\n"
                )
                for r in dns_rows:
                    ts_str          = datetime.fromtimestamp(
                        r["timestamp"]
                    ).strftime("%Y-%m-%d %H:%M:%S")
                    rt              = (
                        f"{r['response_time_ms']:.1f}"
                        if r["response_time_ms"] is not None else "—"
                    )
                    ok              = "yes" if r["success"] else "no"
                    ipv4, ipv6, isp = active_ip_at(r["timestamp"])
                    f.write(
                        f"{ts_str:<22}  {r['dns_provider']:<10}  {r['domain']:<22}  "
                        f"{rt:>8}  {ok:<3}  {ipv4:<17}  {ipv6:<39}  {isp}\n"
                    )
                if not dns_rows:
                    f.write("  (no records)\n")

            self._log_event(f"Exported log to {path}", "info")
        except Exception as exc:
            tk.messagebox.showerror("Export Failed", str(exc), parent=self.root)

    def _set_ip(self, ip: str, isp: str, ipv6: str | None = None) -> None:
        self._ip_lbl.configure(text=f"IPv4: {ip}")
        self._ipv6_lbl.configure(text=f"IPv6: {ipv6}" if ipv6 else "IPv6: —")
        self._isp_lbl.configure(text=f"ISP: {isp}")

    def _refresh_interval_label(self) -> None:
        secs = self.config.get("polling_interval_seconds", 60)
        self._interval_lbl.configure(text=f"Interval: {secs} s")

    def _open_setup(self) -> None:
        dlg = SetupDialog(self.root, self.config)
        # wait_window blocks (while still running the event loop) until the
        # dialog is closed, then we check whether the user clicked Save.
        self.root.wait_window(dlg.top)
        if dlg.result:
            self.config = dlg.result   # replace the live config with the new values
            save_config(self.config)
            if not self.config.get("save_event_log", False):
                log_path = APP_DIR / "event.log"
                if log_path.exists():
                    log_path.unlink()
            self._refresh_interval_label()
            self._log_event("Configuration updated.", "info")
            self._graph.refresh()      # re-draw in case display options changed

    def _show_about(self) -> None:
        dlg = tk.Toplevel(self.root)
        dlg.title("About")
        dlg.resizable(False, False)
        dlg.grab_set()

        msg = (
            f"Internet Uptime Monitor\n"
            f"by Curt Bates\n"
            f"Version {__version__}\n\n"
            "Monitors DNS response times across multiple providers and domains.\n"
            "Tracks public IP and ISP changes.\n\n"
            "Data is stored locally in uptime_monitor.db.\n\n"
            "Minimizing or closing sends the app to the system tray.\n"
            "Right-click the tray icon to restore or exit."
        )
        tk.Label(dlg, text=msg, justify="left", padx=20, pady=16).pack()
        tk.Button(dlg, text="OK", width=10, command=dlg.destroy).pack(pady=(0, 14))

        dlg.update_idletasks()
        w, h = dlg.winfo_reqwidth(), dlg.winfo_reqheight()
        x = self.root.winfo_x() + (self.root.winfo_width() - w) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - h) // 2
        dlg.geometry(f"{w}x{h}+{x}+{y}")

    # ------------------------------------------------------------------ daily purge

    def _schedule_daily_purge(self) -> None:
        # Runs immediately on first call (covers startup), then re-schedules
        # itself every 24 hours so long-running sessions stay trimmed.
        purge_old_records()
        self.root.after(24 * 60 * 60 * 1000, self._schedule_daily_purge)

    # ------------------------------------------------------------------ close / quit

    def _on_close(self) -> None:
        # The window X button sends the app to the tray (if available) instead
        # of quitting, so monitoring continues in the background. The user must
        # explicitly choose Exit from the tray menu or File menu to fully quit.
        if TRAY_AVAILABLE:
            self._hide_to_tray()
        else:
            self._quit()

    def _quit(self) -> None:
        # Full shutdown: stop polling, kill the tray icon, and destroy the window.
        self._stop()
        if self._tray:
            self._tray.stop()
            self._tray = None
        self.root.destroy()
