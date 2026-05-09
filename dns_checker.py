import time
import dns.resolver
import dns.exception


def check_dns(server_ip, domain, timeout=5.0):
    """Query `domain` against `server_ip`. Returns (success, response_time_ms).

    response_time_ms is None when success is False.
    """
    # configure=False tells dnspython to ignore the system's resolv.conf / registry
    # settings entirely. This is essential: we want to query a *specific* server,
    # not whatever the OS happens to be configured with.
    resolver = dns.resolver.Resolver(configure=False)
    resolver.nameservers = [server_ip]  # talk only to this DNS server
    resolver.port = 53                  # standard DNS port (UDP and TCP)

    # lifetime = total time allowed for all retries combined.
    # timeout  = time allowed for a single UDP attempt.
    # Setting both to the same value disables implicit retries so a dead server
    # fails in exactly `timeout` seconds rather than silently retrying.
    resolver.lifetime = timeout
    resolver.timeout = timeout

    # perf_counter() gives the highest-resolution timer available on the platform,
    # which is important for measuring sub-10 ms DNS responses accurately.
    start = time.perf_counter()
    try:
        # Request an A record (IPv4 address). We don't use the returned addresses —
        # a successful response is enough to confirm the resolver is reachable and
        # the domain exists.
        resolver.resolve(domain, "A")
        elapsed_ms = (time.perf_counter() - start) * 1000
        return True, round(elapsed_ms, 2)
    except Exception:
        # Catch-all covers: dns.exception.Timeout, dns.resolver.NXDOMAIN,
        # dns.resolver.NoAnswer, dns.resolver.NoNameservers, OSError, etc.
        # We don't distinguish between failure modes here — any exception means
        # the lookup failed from the user's perspective.
        return False, None
