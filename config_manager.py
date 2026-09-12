import json
from pathlib import Path

# Sensible out-of-the-box settings used when config.json is missing or corrupt.
# Five well-known public DNS resolvers and two stable domains give a reasonable
# baseline without requiring any initial setup from the user.
DEFAULT_CONFIG: dict = {
    "schema_version": 1,
    "polling_interval_seconds": 60,
    "log_dns": True,
    "log_only_incomplete_dns": True,
    "log_score": True,
    "log_score_below_80_only": False,
    "save_event_log": False,
    "log_ip_success": True,
    "log_isp_changes": True,
    "dns_providers": [
        {"name": "Google",     "server": "8.8.8.8"},
        {"name": "Cloudflare", "server": "1.1.1.1"},
        {"name": "OpenDNS",    "server": "208.67.222.222"},
        {"name": "Quad9",      "server": "9.9.9.9"},
        {"name": "Comodo",     "server": "8.26.56.26"},
    ],
    "domains": [
        "google.com", "amazon.com", "cloudflare.com", "microsoft.com", "github.com"
    ],
}

# Store config.json next to this script so the whole project stays self-contained
# in one directory regardless of where the user runs it from.
CONFIG_FILE = Path(__file__).parent / "config.json"


def load_config() -> dict:
    # Only attempt to read the file if it actually exists; skip straight to the
    # default if not, rather than letting open() raise a FileNotFoundError.
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r") as f:
                cfg = json.load(f)
            # Back-fill any keys added in newer versions so old config files
            # continue to work without requiring a manual edit.
            for key, value in DEFAULT_CONFIG.items():
                cfg.setdefault(key, value)
            return cfg
        except (json.JSONDecodeError, IOError):
            # Corrupt or unreadable file — silently fall back to defaults so the
            # app still starts instead of crashing on launch.
            pass
    # copy() prevents callers from mutating the module-level dict
    return DEFAULT_CONFIG.copy()


def save_config(config: dict) -> None:
    # indent=2 keeps the file human-readable so users can edit it by hand if
    # they prefer not to use the Setup dialog.
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)
