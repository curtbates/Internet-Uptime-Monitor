import time
from collections import defaultdict
from datetime import datetime

import matplotlib
import matplotlib.dates as mdates

# Must be set before importing pyplot or the backend-specific modules.
# TkAgg embeds matplotlib figures directly inside tkinter widgets.
matplotlib.use("TkAgg")

import tkinter as tk
from tkinter import ttk
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk

from database import get_dns_results, get_ip_log, get_distinct_isps

# Maps the user-facing dropdown label to how many seconds of history to fetch.
TIME_RANGES = {
    "Last 1h":  3600,
    "Last 6h":  21600,
    "Last 24h": 86400,
    "Last 7d":  604800,
}

# How wide each time bucket is for each range. Wider buckets average out noise;
# narrower buckets show more detail. Chosen so each view produces ~50-100 points.
BUCKET_SECONDS = {
    "Last 1h":  60,     # 1-minute buckets  → up to 60 points
    "Last 6h":  300,    # 5-minute buckets  → up to 72 points
    "Last 24h": 900,    # 15-minute buckets → up to 96 points
    "Last 7d":  3600,   # 1-hour buckets    → up to 168 points
}


class GraphPanel:
    def __init__(self, parent):
        # Expose self.frame so the caller can pack/grid this widget like any
        # other tkinter container.
        self.frame = ttk.Frame(parent)
        self._build_controls()
        self._build_graph()

    def _build_controls(self):
        ctrl = ttk.Frame(self.frame)
        ctrl.pack(fill=tk.X, pady=(4, 2))

        # View selector — radio buttons trigger an immediate refresh so the graph
        # updates the moment the user clicks rather than requiring a separate action.
        ttk.Label(ctrl, text="View:").pack(side=tk.LEFT, padx=(4, 2))
        self.view_var = tk.StringVar(value="provider")  # default view on startup
        for label, val in [
            ("By Provider",   "provider"),
            ("By Domain",     "domain"),
            ("Summary Score", "summary"),
        ]:
            ttk.Radiobutton(
                ctrl, text=label, variable=self.view_var, value=val,
                command=self.refresh,   # redraw immediately on selection
            ).pack(side=tk.LEFT, padx=2)

        ttk.Separator(ctrl, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=8)

        # Time range dropdown — also refreshes immediately on change.
        ttk.Label(ctrl, text="Range:").pack(side=tk.LEFT, padx=(0, 2))
        self.range_var = tk.StringVar(value="Last 1h")
        combo = ttk.Combobox(
            ctrl, textvariable=self.range_var,
            values=list(TIME_RANGES.keys()), state="readonly", width=10,
        )
        combo.pack(side=tk.LEFT, padx=2)
        combo.bind("<<ComboboxSelected>>", lambda _: self.refresh())

        # Manual refresh button in case the user wants to pull in new data without
        # waiting for the next automatic poll.
        ttk.Button(ctrl, text="Refresh", command=self.refresh).pack(side=tk.LEFT, padx=8)

        ttk.Separator(ctrl, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=8)

        ttk.Label(ctrl, text="ISP:").pack(side=tk.LEFT, padx=(0, 2))
        self.isp_var = tk.StringVar(value="All ISPs")
        self._isp_combo = ttk.Combobox(
            ctrl, textvariable=self.isp_var,
            values=["All ISPs"], state="readonly", width=22,
        )
        self._isp_combo.pack(side=tk.LEFT, padx=2)
        self._isp_combo.bind("<<ComboboxSelected>>", lambda _: self.refresh())

    def _build_graph(self):
        # Create a matplotlib Figure and attach it to a tkinter canvas widget.
        self.fig = Figure(figsize=(9, 4), dpi=100)
        self.ax  = self.fig.add_subplot(111)   # single plot filling the figure

        # Extra bottom padding prevents x-axis labels from being clipped when
        # tight_layout() runs after each refresh.
        self.fig.subplots_adjust(bottom=0.18)

        self.canvas = FigureCanvasTkAgg(self.fig, master=self.frame)
        self.canvas.draw()
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        # The navigation toolbar provides pan, zoom, and save-image buttons
        # without any extra code on our part.
        toolbar_frame = ttk.Frame(self.frame)
        toolbar_frame.pack(fill=tk.X)
        NavigationToolbar2Tk(self.canvas, toolbar_frame).update()

    # ------------------------------------------------------------------

    def _update_isp_options(self):
        isps    = get_distinct_isps()
        options = ["All ISPs"] + isps
        current = self.isp_var.get()
        self._isp_combo["values"] = options
        if current not in options:
            self.isp_var.set("All ISPs")

    def _filter_by_isp(self, results, isp_name):
        ip_log = get_ip_log()   # all records, sorted by timestamp ascending
        if not ip_log:
            return results
        transitions = [(e["timestamp"], e["isp_name"]) for e in ip_log]
        filtered = []
        for r in results:
            ts = r["timestamp"]
            active_isp = None
            for t, isp in reversed(transitions):
                if t <= ts:
                    active_isp = isp
                    break
            if active_isp == isp_name:
                filtered.append(r)
        return filtered

    def refresh(self):
        range_label = self.range_var.get()
        # Calculate the Unix timestamp for the start of the requested window.
        since   = time.time() - TIME_RANGES.get(range_label, 3600)
        results = get_dns_results(since)

        self._update_isp_options()

        selected_isp = self.isp_var.get()
        if selected_isp != "All ISPs" and results:
            results = self._filter_by_isp(results, selected_isp)

        self.ax.clear()     # wipe the previous plot before drawing the new one

        if not results:
            # Show a friendly placeholder instead of a blank white box.
            self.ax.text(
                0.5, 0.5, "No data available\n\nStart monitoring to collect data.",
                transform=self.ax.transAxes, ha="center", va="center",
                fontsize=13, color="gray",
            )
            self.canvas.draw()
            return

        view     = self.view_var.get()
        bucket_s = BUCKET_SECONDS.get(range_label, 60)

        if view == "provider":
            self._plot_by_provider(results, bucket_s)
        elif view == "domain":
            self._plot_by_domain(results, bucket_s)
        else:
            self._plot_summary(results, bucket_s)

        self._format_xaxis(range_label)
        self.fig.tight_layout()     # adjust margins so labels aren't clipped
        self.canvas.draw()          # push the updated figure to the screen

    # ------------------------------------------------------------------

    def _bucket(self, results, key_fn, bucket_s):
        """Group successful results into fixed-width time buckets.

        Returns {bucket_start_ts: {key: [response_time_ms, ...]}} where the
        key is determined by key_fn(row). Failed results are excluded because
        they have no response time to average.
        """
        data = defaultdict(lambda: defaultdict(list))
        for r in results:
            # Integer-divide the timestamp by bucket_s then multiply back to snap
            # it to the start of the bucket (floor to the nearest bucket boundary).
            b = int(r["timestamp"] / bucket_s) * bucket_s
            if r["success"] and r["response_time_ms"] is not None:
                data[b][key_fn(r)].append(r["response_time_ms"])
        return data

    def _bucket_success(self, results, bucket_s):
        """Group all results (success and failure) into buckets.

        Returns {bucket_start_ts: [success_count, total_count]}.
        Used by the summary view which needs to know both the success rate
        *and* the response times.
        """
        data = defaultdict(lambda: [0, 0])
        for r in results:
            b = int(r["timestamp"] / bucket_s) * bucket_s
            data[b][1] += 1         # increment total
            if r["success"]:
                data[b][0] += 1     # increment successes
        return data

    def _plot_by_provider(self, results, bucket_s):
        # Group by provider name; each provider gets its own line.
        buckets   = self._bucket(results, lambda r: r["dns_provider"], bucket_s)
        providers = sorted({r["dns_provider"] for r in results})
        all_times = sorted(buckets)     # chronological order for left-to-right plotting

        for provider in providers:
            xs, ys = [], []
            for t in all_times:
                vals = buckets[t].get(provider)
                if vals:
                    # Convert the Unix timestamp to a Python datetime so matplotlib
                    # can format the x-axis as human-readable times.
                    xs.append(datetime.fromtimestamp(t))
                    ys.append(sum(vals) / len(vals))    # average response time for this bucket
            if xs:
                # markersize=3 adds small dots at each data point without overwhelming the line.
                self.ax.plot(xs, ys, marker="o", markersize=3, linewidth=1.5, label=provider)

        self.ax.set_title("DNS Response Time by Provider")
        self.ax.set_ylabel("Avg Response Time (ms)")
        self.ax.legend()
        self.ax.grid(True, alpha=0.3)   # faint grid helps read values without cluttering

    def _plot_by_domain(self, results, bucket_s):
        # Same structure as _plot_by_provider but keyed on domain name instead.
        buckets = self._bucket(results, lambda r: r["domain"], bucket_s)
        domains = sorted({r["domain"] for r in results})
        all_times = sorted(buckets)

        for domain in domains:
            xs, ys = [], []
            for t in all_times:
                vals = buckets[t].get(domain)
                if vals:
                    xs.append(datetime.fromtimestamp(t))
                    ys.append(sum(vals) / len(vals))
            if xs:
                self.ax.plot(xs, ys, marker="o", markersize=3, linewidth=1.5, label=domain)

        self.ax.set_title("DNS Response Time by Domain")
        self.ax.set_ylabel("Avg Response Time (ms)")
        self.ax.legend()
        self.ax.grid(True, alpha=0.3)

    def _plot_summary(self, results, bucket_s):
        # Use a sentinel key "_all" so _bucket() still groups everything into
        # the same dict structure but under a single key instead of per-provider.
        rt_buckets      = self._bucket(results, lambda _: "_all", bucket_s)
        success_buckets = self._bucket_success(results, bucket_s)
        all_times       = sorted(success_buckets)

        xs, scores = [], []
        for t in all_times:
            ok, total   = success_buckets[t]
            success_rate = ok / total if total else 0   # fraction 0.0–1.0

            rt_vals = rt_buckets[t].get("_all", [])
            if rt_vals:
                avg_rt = sum(rt_vals) / len(rt_vals)
                # Linear scale: 20 ms maps to score 1.0 (excellent),
                # 500 ms maps to score 0.0 (worst), clamped outside that range.
                rt_score = max(0.0, min(1.0, 1.0 - (avg_rt - 20) / 480))
            else:
                # No successful lookups in this bucket — worst possible RT score.
                rt_score = 0.0

            # Weighted combination: success rate matters more than raw speed.
            score = (success_rate * 0.7 + rt_score * 0.3) * 100
            xs.append(datetime.fromtimestamp(t))
            scores.append(score)

        if xs:
            # Shaded area under the line makes it easier to see dips at a glance.
            self.ax.fill_between(xs, scores, alpha=0.25, color="steelblue")
            self.ax.plot(xs, scores, color="steelblue", linewidth=2, label="Score")
            # Reference lines give context: above 80 is good, below 50 is poor.
            self.ax.axhline(80, color="orange", linestyle="--", alpha=0.7, label="Good (80)")
            self.ax.axhline(50, color="red",    linestyle="--", alpha=0.7, label="Poor (50)")

        self.ax.set_title("Summary Score  (70 % success rate + 30 % response time)")
        self.ax.set_ylabel("Score (0 – 100)")
        self.ax.set_ylim(0, 105)    # headroom above 100 so the line isn't clipped
        self.ax.legend()
        self.ax.grid(True, alpha=0.3)

    def _format_xaxis(self, range_label):
        # AutoDateLocator picks sensible tick intervals for the selected time span.
        # ConciseDateFormatter uses the shortest unambiguous label for each tick
        # (e.g. just "14:30" within a single day, "Jun 5" across multiple days).
        locator   = mdates.AutoDateLocator()
        formatter = mdates.ConciseDateFormatter(locator)
        self.ax.xaxis.set_major_locator(locator)
        self.ax.xaxis.set_major_formatter(formatter)
        self.fig.autofmt_xdate(rotation=30)     # tilt labels to avoid overlap
