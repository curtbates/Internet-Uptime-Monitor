import queue
import threading
import time
import tkinter as tk
from datetime import datetime, timedelta
from tkinter import filedialog, ttk

from config_manager import load_config, save_config
from database import initialize_db, purge_old_records, insert_dns_result, insert_ip_log, get_latest_ip, get_dns_results, get_ip_log
from dns_checker import check_dns
from ip_tracker import get_public_ip_info
from gui.setup_dialog import SetupDialog
from gui.graph_panel import GraphPanel

# pystray and Pillow are optional — the app runs fine without them, just without
# the system-tray feature. Wrapping the import in try/except lets us degrade
# gracefully instead of refusing to start.
try:
    import pystray
    from PIL import Image, ImageDraw
    _TRAY_AVAILABLE = True
except ImportError:
    _TRAY_AVAILABLE = False


class MainWindow:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Internet Uptime Monitor - by Curt Bates")
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

        self._last_ip: str | None = None    # tracks last seen IP to detect changes
        self._last_score: float | None = None  # most recent summary score (0–100)

        # Tray state — both set together to prevent re-entrancy (see _on_unmap).
        self._tray: "pystray.Icon | None" = None
        self._in_tray = False   # True while the window is hidden and tray is shown

        self._build_ui()
        self._queue_check()         # start the 200 ms queue-drain loop
        self._schedule_daily_purge()

        # Bind minimize events only when the tray feature is available.
        # <Unmap> fires when the window is iconified (minimized) on all platforms.
        if _TRAY_AVAILABLE:
            self.root.bind("<Unmap>", self._on_unmap)

        # Pre-populate the status bar with the last IP seen in a previous session
        # so the user isn't staring at dashes until the first poll finishes.
        latest = get_latest_ip()
        if latest:
            self._last_ip = latest["public_ip"]
            self._set_ip(latest["public_ip"], latest.get("isp_name") or "Unknown")

        self._start()

    # ------------------------------------------------------------------ UI build

    def _build_ui(self):
        # Build order matters for tkinter's pack geometry manager: widgets packed
        # first claim space before later ones, so we build fixed-height regions
        # (menu, status bar, bottom bar) first, then fill the remainder with the
        # expandable content area.
        self._build_menu()
        self._build_status_bar()
        self._build_bottom_bar()
        self._build_content()

    def _build_menu(self):
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

    def _build_status_bar(self):
        # Sunken relief gives the bar a subtle inset appearance common in status bars.
        bar = ttk.Frame(self.root, relief=tk.SUNKEN, padding="4 3")
        bar.pack(side=tk.TOP, fill=tk.X)

        # Each piece of information is separated by a vertical line for readability.
        self._ip_lbl = ttk.Label(bar, text="IP: —")
        self._ip_lbl.pack(side=tk.LEFT, padx=6)
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

    def _build_bottom_bar(self):
        # Pack to BOTTOM before the content frame so tkinter reserves this space
        # first; the content frame then fills everything in between.
        bar = ttk.Frame(self.root, padding="6 4")
        bar.pack(side=tk.BOTTOM, fill=tk.X)

        self._toggle_btn = ttk.Button(bar, text="▶  Start Monitoring", command=self._toggle)
        self._toggle_btn.pack(side=tk.LEFT, padx=4)

        # Shows the configured interval so the user knows how often polls run
        # without opening the Setup dialog.
        self._interval_lbl = ttk.Label(bar, text="", foreground="gray")
        self._interval_lbl.pack(side=tk.LEFT, padx=12)
        self._refresh_interval_label()

    def _build_content(self):
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
        self._log = tk.Text(log_lf, height=6, state=tk.DISABLED, wrap=tk.WORD,
                            font=("Courier New", 9))

        # Named tags let us colour-code lines without managing colour state ourselves.
        self._log.tag_config("ok",   foreground="#27ae60")  # green  — all lookups passed
        self._log.tag_config("fail", foreground="#c0392b")  # red    — all lookups failed
        self._log.tag_config("info", foreground="#2980b9")  # blue   — informational messages

        sb = ttk.Scrollbar(log_lf, orient=tk.VERTICAL, command=self._log.yview)
        self._log.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self._log.pack(fill=tk.X, expand=True)

        # Graph panel takes the remaining space above the log.
        self._graph = GraphPanel(content)
        self._graph.frame.pack(fill=tk.BOTH, expand=True)

    # ------------------------------------------------------------------ logging

    def _log_event(self, msg: str, tag: str = ""):
        ts = datetime.now().strftime("%H:%M:%S")
        # Must briefly enable the widget to insert text, then disable again to
        # keep it read-only for the user.
        self._log.configure(state=tk.NORMAL)
        self._log.insert(tk.END, f"[{ts}] {msg}\n", tag)
        self._log.see(tk.END)           # auto-scroll to the newest entry
        self._log.configure(state=tk.DISABLED)

    # ------------------------------------------------------------------ monitoring

    def _toggle(self):
        if self._monitoring:
            self._stop()
        else:
            self._start()

    def _start(self):
        self._monitoring = True
        self._toggle_btn.configure(text="■  Stop Monitoring")
        self._status_lbl.configure(text="● Monitoring", foreground="#27ae60")
        self._log_event("Monitoring started.", "info")
        self._update_tray()         # refresh tray icon colour and tooltip
        self._schedule_poll(0)      # delay=0 means run the first poll immediately

    def _stop(self):
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

    def _schedule_poll(self, delay_ms: int):
        # root.after() is the correct way to schedule work in tkinter — it runs
        # the callback on the main thread inside the event loop, avoiding any
        # thread-safety issues.
        self._poll_id = self.root.after(delay_ms, self._fire_poll)

    def _fire_poll(self):
        if not self._monitoring:
            return
        # Run the actual DNS + IP work in a daemon thread so the GUI stays
        # responsive while potentially slow network calls are in progress.
        # daemon=True means this thread won't keep the process alive if the
        # main window closes.
        threading.Thread(target=self._poll_worker, daemon=True).start()

    def _poll_worker(self):
        # This runs in a background thread — do NOT touch any tkinter widgets here.
        # All results are sent back via self._queue and handled on the main thread.
        ts = time.time()    # single timestamp for the whole poll cycle
        cfg = self.config
        dns_results = []

        # Query every provider × domain combination defined in the config.
        for provider in cfg.get("dns_providers", []):
            for domain in cfg.get("domains", []):
                ok, rt = check_dns(provider["server"], domain)
                dns_results.append({
                    "provider": provider["name"],
                    "domain":   domain,
                    "success":  ok,
                    "response_time_ms": rt,
                    "timestamp": ts,
                })

        ip_info = get_public_ip_info()  # may return None if all services are unreachable

        # Put a single tuple on the queue; _handle() unpacks it on the main thread.
        self._queue.put(("done", ts, dns_results, ip_info))

    def _queue_check(self):
        # Drain every available item from the queue in one pass so results
        # aren't stacked up if a poll completes while a previous one is being processed.
        try:
            while True:
                self._handle(self._queue.get_nowait())
        except queue.Empty:
            pass
        # Re-schedule itself every 200 ms. This is fast enough that results feel
        # instant, but slow enough not to burn CPU.
        self.root.after(200, self._queue_check)

    def _handle(self, item):
        kind = item[0]
        if kind != "done":
            return
        _, ts, dns_results, ip_info = item

        # Write every DNS result to the database.
        for r in dns_results:
            insert_dns_result(r["timestamp"], r["provider"], r["domain"],
                              r["response_time_ms"], r["success"])

        # Only log an IP record when the address actually changes. This keeps
        # ip_log as a meaningful change-history rather than a flood of duplicates,
        # making it easy to identify ISP failover events later.
        if ip_info:
            ip  = ip_info["ip"]
            isp = ip_info["isp"]
            if ip != self._last_ip:
                insert_ip_log(ts, ip, isp, ip_info.get("org"))
                # Use different wording for the very first detection vs. a change.
                verb = "changed to" if self._last_ip else "detected as"
                self._log_event(f"Public IP {verb} {ip}  ({isp})", "info")
                self._last_ip = ip
            self._set_ip(ip, isp)

        # Compute summary score (mirrors GraphPanel._plot_summary logic).
        ok_count = sum(1 for r in dns_results if r["success"])
        total    = len(dns_results)
        rts      = [r["response_time_ms"] for r in dns_results if r["response_time_ms"] is not None]
        success_rate = ok_count / total if total else 0
        if rts:
            avg_rt   = sum(rts) / len(rts)
            rt_score = max(0.0, min(1.0, 1.0 - (avg_rt - 20) / 480))
        else:
            rt_score = 0.0
        self._last_score = min(100.0, (success_rate * 0.7 + rt_score * 0.3) * 100)

        # Summarise the poll results in the event log with colour coding.
        ok_count = sum(1 for r in dns_results if r["success"])
        total    = len(dns_results)
        rts      = [r["response_time_ms"] for r in dns_results if r["response_time_ms"] is not None]
        avg      = f", avg {sum(rts)/len(rts):.1f} ms" if rts else ""
        # Green if everything passed, red if everything failed, no tag (default)
        # if it was a partial failure.
        tag = "ok" if ok_count == total else ("fail" if ok_count == 0 else "")
        self._log_event(f"DNS poll: {ok_count}/{total} succeeded{avg}", tag)

        self._last_lbl.configure(
            text=f"Last check: {datetime.fromtimestamp(ts).strftime('%H:%M:%S')}"
        )
        self._graph.refresh()   # redraw the graph with the new data point
        self._update_tray()     # keep the tray tooltip's "last check" time current

        # Schedule the next poll only if monitoring is still active — the user
        # may have clicked Stop while this handler was running.
        if self._monitoring:
            interval_ms = int(self.config.get("polling_interval_seconds", 60) * 1000)
            next_t = datetime.now() + timedelta(milliseconds=interval_ms)
            self._next_lbl.configure(text=f"Next: {next_t.strftime('%H:%M:%S')}")
            self._schedule_poll(interval_ms)

    # ------------------------------------------------------------------ helpers

    def _export_log(self):
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
            ip_rows  = get_ip_log()
            dns_rows = get_dns_results(0)   # 0 = all records

            with open(path, "w", encoding="utf-8") as f:
                f.write("Internet Uptime Monitor — Export\n")
                f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write("=" * 72 + "\n\n")

                f.write("IP / ISP LOG\n")
                f.write("-" * 72 + "\n")
                f.write(f"{'Timestamp':<22}  {'Public IP':<18}  {'ISP'}\n")
                f.write(f"{'─'*22}  {'─'*18}  {'─'*28}\n")
                for r in ip_rows:
                    ts  = datetime.fromtimestamp(r["timestamp"]).strftime("%Y-%m-%d %H:%M:%S")
                    isp = r.get("isp_name") or r.get("org") or "Unknown"
                    f.write(f"{ts:<22}  {r['public_ip']:<18}  {isp}\n")
                if not ip_rows:
                    f.write("  (no records)\n")

                f.write("\n\nDNS RESULTS\n")
                f.write("-" * 72 + "\n")
                f.write(f"{'Timestamp':<22}  {'Provider':<10}  {'Domain':<22}  {'RT (ms)':>8}  {'OK?'}\n")
                f.write(f"{'─'*22}  {'─'*10}  {'─'*22}  {'─'*8}  {'─'*3}\n")
                for r in dns_rows:
                    ts  = datetime.fromtimestamp(r["timestamp"]).strftime("%Y-%m-%d %H:%M:%S")
                    rt  = f"{r['response_time_ms']:.1f}" if r["response_time_ms"] is not None else "—"
                    ok  = "yes" if r["success"] else "no"
                    f.write(f"{ts:<22}  {r['dns_provider']:<10}  {r['domain']:<22}  {rt:>8}  {ok}\n")
                if not dns_rows:
                    f.write("  (no records)\n")

            self._log_event(f"Exported log to {path}", "info")
        except Exception as exc:
            tk.messagebox.showerror("Export Failed", str(exc), parent=self.root)

    def _set_ip(self, ip: str, isp: str):
        self._ip_lbl.configure(text=f"IP: {ip}")
        self._isp_lbl.configure(text=f"ISP: {isp}")

    def _refresh_interval_label(self):
        secs = self.config.get("polling_interval_seconds", 60)
        self._interval_lbl.configure(text=f"Interval: {secs} s")

    def _open_setup(self):
        dlg = SetupDialog(self.root, self.config)
        # wait_window blocks (while still running the event loop) until the
        # dialog is closed, then we check whether the user clicked Save.
        self.root.wait_window(dlg.top)
        if dlg.result:
            self.config = dlg.result   # replace the live config with the new values
            save_config(self.config)
            self._refresh_interval_label()
            self._log_event("Configuration updated.", "info")
            self._graph.refresh()      # re-draw in case display options changed

    def _show_about(self):
        dlg = tk.Toplevel(self.root)
        dlg.title("About")
        dlg.resizable(False, False)
        dlg.grab_set()

        msg = (
            "Internet Uptime Monitor\n"
            "by Curt Bates\n"
            "Version 20260509b\n\n"
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

    # ------------------------------------------------------------------ tray

    def _make_icon_image(self) -> "Image.Image":
        # Draw a simple filled circle as the tray icon. Green = monitoring,
        # red = stopped. The icon is 64×64 RGBA so it works on both Windows
        # (which uses the alpha channel) and Linux.
        size = 64
        img  = Image.new("RGBA", (size, size), (0, 0, 0, 0))  # fully transparent canvas
        draw = ImageDraw.Draw(img)
        if not self._monitoring:
            color = "#2980b9"   # blue — not monitoring
        elif self._last_score is None or self._last_score > 80:
            color = "#27ae60"   # green — good
        elif self._last_score >= 50:
            color = "#f39c12"   # yellow — degraded
        else:
            color = "#c0392b"   # red — poor
        draw.ellipse([4, 4, size - 4, size - 4], fill=color, outline="white", width=3)
        return img

    def _make_tray_icon(self) -> "pystray.Icon":
        # The label for the toggle menu item must reflect the *current* state each
        # time the menu is opened, so we pass a callable instead of a plain string.
        def toggle_label(item):
            return "Stop Monitoring" if self._monitoring else "Start Monitoring"

        menu = pystray.Menu(
            # default=True makes "Show" the action triggered by a double-click.
            pystray.MenuItem("Show", self._tray_restore, default=True),
            pystray.Menu.SEPARATOR,
            # The toggle callback uses root.after(0, ...) to marshal the call back
            # to the main thread — pystray callbacks run in the pystray thread.
            pystray.MenuItem(toggle_label, lambda: self.root.after(0, self._toggle)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Exit", self._tray_quit),
        )
        status = "Monitoring" if self._monitoring else "Stopped"
        return pystray.Icon(
            "uptime_monitor",           # internal name used by the OS
            self._make_icon_image(),    # PIL Image used as the visible icon
            f"Internet Uptime Monitor  [{status}]",  # tooltip text
            menu=menu,
        )

    def _on_unmap(self, event):
        # <Unmap> fires whenever the window is hidden, including when we call
        # root.withdraw() ourselves. The _in_tray guard prevents that from
        # triggering a second call to _hide_to_tray.
        if self._in_tray or event.widget is not self.root:
            return
        # A short delay lets the OS finish the iconify animation before we
        # withdraw the window, which avoids a visual glitch on some platforms.
        self.root.after(150, self._hide_to_tray)

    def _hide_to_tray(self):
        if self._in_tray:   # re-entrancy guard (see _on_unmap above)
            return
        self._in_tray = True
        self.root.withdraw()    # hide window and remove it from the taskbar
        self._tray = self._make_tray_icon()
        # pystray's run() blocks until icon.stop() is called, so it must live
        # in its own thread. daemon=True ensures it won't keep the process alive.
        threading.Thread(target=self._tray.run, daemon=True).start()

    def _tray_restore(self, icon=None, item=None):
        # This is called from the pystray thread, so we cannot directly call
        # tkinter methods. root.after(0, ...) queues the call to run on the
        # main thread at the next available opportunity.
        self.root.after(0, self._restore_from_tray)

    def _restore_from_tray(self):
        # Stop the pystray event loop and destroy the icon before showing the
        # window again, so we don't end up with a ghost icon in the tray.
        if self._tray:
            self._tray.stop()
            self._tray = None
        self._in_tray = False
        self.root.deiconify()       # make the window visible again
        self.root.lift()            # raise it above other windows
        self.root.focus_force()     # give it keyboard focus

    def _update_tray(self):
        # Called after every poll and every start/stop so the tray icon colour
        # and tooltip always reflect the current state, even when the window
        # is hidden.
        if not self._tray:
            return
        self._tray.icon  = self._make_icon_image()
        status = "Monitoring" if self._monitoring else "Stopped"
        last   = self._last_lbl.cget("text")   # read the status bar label text
        self._tray.title = f"Internet Uptime Monitor  [{status}]  {last}"

    def _tray_quit(self, icon=None, item=None):
        # Called from the pystray thread — marshal to main thread before
        # touching any tkinter state.
        self.root.after(0, self._quit)

    # ------------------------------------------------------------------ daily purge

    def _schedule_daily_purge(self):
        # Runs immediately on first call (covers startup), then re-schedules
        # itself every 24 hours so long-running sessions stay trimmed.
        purge_old_records()
        self.root.after(24 * 60 * 60 * 1000, self._schedule_daily_purge)

    # ------------------------------------------------------------------ close / quit

    def _on_close(self):
        # The window X button sends the app to the tray (if available) instead
        # of quitting, so monitoring continues in the background. The user must
        # explicitly choose Exit from the tray menu or File menu to fully quit.
        if _TRAY_AVAILABLE:
            self._hide_to_tray()
        else:
            self._quit()

    def _quit(self):
        # Full shutdown: stop polling, kill the tray icon, and destroy the window.
        self._stop()
        if self._tray:
            self._tray.stop()
            self._tray = None
        self.root.destroy()
