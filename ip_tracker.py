import socket
import requests
from requests.adapters import HTTPAdapter

# Two free IP-info services tried in order. If the first is unreachable or
# returns unexpected data, the loop falls through to the second automatically.
_SERVICES = [
    "https://ipinfo.io/json",   # primary: returns {"ip", "org", "city", ...}
    "https://ipapi.co/json/",   # fallback: returns {"ip", "org", "isp", ...}
]

# IPv6-only endpoint — only reachable over IPv6. A connection failure means
# the host has no IPv6 connectivity, which is a normal condition.
_IPV6_SERVICE = "https://api6.ipify.org?format=json"


class _ForceIPv4Adapter(HTTPAdapter):
    # On dual-stack machines requests may connect to ipinfo.io over IPv6, which
    # causes the service to return the machine's IPv6 address as the "public IP"
    # instead of the IPv4 address. Overriding allowed_gai_family() for the
    # duration of the request forces the resolver to only return AF_INET results.
    def send(self, *args, **kwargs):
        import urllib3.util.connection as _conn
        _orig = _conn.allowed_gai_family
        _conn.allowed_gai_family = lambda: socket.AF_INET
        try:
            return super().send(*args, **kwargs)
        finally:
            _conn.allowed_gai_family = _orig


# Module-level session so the adapter and connection pool are reused across
# calls rather than rebuilt on every poll.
_ipv4_session = requests.Session()
_ipv4_session.mount("https://", _ForceIPv4Adapter())
_ipv4_session.mount("http://",  _ForceIPv4Adapter())


def _fetch_ipv6(timeout=5):
    """Return the public IPv6 address string, or None if the host has no IPv6."""
    try:
        # Plain requests.get (not _ipv4_session) so the OS can use IPv6 to
        # reach api6.ipify.org, which has AAAA records only.
        resp = requests.get(_IPV6_SERVICE, timeout=timeout)
        resp.raise_for_status()
        return resp.json().get("ip") or None
    except Exception:
        return None


def get_public_ip_info(timeout=10):
    """Returns {'ip': str, 'ipv6': str|None, 'isp': str, 'org': str} or None on complete failure."""
    for url in _SERVICES:
        try:
            resp = _ipv4_session.get(url, timeout=timeout)
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

            ipv6 = _fetch_ipv6()
            return {"ip": ip, "ipv6": ipv6, "isp": isp, "org": org}

        except Exception:
            # Network error, timeout, or JSON parse failure — try the next service.
            continue

    # All services failed (e.g. no internet at all). Caller handles None gracefully.
    return None
