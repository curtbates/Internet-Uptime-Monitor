"""Polling loop behaviour mixed into MainWindow.

All methods here assume the following attributes exist on self (set by
MainWindow.__init__):
    root, config, _monitoring, _poll_id, _queue,
    _last_ip, _last_ipv6, _last_isp, _last_score
And these methods:
    _log_event(), _set_ip(), _update_tray()
And these widgets:
    _graph, _last_lbl, _next_lbl
"""
import queue
import threading
import time
from datetime import datetime, timedelta

from database import (
    insert_dns_result, insert_ip_log, insert_ip_failure,
    get_ip_log, get_ip_failures,
)
from dns_checker import check_dns
from ip_tracker import get_public_ip, get_isp_for_ip, _fetch_ipv6
from utils import calculate_score


class _PollMixin:
    # ------------------------------------------------------------------ monitoring

    def _schedule_poll(self, delay_ms: int) -> None:
        # root.after() is the correct way to schedule work in tkinter — it runs
        # the callback on the main thread inside the event loop, avoiding any
        # thread-safety issues.
        self._poll_id = self.root.after(delay_ms, self._fire_poll)

    def _fire_poll(self) -> None:
        if not self._monitoring:
            return
        # Run the actual DNS + IP work in a daemon thread so the GUI stays
        # responsive while potentially slow network calls are in progress.
        # daemon=True means this thread won't keep the process alive if the
        # main window closes.
        threading.Thread(target=self._poll_worker, daemon=True).start()

    def _poll_worker(self) -> None:
        # This runs in a background thread — do NOT touch any tkinter widgets here.
        # All results are sent back via self._queue and handled on the main thread.
        ts  = time.time()    # single timestamp for the whole poll cycle
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

        # Cheap IP check every poll — uses ipify/checkip with no rate limits.
        ip = get_public_ip()

        # Only do the expensive ISP lookup when the IP actually changes. This
        # avoids hammering free-tier ISP services (ipinfo.io, ipapi.co) on
        # every poll, which causes HTTP 429 rate-limit responses and "Unknown".
        if ip and ip != self._last_ip:
            isp, org = get_isp_for_ip(ip)
            ipv6     = _fetch_ipv6()
            ip_info  = {"ip": ip, "ipv6": ipv6, "isp": isp, "org": org}
        elif ip:
            # IP unchanged — reuse last-known ISP; fetch fresh IPv6 quietly.
            ipv6    = _fetch_ipv6()
            ip_info = {
                "ip":   ip,
                "ipv6": ipv6,
                "isp":  self._last_isp or "Unknown",
                "org":  "",
            }
        else:
            ip_info = None

        # Put a single tuple on the queue; _handle() unpacks it on the main thread.
        self._queue.put(("done", ts, dns_results, ip_info))

    def _queue_check(self) -> None:
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

    def _handle(self, item: tuple) -> None:
        kind = item[0]
        if kind != "done":
            return
        _, ts, dns_results, ip_info = item

        # Write every DNS result to the database.
        for r in dns_results:
            insert_dns_result(
                r["timestamp"], r["provider"], r["domain"],
                r["response_time_ms"], r["success"],
            )

        # Log a new ip_log row whenever IPv4 or IPv6 changes. Both are tracked
        # independently so an IPv6 rotation doesn't go unnoticed just because
        # IPv4 stayed the same, and vice-versa.
        if ip_info:
            ip   = ip_info["ip"]
            ipv6 = ip_info.get("ipv6")
            isp  = ip_info["isp"]

            ip_changed = ip != self._last_ip
            # Only treat a None IPv6 result as a change if we previously had a
            # value — a None means the fetch failed transiently, not that the
            # address was lost, so we avoid spurious "lost IPv6" log entries.
            ipv6_changed = ipv6 is not None and ipv6 != self._last_ipv6

            if ip_changed or ipv6_changed:
                insert_ip_log(ts, ip, isp, ip_info.get("org"), ipv6)
                log_ip  = self.config.get("log_ip_success", True)
                log_isp = self.config.get("log_isp_changes", True)

                if ip_changed:
                    isp_also_changed = (
                        self._last_isp is not None and isp != self._last_isp
                    )
                    verb = "changed to" if self._last_ip else "detected as"

                    # Combine IP + ISP change into one line when both config
                    # flags are on — avoids two redundant log entries.
                    if isp_also_changed and log_ip and log_isp:
                        self._log_event(
                            f"Public IP {verb} {ip}  "
                            f"(ISP: {self._last_isp} → {isp})",
                            "info",
                        )
                    else:
                        if isp_also_changed and log_isp:
                            self._log_event(
                                f"ISP changed from {self._last_isp} to {isp}",
                                "info",
                            )
                        if log_ip:
                            self._log_event(
                                f"Public IP {verb} {ip}  ({isp})", "info"
                            )

                    self._last_isp = isp
                    self._last_ip  = ip

                elif ipv6_changed:
                    # IPv6 changed but IPv4 didn't — log current IPv4 for context.
                    if log_ip:
                        self._log_event(f"Public IP: {ip}  ({isp})", "info")

                if ipv6_changed:
                    verb = "changed to" if self._last_ipv6 else "detected as"
                    if log_ip:
                        self._log_event(
                            f"Public IPv6 {verb} {ipv6}  ({isp})", "info"
                        )
                    self._last_ipv6 = ipv6

            self._set_ip(ip, isp, ipv6)
        else:
            # Log once when the lookup first fails, then reset _last_ip so the
            # next successful poll always logs the restored IP — even if it
            # matches the pre-outage address. Without the reset, a brief routing
            # glitch that sets _last_ip back to the primary ISP's address causes
            # the actual failback to go undetected.
            if self._last_ip is not None:
                self._log_event(
                    "Public IP check failed — connection may be down.", "fail"
                )
                insert_ip_failure(ts)
                self._last_ip   = None
                self._last_ipv6 = None

        # Compute and cache the summary score for the tray icon colour.
        ok_count = sum(1 for r in dns_results if r["success"])
        total    = len(dns_results)
        rts      = [r["response_time_ms"] for r in dns_results
                    if r["response_time_ms"] is not None]
        self._last_score = calculate_score(ok_count, total, rts)

        # Summarise the poll results in the event log with colour coding.
        avg   = f", avg {sum(rts)/len(rts):.1f} ms" if rts else ""
        # Green if everything passed, red if everything failed, no tag (default)
        # if it was a partial failure.
        tag   = "ok" if ok_count == total else ("fail" if ok_count == 0 else "")
        if self.config.get("log_dns", True) and (
            not self.config.get("log_only_incomplete_dns", True) or ok_count < total
        ):
            self._log_event(f"DNS poll: {ok_count}/{total} succeeded{avg}", tag)

        if self.config.get("log_score", True) and self._last_score is not None:
            score = self._last_score
            if not self.config.get("log_score_below_80_only", False) or score < 80:
                score_tag = "ok" if score >= 80 else ("fail" if score < 50 else "")
                self._log_event(f"Score: {score:.1f}/100", score_tag)

        self._last_lbl.configure(
            text=f"Last check: {datetime.fromtimestamp(ts).strftime('%H:%M:%S')}"
        )
        self._graph.refresh()   # redraw the graph with the new data point
        self._update_tray()     # keep the tray tooltip's "last check" time current

        # Schedule the next poll only if monitoring is still active — the user
        # may have clicked Stop while this handler was running.
        if self._monitoring:
            interval_ms = int(self.config.get("polling_interval_seconds", 60) * 1000)
            next_t      = datetime.now() + timedelta(milliseconds=interval_ms)
            self._next_lbl.configure(text=f"Next: {next_t.strftime('%H:%M:%S')}")
            self._schedule_poll(interval_ms)
