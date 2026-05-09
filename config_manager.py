import json
from pathlib import Path

# Sensible out-of-the-box settings used when config.json is missing or corrupt.
# Three well-known public DNS resolvers and two stable domains give a reasonable
# baseline without requiring any initial setup from the user.
DEFAULT_CONFIG = {
    "polling_interval_seconds": 60,
    "dns_providers": [
        {"name": "Google",     "server": "8.8.8.8"},
        {"name": "Cloudflare", "server": "1.1.1.1"},
        {"name": "OpenDNS",    "server": "208.67.222.222"},
    ],
    "domains": ["google.com", "example.com"],
}

# Store config.json next to this script so the whole project stays self-contained
# in one directory regardless of where the user runs it from.
CONFIG_FILE = Path(__file__).parent / "config.json"


def load_config():
    # Only attempt to read the file if it actually exists; skip straight to the
    # default if not, rather than letting open() raise a FileNotFoundError.
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            # Corrupt or unreadable file — silently fall back to defaults so the
            # app still starts instead of crashing on launch.
            pass
    return DEFAULT_CONFIG.copy()   # copy() prevents callers from mutating the module-level dict


def save_config(config):
    # indent=2 keeps the file human-readable so users can edit it by hand if
    # they prefer not to use the Setup dialog.
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)
