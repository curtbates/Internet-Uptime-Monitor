import tkinter as tk
from tkinter import ttk, messagebox


class SetupDialog:
    """Modal configuration dialog. Inspect self.result after the window closes.

    self.result is None if the user cancelled, or the updated config dict if
    they clicked Save.
    """

    def __init__(self, parent, config):
        self.result = None  # stays None unless the user clicks Save

        # Deep-copy the relevant config sections so edits inside the dialog
        # don't affect the live config until the user explicitly saves.
        self._cfg = {
            "polling_interval_seconds": config.get("polling_interval_seconds", 60),
            "log_dns":                  config.get("log_dns", True),
            "log_only_incomplete_dns":  config.get("log_only_incomplete_dns", True),
            "log_score":                config.get("log_score", True),
            "log_score_below_80_only":  config.get("log_score_below_80_only", False),
            "save_event_log":           config.get("save_event_log", False),
            "log_ip_success":           config.get("log_ip_success", True),
            "log_isp_changes":          config.get("log_isp_changes", True),
            "dns_providers": [p.copy() for p in config.get("dns_providers", [])],
            "domains":       list(config.get("domains", [])),
        }

        self.top = tk.Toplevel(parent)
        self.top.title("Setup – Internet Uptime Monitor")
        self.top.geometry("520x460")
        self.top.resizable(False, False)

        # grab_set() makes this a modal dialog — the user cannot interact with
        # the main window while this dialog is open.
        self.top.grab_set()

        # transient() ties this window's lifetime to the parent: it minimizes
        # with the parent and doesn't appear separately in the taskbar.
        self.top.transient(parent)

        # Notebook gives us the three tabs (Providers, Domains, Settings)
        # without needing separate windows.
        nb = ttk.Notebook(self.top)
        nb.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        prov_frame = ttk.Frame(nb)
        nb.add(prov_frame, text="DNS Providers")
        self._build_providers_tab(prov_frame)

        dom_frame = ttk.Frame(nb)
        nb.add(dom_frame, text="Domains")
        self._build_domains_tab(dom_frame)

        set_frame = ttk.Frame(nb)
        nb.add(set_frame, text="Settings")
        self._build_settings_tab(set_frame)

        log_frame = ttk.Frame(nb)
        nb.add(log_frame, text="Event Log")
        self._build_event_log_tab(log_frame)

        # Save / Cancel buttons live outside the notebook so they're always
        # visible regardless of which tab is active.
        btn_frame = ttk.Frame(self.top)
        btn_frame.pack(fill=tk.X, padx=8, pady=(0, 8))
        ttk.Button(btn_frame, text="Cancel", command=self.top.destroy).pack(side=tk.RIGHT, padx=4)
        ttk.Button(btn_frame, text="Save",   command=self._save).pack(side=tk.RIGHT, padx=4)

    # ------------------------------------------------------------------ Providers tab

    def _build_providers_tab(self, parent):
        list_frame = ttk.Frame(parent)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        # exportselection=False prevents the listbox from clearing its selection
        # when focus moves to another widget (e.g. when the user clicks an entry field).
        self._prov_lb = tk.Listbox(list_frame, height=7, exportselection=False)
        self._prov_lb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._prov_lb.bind("<<ListboxSelect>>", self._on_prov_select)

        sb = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self._prov_lb.yview)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self._prov_lb.configure(yscrollcommand=sb.set)

        # Detail fields below the list let the user edit a selected provider
        # or fill in a new one before clicking Add.
        edit = ttk.LabelFrame(parent, text="Details", padding="8 4")
        edit.pack(fill=tk.X, padx=8, pady=(0, 4))

        ttk.Label(edit, text="Name:").grid(row=0, column=0, sticky=tk.W, padx=4, pady=2)
        self._prov_name = tk.StringVar()
        ttk.Entry(edit, textvariable=self._prov_name, width=24).grid(
            row=0, column=1, padx=4, pady=2, sticky=tk.W)

        ttk.Label(edit, text="Server IP:").grid(row=1, column=0, sticky=tk.W, padx=4, pady=2)
        self._prov_server = tk.StringVar()
        ttk.Entry(edit, textvariable=self._prov_server, width=24).grid(
            row=1, column=1, padx=4, pady=2, sticky=tk.W)

        bf = ttk.Frame(parent)
        bf.pack(fill=tk.X, padx=8, pady=(0, 8))
        ttk.Button(bf, text="Add",    command=self._prov_add).pack(side=tk.LEFT, padx=2)
        ttk.Button(bf, text="Update", command=self._prov_update).pack(side=tk.LEFT, padx=2)
        ttk.Button(bf, text="Remove", command=self._prov_remove).pack(side=tk.LEFT, padx=2)

        self._prov_refresh()    # populate the listbox with the current providers

    def _prov_refresh(self):
        # Rebuild the listbox from scratch. This is simpler than tracking
        # individual inserts/deletes and keeps the display in sync with self._cfg.
        self._prov_lb.delete(0, tk.END)
        for p in self._cfg["dns_providers"]:
            self._prov_lb.insert(tk.END, f"{p['name']}  ({p['server']})")

    def _on_prov_select(self, _event=None):
        # When the user clicks a row, copy its values into the edit fields so
        # they can modify and Update without retyping everything.
        sel = self._prov_lb.curselection()
        if sel:
            p = self._cfg["dns_providers"][sel[0]]
            self._prov_name.set(p["name"])
            self._prov_server.set(p["server"])

    def _prov_add(self):
        name   = self._prov_name.get().strip()
        server = self._prov_server.get().strip()
        if not name or not server:
            messagebox.showwarning("Input Error", "Enter both a name and a server IP.",
                                   parent=self.top)
            return
        self._cfg["dns_providers"].append({"name": name, "server": server})
        self._prov_refresh()

    def _prov_update(self):
        # Overwrite the selected entry in-place with whatever is in the edit fields.
        sel = self._prov_lb.curselection()
        if not sel:
            return
        self._cfg["dns_providers"][sel[0]] = {
            "name":   self._prov_name.get().strip(),
            "server": self._prov_server.get().strip(),
        }
        self._prov_refresh()

    def _prov_remove(self):
        sel = self._prov_lb.curselection()
        if not sel:
            return
        del self._cfg["dns_providers"][sel[0]]
        self._prov_refresh()

    # ------------------------------------------------------------------ Domains tab

    def _build_domains_tab(self, parent):
        list_frame = ttk.Frame(parent)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        self._dom_lb = tk.Listbox(list_frame, height=10, exportselection=False)
        self._dom_lb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        sb = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self._dom_lb.yview)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self._dom_lb.configure(yscrollcommand=sb.set)

        ef = ttk.Frame(parent)
        ef.pack(fill=tk.X, padx=8, pady=(0, 8))
        ttk.Label(ef, text="Domain:").pack(side=tk.LEFT, padx=(0, 4))
        self._dom_entry = tk.StringVar()
        entry = ttk.Entry(ef, textvariable=self._dom_entry, width=32)
        entry.pack(side=tk.LEFT, padx=2)
        # Bind Enter so the user can type a domain and press Enter without
        # needing to click the Add button.
        entry.bind("<Return>", lambda _: self._dom_add())
        ttk.Button(ef, text="Add",    command=self._dom_add).pack(side=tk.LEFT, padx=2)
        ttk.Button(ef, text="Remove", command=self._dom_remove).pack(side=tk.LEFT, padx=2)

        self._dom_refresh()

    def _dom_refresh(self):
        self._dom_lb.delete(0, tk.END)
        for d in self._cfg["domains"]:
            self._dom_lb.insert(tk.END, d)

    def _dom_add(self):
        # Normalise to lowercase so "Google.com" and "google.com" aren't treated
        # as two different domains.
        domain = self._dom_entry.get().strip().lower()
        if not domain:
            return
        # Silently ignore duplicates rather than showing an error, matching the
        # behaviour users expect from a list editor.
        if domain not in self._cfg["domains"]:
            self._cfg["domains"].append(domain)
            self._dom_refresh()
        self._dom_entry.set("")     # clear the entry field ready for the next domain

    def _dom_remove(self):
        sel = self._dom_lb.curselection()
        if not sel:
            return
        del self._cfg["domains"][sel[0]]
        self._dom_refresh()

    # ------------------------------------------------------------------ Settings tab

    def _build_settings_tab(self, parent):
        f = ttk.Frame(parent, padding="24 24")
        f.pack(fill=tk.BOTH, expand=True)

        ttk.Label(f, text="Polling interval (seconds):").grid(
            row=0, column=0, sticky=tk.W, pady=8)

        # Spinbox enforces the numeric range visually; _save() validates it again
        # in case the user types a value directly into the field.
        self._interval_var = tk.StringVar(value=str(self._cfg["polling_interval_seconds"]))
        ttk.Spinbox(f, textvariable=self._interval_var, from_=10, to=3600, width=8).grid(
            row=0, column=1, padx=12, sticky=tk.W)

        ttk.Label(f, text="min 10 s, max 3600 s", foreground="gray").grid(
            row=1, column=1, sticky=tk.W, padx=12)

    # ------------------------------------------------------------------ Event Log tab

    def _build_event_log_tab(self, parent):
        f = ttk.Frame(parent, padding="24 24")
        f.pack(fill=tk.BOTH, expand=True)

        self._log_dns_var = tk.BooleanVar(value=self._cfg["log_dns"])
        ttk.Checkbutton(
            f,
            text="Log DNS responses to event window",
            variable=self._log_dns_var,
            command=self._on_log_dns_toggle,
        ).grid(row=0, column=0, sticky=tk.W, pady=8)

        self._log_only_incomplete_var = tk.BooleanVar(
            value=self._cfg["log_only_incomplete_dns"]
        )
        self._log_only_incomplete_cb = ttk.Checkbutton(
            f,
            text="Only log incomplete DNS responses",
            variable=self._log_only_incomplete_var,
        )
        self._log_only_incomplete_cb.grid(row=1, column=0, sticky=tk.W, pady=4, padx=(24, 0))
        self._on_log_dns_toggle()  # set initial enabled/disabled state

        self._log_ip_success_var = tk.BooleanVar(value=self._cfg["log_ip_success"])
        ttk.Checkbutton(
            f,
            text="Log IP successful detection messages",
            variable=self._log_ip_success_var,
        ).grid(row=2, column=0, sticky=tk.W, pady=8)

        self._log_isp_changes_var = tk.BooleanVar(value=self._cfg["log_isp_changes"])
        ttk.Checkbutton(
            f,
            text="Log ISP changes to event window",
            variable=self._log_isp_changes_var,
        ).grid(row=3, column=0, sticky=tk.W, pady=8)

        self._log_score_var = tk.BooleanVar(value=self._cfg["log_score"])
        ttk.Checkbutton(
            f,
            text="Log score to event window",
            variable=self._log_score_var,
            command=self._on_log_score_toggle,
        ).grid(row=4, column=0, sticky=tk.W, pady=8)

        self._log_score_below_80_var = tk.BooleanVar(value=self._cfg["log_score_below_80_only"])
        self._log_score_below_80_cb = ttk.Checkbutton(
            f,
            text="Only log scores below 80",
            variable=self._log_score_below_80_var,
        )
        self._log_score_below_80_cb.grid(row=5, column=0, sticky=tk.W, pady=4, padx=(24, 0))
        self._on_log_score_toggle()  # set initial enabled/disabled state

        self._save_event_log_var = tk.BooleanVar(value=self._cfg["save_event_log"])
        ttk.Checkbutton(
            f,
            text="Save Event Log",
            variable=self._save_event_log_var,
        ).grid(row=6, column=0, sticky=tk.W, pady=8)

    def _on_log_dns_toggle(self):
        state = tk.NORMAL if self._log_dns_var.get() else tk.DISABLED
        self._log_only_incomplete_cb.configure(state=state)

    def _on_log_score_toggle(self):
        state = tk.NORMAL if self._log_score_var.get() else tk.DISABLED
        self._log_score_below_80_cb.configure(state=state)

    # ------------------------------------------------------------------

    def _save(self):
        # Validate the interval before accepting the save. The Spinbox widget
        # can still receive arbitrary typed input, so we must re-check here.
        try:
            interval = int(self._interval_var.get())
            if not 10 <= interval <= 3600:
                raise ValueError
        except ValueError:
            messagebox.showwarning("Input Error",
                                   "Interval must be between 10 and 3600 seconds.",
                                   parent=self.top)
            return

        # Require at least one provider and one domain so a poll cycle always
        # has something to do.
        if not self._cfg["dns_providers"]:
            messagebox.showwarning("Input Error",
                                   "At least one DNS provider is required.",
                                   parent=self.top)
            return

        if not self._cfg["domains"]:
            messagebox.showwarning("Input Error",
                                   "At least one domain is required.",
                                   parent=self.top)
            return

        self._cfg["polling_interval_seconds"] = interval
        self._cfg["log_dns"]                 = self._log_dns_var.get()
        self._cfg["log_only_incomplete_dns"]  = self._log_only_incomplete_var.get()
        self._cfg["log_score"]               = self._log_score_var.get()
        self._cfg["log_score_below_80_only"] = self._log_score_below_80_var.get()
        self._cfg["save_event_log"]           = self._save_event_log_var.get()
        self._cfg["log_ip_success"]           = self._log_ip_success_var.get()
        self._cfg["log_isp_changes"]          = self._log_isp_changes_var.get()
        self.result = self._cfg    # signal to the caller that the user saved
        self.top.destroy()
