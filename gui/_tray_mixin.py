"""Tray icon behaviour mixed into MainWindow.

All methods here assume the following attributes exist on self (set by
MainWindow.__init__):
    root, _monitoring, _last_score, _in_tray, _tray, _last_lbl
And these methods:
    _toggle(), _quit()
"""
import threading

# pystray and Pillow are optional — the app runs fine without them, just without
# the system-tray feature. Wrapping the import in try/except lets us degrade
# gracefully instead of refusing to start.
try:
    import pystray
    from PIL import Image, ImageDraw
    TRAY_AVAILABLE = True
except ImportError:
    TRAY_AVAILABLE = False


class _TrayMixin:
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

    def _on_unmap(self, event) -> None:
        # <Unmap> fires whenever the window is hidden, including when we call
        # root.withdraw() ourselves. The _in_tray guard prevents that from
        # triggering a second call to _hide_to_tray.
        if self._in_tray or event.widget is not self.root:
            return
        # A short delay lets the OS finish the iconify animation before we
        # withdraw the window, which avoids a visual glitch on some platforms.
        self.root.after(150, self._hide_to_tray)

    def _hide_to_tray(self) -> None:
        if self._in_tray:   # re-entrancy guard (see _on_unmap above)
            return
        self._in_tray = True
        self.root.withdraw()    # hide window and remove it from the taskbar
        self._tray = self._make_tray_icon()
        # pystray's run() blocks until icon.stop() is called, so it must live
        # in its own thread. daemon=True ensures it won't keep the process alive.
        threading.Thread(target=self._tray.run, daemon=True).start()

    def _tray_restore(self, icon=None, item=None) -> None:
        # This is called from the pystray thread, so we cannot directly call
        # tkinter methods. root.after(0, ...) queues the call to run on the
        # main thread at the next available opportunity.
        self.root.after(0, self._restore_from_tray)

    def _restore_from_tray(self) -> None:
        # Stop the pystray event loop and destroy the icon before showing the
        # window again, so we don't end up with a ghost icon in the tray.
        if self._tray:
            self._tray.stop()
            self._tray = None
        self._in_tray = False
        self.root.deiconify()       # make the window visible again
        self.root.lift()            # raise it above other windows
        self.root.focus_force()     # give it keyboard focus

    def _update_tray(self) -> None:
        # Called after every poll and every start/stop so the tray icon colour
        # and tooltip always reflect the current state, even when the window
        # is hidden.
        if not self._tray:
            return
        self._tray.icon  = self._make_icon_image()
        status = "Monitoring" if self._monitoring else "Stopped"
        last   = self._last_lbl.cget("text")   # read the status bar label text
        self._tray.title = f"Internet Uptime Monitor  [{status}]  {last}"

    def _tray_quit(self, icon=None, item=None) -> None:
        # Called from the pystray thread — marshal to main thread before
        # touching any tkinter state.
        self.root.after(0, self._quit)
