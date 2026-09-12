import re
import socket
import requests
from requests.adapters import HTTPAdapter

# Fast, high-availability services used on every poll just to get the IP address.
# These have no meaningful rate limits and return minimal data quickly.
_IP_SERVICES = [
    "https://api.ipify.org?format=json",  # returns {"ip": "x.x.x.x"}
    "https://checkip.amazonaws.com",      # returns plain-text IP
]

# Services queried only when the IP changes, to look up the ISP.
# ip-api.com allows 45 req/min on the free tier (HTTP only).
# ipinfo.io and ipapi.co are fallbacks but have lower rate limits.
_ISP_SERVICES = [
    "http://ip-api.com/json/{ip}?fields=status,isp,org,query",
    "https://ipinfo.io/json",
    "https://ipapi.co/json/",
]

# IPv6-only endpoint — only reachable over IPv6.
_IPV6_SERVICE = "https://api6.ipify.org?format=json"

# Loose validation: four decimal octets, each 1-3 digits. Guards against
# HTML error pages or unexpected text being stored in the DB as an IP address.
_IPV4_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


def _is_valid_ipv4(addr: str) -> bool:
    return bool(_IPV4_RE.match(addr))


class _ForceIPv4Adapter(HTTPAdapter):
    # On dual-stack machines requests may route to ipinfo.io over IPv6, which
    # causes the service to return the machine's IPv6 address as the "public IP".
    # Forcing AF_INET for the duration of the request avoids this.
    def send(self, *args, **kwargs):
        import urllib3.util.connection as _conn
        _orig = _conn.allowed_gai_family
        _conn.allowed_gai_family = lambda: socket.AF_INET
        try:
            return super().send(*args, **kwargs)
        finally:
            _conn.allowed_gai_family = _orig


# Module-level session so the adapter and connection pool are reused across calls.
_ipv4_session = requests.Session()
_ipv4_session.mount("https://", _ForceIPv4Adapter())
_ipv4_session.mount("http://",  _ForceIPv4Adapter())


def _fetch_ipv6(timeout: int = 5) -> str | None:
    """Return the public IPv6 address string, or None if the host has no IPv6."""
    try:
        resp = requests.get(_IPV6_SERVICE, timeout=timeout)
        resp.raise_for_status()
        return resp.json().get("ip") or None
    except (requests.RequestException, ValueError, KeyError):
        return None


def get_public_ip(timeout: int = 10) -> str | None:
    """Return just the public IPv4 address string, or None on failure.

    Uses lightweight services with no practical rate limits. Called every poll.
    """
    for url in _IP_SERVICES:
        try:
            resp = _ipv4_session.get(url, timeout=timeout)
            resp.raise_for_status()
            try:
                ip = resp.json().get("ip")
            except ValueError:
                ip = resp.text.strip() or None
            if ip and _is_valid_ipv4(ip):
                return ip
        except requests.RequestException:
            continue
    return None


def get_isp_for_ip(ip: str, timeout: int = 10) -> tuple[str, str]:
    """Look up the ISP name and raw org string for a given IP address.

    Returns (isp_name, org_raw). Called only when the IP changes, so rate
    limits on free-tier services are not a concern.
    """
    for url_template in _ISP_SERVICES:
        url = url_template.format(ip=ip)
        try:
            resp = _ipv4_session.get(url, timeout=timeout)
            resp.raise_for_status()
            try:
                data = resp.json()
            except ValueError:
                continue

            # ip-api.com returns {"status": "success", "isp": "...", "org": "..."}
            if data.get("status") == "success":
                isp = data.get("isp") or data.get("org") or ""
                org = data.get("org") or isp
                if isp:
                    return isp, org

            # ipinfo.io / ipapi.co return {"org": "AS1234 ISP Name", "isp": "..."}
            org = data.get("org", "") or data.get("isp", "") or ""
            if org:
                # Strip the leading ASN token (e.g. "AS7922 Comcast…" → "Comcast…")
                if org.startswith("AS"):
                    parts = org.split(" ", 1)
                    isp = parts[1] if len(parts) > 1 else org
                else:
                    isp = org
                return isp, org

        except requests.RequestException:
            continue

    return "Unknown", ""


def get_public_ip_info(timeout: int = 10) -> dict | None:
    """Returns {'ip': str, 'ipv6': str|None, 'isp': str, 'org': str} or None.

    Combines a fast IP fetch with a full ISP lookup. Use this on the first
    poll or after detecting an IP change; use get_public_ip() for routine
    polls where only the address is needed.
    """
    ip = get_public_ip(timeout=timeout)
    if not ip:
        return None
    isp, org  = get_isp_for_ip(ip, timeout=timeout)
    ipv6      = _fetch_ipv6()
    return {"ip": ip, "ipv6": ipv6, "isp": isp, "org": org}
