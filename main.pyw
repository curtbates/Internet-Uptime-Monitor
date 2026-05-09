import sys
import tkinter as tk
from tkinter import messagebox


def _check_deps():
    # Verify the three pip-installed packages are present before any GUI code
    # runs. Uses a messagebox rather than print() because .pyw has no console.
    missing = []
    for pkg in ("dns", "requests", "matplotlib"):
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "Missing Dependencies",
            f"Missing packages: {', '.join(missing)}\n\n"
            "Run:  pip install -r requirements.txt"
        )
        sys.exit(1)


def main():
    _check_deps()

    # Import MainWindow only after the dependency check so a missing library
    # doesn't produce a confusing ImportError inside the gui package.
    from gui.main_window import MainWindow

    root = tk.Tk()          # create the single top-level tkinter window
    MainWindow(root)        # hand it to our application class; it owns the window
    root.mainloop()         # block here, running the tkinter event loop until the window closes


if __name__ == "__main__":
    main()
