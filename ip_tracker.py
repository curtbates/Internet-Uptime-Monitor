import requests

# Two free IP-info services tried in order. If the first is unreachable or
# returns unexpected data, the loop falls through to the second automatically.
_SERVICES = [
    "https://ipinfo.io/json",   # primary: returns {"ip", "org", "city", ...}
    "https://ipapi.co/json/",   # fallback: returns {"ip", "org", "isp", ...}
]


def get_public_ip_info(timeout=10):
    """Returns {'ip': str, 'isp': str, 'org': str} or None on complete failure."""
    for url in _SERVICES:
        try:
            resp = requests.get(url, timeout=timeout)
            resp.raise_for_status()     # treat HTTP 4xx/5xx as failures
            data = resp.json()

            # ipinfo.io uses "ip"; some other services use "query".
            # The "or" chain tries each key in turn and skips this service if
            # neither key yields a non-empty value.
            ip = data.get("ip") or data.get("query")
            if not ip:
                continue    # malformed response — try the next service

            # ipinfo.io puts the ISP in "org"; ipapi.co uses "org" or "isp".
            # The chained "or" handles all three field names.
            org = data.get("org", "") or data.get("isp", "") or ""

            # Both services prefix the ISP name with an ASN token, e.g.:
            #   "AS7922 Comcast Cable Communications"
            # Strip that prefix so the status bar shows just "Comcast Cable
            # Communications" rather than the raw ASN string.
            if org.startswith("AS"):
                parts = org.split(" ", 1)           # split on the first space only
                isp = parts[1] if len(parts) > 1 else org
            else:
                isp = org or "Unknown"

            return {"ip": ip, "isp": isp, "org": org}

        except Exception:
            # Network error, timeout, or JSON parse failure — try the next service.
            continue

    # All services failed (e.g. no internet at all). Caller handles None gracefully.
    return None
